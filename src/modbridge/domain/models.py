"""Core domain models: mod manifests, change sets, and the diffing algorithm.

The filesystem manifest is ModBridge's ground truth for "did anything change".
Upstream tool logs are only used to *enrich* these diffs, never to produce them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self


class ChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    UPDATED = "updated"


@dataclass(frozen=True, slots=True)
class ModInfo:
    """A single mod jar as observed on disk."""

    filename: str
    sha256: str
    mod_id: str | None = None
    name: str | None = None
    version: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or self.mod_id or self.filename

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "mod_id": self.mod_id,
            "name": self.name,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            filename=data["filename"],
            sha256=data["sha256"],
            mod_id=data.get("mod_id"),
            name=data.get("name"),
            version=data.get("version"),
        )


@dataclass(frozen=True)
class ModsManifest:
    """Snapshot of the mods directory at a point in time."""

    mods: tuple[ModInfo, ...] = ()

    def content_hash(self) -> str:
        """Stable hash of the manifest contents; equal hashes mean identical mod sets."""
        payload = sorted((m.filename, m.sha256) for m in self.mods)
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()

    def by_mod_id(self) -> dict[str, ModInfo]:
        return {m.mod_id: m for m in self.mods if m.mod_id}

    def by_filename(self) -> dict[str, ModInfo]:
        return {m.filename: m for m in self.mods}

    def to_dict(self) -> dict[str, Any]:
        return {"mods": [m.to_dict() for m in self.mods]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(mods=tuple(ModInfo.from_dict(m) for m in data.get("mods", [])))


@dataclass(frozen=True, slots=True)
class ModChange:
    kind: ChangeKind
    display_name: str
    filename: str
    mod_id: str | None = None
    old_version: str | None = None
    new_version: str | None = None
    changelog: str | None = None  # optional per-mod changelog (Modrinth enrichment)


@dataclass(frozen=True)
class ChangeSet:
    """Everything that changed between two manifests, plus optional MC/loader changes."""

    changes: tuple[ModChange, ...] = ()
    minecraft_old: str | None = None
    minecraft_new: str | None = None
    extra_notes: tuple[str, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        return not self.changes and self.minecraft_old == self.minecraft_new

    def of_kind(self, kind: ChangeKind) -> tuple[ModChange, ...]:
        return tuple(c for c in self.changes if c.kind == kind)

    @property
    def added(self) -> tuple[ModChange, ...]:
        return self.of_kind(ChangeKind.ADDED)

    @property
    def removed(self) -> tuple[ModChange, ...]:
        return self.of_kind(ChangeKind.REMOVED)

    @property
    def updated(self) -> tuple[ModChange, ...]:
        return self.of_kind(ChangeKind.UPDATED)

    def summary(self) -> str:
        parts: list[str] = []
        if self.minecraft_old != self.minecraft_new and self.minecraft_new:
            parts.append(f"Minecraft {self.minecraft_old or '?'} -> {self.minecraft_new}")
        counts = [
            (len(self.updated), "updated"),
            (len(self.added), "added"),
            (len(self.removed), "removed"),
        ]
        parts.extend(f"{n} {label}" for n, label in counts if n)
        return ", ".join(parts) if parts else "no changes"


def diff_manifests(old: ModsManifest, new: ModsManifest) -> ChangeSet:
    """Diff two manifests into a ChangeSet.

    Matching strategy, in order of reliability:
    1. mod_id (survives file renames, the common case for updates)
    2. filename (for jars whose metadata could not be read)
    Anything unmatched in `old` is removed; unmatched in `new` is added.
    """
    old_by_id = old.by_mod_id()
    new_by_id = new.by_mod_id()

    changes: list[ModChange] = []
    matched_old: set[str] = set()
    matched_new: set[str] = set()

    # Pass 1: match by mod_id.
    for mod_id, new_mod in new_by_id.items():
        old_mod = old_by_id.get(mod_id)
        if old_mod is None:
            continue
        matched_old.add(old_mod.filename)
        matched_new.add(new_mod.filename)
        if old_mod.sha256 != new_mod.sha256:
            changes.append(
                ModChange(
                    kind=ChangeKind.UPDATED,
                    display_name=new_mod.display_name,
                    filename=new_mod.filename,
                    mod_id=mod_id,
                    old_version=old_mod.version,
                    new_version=new_mod.version,
                )
            )

    # Pass 2: match remaining files by filename (metadata-less jars).
    old_rest = {m.filename: m for m in old.mods if m.filename not in matched_old}
    new_rest = {m.filename: m for m in new.mods if m.filename not in matched_new}
    for filename, new_mod in list(new_rest.items()):
        old_mod = old_rest.get(filename)
        if old_mod is None:
            continue
        del old_rest[filename]
        del new_rest[filename]
        if old_mod.sha256 != new_mod.sha256:
            changes.append(
                ModChange(
                    kind=ChangeKind.UPDATED,
                    display_name=new_mod.display_name,
                    filename=filename,
                    mod_id=new_mod.mod_id,
                    old_version=old_mod.version,
                    new_version=new_mod.version,
                )
            )

    changes.extend(
        ModChange(
            kind=ChangeKind.REMOVED,
            display_name=m.display_name,
            filename=m.filename,
            mod_id=m.mod_id,
            old_version=m.version,
        )
        for m in old_rest.values()
    )
    changes.extend(
        ModChange(
            kind=ChangeKind.ADDED,
            display_name=m.display_name,
            filename=m.filename,
            mod_id=m.mod_id,
            new_version=m.version,
        )
        for m in new_rest.values()
    )

    changes.sort(key=lambda c: (c.kind, c.display_name.lower()))
    return ChangeSet(changes=tuple(changes))

"""Scan a mods directory into a ModsManifest, extracting metadata from jar internals.

Supported metadata sources (in order): NeoForge/Forge mods.toml, Fabric fabric.mod.json,
Quilt quilt.mod.json. Unreadable jars still enter the manifest (hash + filename only),
so diffing never misses a change just because metadata parsing failed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tomllib
import zipfile
from pathlib import Path

from modbridge.domain.models import ModInfo, ModsManifest

log = logging.getLogger(__name__)

_TOML_CANDIDATES = ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")
_IMPL_VERSION_RE = re.compile(r"^Implementation-Version:\s*(.+)$", re.MULTILINE)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_impl_version(zf: zipfile.ZipFile) -> str | None:
    try:
        text = zf.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")
    except KeyError:
        return None
    match = _IMPL_VERSION_RE.search(text)
    return match.group(1).strip() if match else None


def _read_toml_metadata(zf: zipfile.ZipFile) -> tuple[str | None, str | None, str | None]:
    for candidate in _TOML_CANDIDATES:
        try:
            raw = zf.read(candidate)
        except KeyError:
            continue
        data = tomllib.loads(raw.decode("utf-8", errors="replace"))
        mods = data.get("mods")
        if not isinstance(mods, list) or not mods:
            continue
        entry = mods[0]
        mod_id = entry.get("modId")
        name = entry.get("displayName")
        version = entry.get("version")
        # Gradle placeholder: real version lives in the jar manifest.
        if isinstance(version, str) and version.startswith("${"):
            version = _manifest_impl_version(zf)
        return (
            mod_id if isinstance(mod_id, str) else None,
            name if isinstance(name, str) else None,
            version if isinstance(version, str) else None,
        )
    return None, None, None


def _read_fabric_metadata(
    zf: zipfile.ZipFile, member: str
) -> tuple[str | None, str | None, str | None]:
    try:
        raw = zf.read(member)
    except KeyError:
        return None, None, None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None, None, None
    if member == "quilt.mod.json":
        data = data.get("quilt_loader", {})
    mod_id = data.get("id")
    name = data.get("name")
    version = data.get("version")
    return (
        mod_id if isinstance(mod_id, str) else None,
        name if isinstance(name, str) else None,
        version if isinstance(version, str) else None,
    )


def read_jar_metadata(jar_path: Path) -> tuple[str | None, str | None, str | None]:
    """Return (mod_id, display_name, version) for a mod jar, best effort."""
    try:
        with zipfile.ZipFile(jar_path) as zf:
            mod_id, name, version = _read_toml_metadata(zf)
            if mod_id:
                return mod_id, name, version
            for member in ("fabric.mod.json", "quilt.mod.json"):
                mod_id, name, version = _read_fabric_metadata(zf, member)
                if mod_id:
                    return mod_id, name, version
    except (zipfile.BadZipFile, OSError) as exc:
        log.warning("Could not read metadata from %s: %s", jar_path.name, exc)
    return None, None, None


def scan_mods_dir(mods_dir: Path) -> ModsManifest:
    """Hash and identify every .jar in the mods directory (non-recursive, like the loaders)."""
    if not mods_dir.is_dir():
        return ModsManifest()
    mods: list[ModInfo] = []
    for jar in sorted(mods_dir.glob("*.jar")):
        if not jar.is_file():
            continue
        mod_id, name, version = read_jar_metadata(jar)
        mods.append(
            ModInfo(
                filename=jar.name,
                sha256=_sha256(jar),
                mod_id=mod_id,
                name=name,
                version=version,
            )
        )
    return ModsManifest(mods=tuple(mods))

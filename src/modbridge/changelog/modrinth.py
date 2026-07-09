"""Optional Modrinth enrichment: attach upstream changelog snippets to updated mods.

Fail-soft by design: any API hiccup simply yields an unenriched changelog.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx

from modbridge.domain.models import ChangeKind, ChangeSet, ModChange

log = logging.getLogger(__name__)

_API = "https://api.modrinth.com/v2"
_MAX_SNIPPET = 300


def _sha512(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_SNIPPET else text[: _MAX_SNIPPET - 1] + "…"


def enrich_changeset(changeset: ChangeSet, mods_dir: Path) -> ChangeSet:
    """Look up each updated mod's new jar on Modrinth and attach its version changelog."""
    enriched: list[ModChange] = []
    with httpx.Client(base_url=_API, timeout=15.0, headers={"User-Agent": "modbridge"}) as client:
        for change in changeset.changes:
            if change.kind != ChangeKind.UPDATED:
                enriched.append(change)
                continue
            jar = mods_dir / change.filename
            changelog: str | None = None
            try:
                resp = client.get(
                    f"/version_file/{_sha512(jar)}", params={"algorithm": "sha512"}
                )
                if resp.status_code == 200:
                    raw = resp.json().get("changelog")
                    if isinstance(raw, str) and raw.strip():
                        changelog = _snippet(raw)
            except (httpx.HTTPError, OSError, ValueError) as exc:
                log.debug("Modrinth enrichment failed for %s: %s", change.filename, exc)
            enriched.append(
                ModChange(
                    kind=change.kind,
                    display_name=change.display_name,
                    filename=change.filename,
                    mod_id=change.mod_id,
                    old_version=change.old_version,
                    new_version=change.new_version,
                    changelog=changelog,
                )
            )
    return ChangeSet(
        changes=tuple(enriched),
        minecraft_old=changeset.minecraft_old,
        minecraft_new=changeset.minecraft_new,
        extra_notes=changeset.extra_notes,
    )

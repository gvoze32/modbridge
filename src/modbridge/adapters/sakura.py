"""Adapter for SakuraUpdater (server-side NeoForge mod).

Integration contract discovered from the upstream source (v0.3.0):
- The commit is a Brigadier server command: ``sakuraupdater commit <version> <description>``
  (no leading slash from the console). We inject it via the server supervisor.
- If ``<description>`` is a path to an existing file, SakuraUpdater reads that file's
  content as a markdown changelog — we always pass an absolute changelog file path.
- Its embedded HTTP server exposes ``POST /heartbeat`` (liveness) and
  ``POST /updateList`` (``{}`` => latest commit, ``{"version": v}`` => that commit),
  which lets us verify a commit landed without parsing server logs.
- Version strings are a SQLite primary key: duplicates hard-fail, and "latest" is
  ordered by commit timestamp. We generate ``YYYY.MM.DD`` with ``-2``, ``-3``…
  suffixes on same-day runs, checked against the live API.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from modbridge.adapters.base import ServerSupervisor
from modbridge.config.schema import Config

log = logging.getLogger(__name__)


class SakuraAdapter:
    def __init__(
        self,
        config: Config,
        supervisor: ServerSupervisor,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = f"http://{config.sakura.host}:{config.sakura.port}"
        self.command = config.sakura.command
        self.commit_timeout = config.sakura.commit_timeout
        self.mods_dir = config.mods_dir
        self.supervisor = supervisor
        self._client = httpx.Client(timeout=10.0, transport=transport)

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> httpx.Response | None:
        try:
            return self._client.post(f"{self.base_url}{path}", json=payload or {})
        except httpx.HTTPError as exc:
            log.debug("SakuraUpdater %s unreachable: %s", path, exc)
            return None

    def is_healthy(self, retries: int = 1, delay: float = 1.0) -> bool:
        for attempt in range(retries):
            resp = self._post("/heartbeat")
            if resp is not None and resp.status_code == 200:
                return True
            if attempt < retries - 1:
                time.sleep(delay)
        return False

    def _update_list(self, version: str | None = None) -> dict[str, Any] | None:
        payload = {"version": version} if version else {}
        resp = self._post("/updateList", payload)
        if resp is None or resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def latest_version(self) -> str | None:
        data = self._update_list()
        version = (data or {}).get("version")
        return version if isinstance(version, str) and version else None

    def version_exists(self, version: str) -> bool:
        data = self._update_list(version)
        return data is not None and data.get("version") == version

    def next_version(self, today: date | None = None) -> str:
        base = (today or date.today()).strftime("%Y.%m.%d")
        if not self.version_exists(base):
            return base
        for n in range(2, 100):
            candidate = f"{base}-{n}"
            if not self.version_exists(candidate):
                return candidate
        raise RuntimeError(f"Exhausted version suffixes for {base}")

    def _manifest_file_count(self, data: dict[str, Any]) -> int:
        paths = data.get("paths")
        if not isinstance(paths, list):
            return 0
        return sum(
            len(p.get("files", [])) for p in paths if isinstance(p, dict)
        )

    def commit(self, version: str, changelog_file: Path) -> bool:
        """Inject the commit command into the server console and verify via HTTP.

        Verification is two-fold: the version must appear in /updateList AND its
        manifest must actually contain files (an empty manifest means the mod's
        SYNC_DIR is broken — clients would "update" to nothing).
        """
        if not changelog_file.is_file():
            log.error("Changelog file missing: %s", changelog_file)
            return False
        self.supervisor.send_command(f"{self.command} commit {version} {changelog_file}")
        deadline = time.monotonic() + self.commit_timeout
        while time.monotonic() < deadline:
            data = self._update_list(version)
            if data and data.get("version") == version:
                files = self._manifest_file_count(data)
                mods_on_disk = any(self.mods_dir.glob("*.jar")) if self.mods_dir.is_dir() else False
                if files == 0 and mods_on_disk:
                    log.error(
                        "Commit %s landed but its manifest is EMPTY while mods/ has files — "
                        "SakuraUpdater's SYNC_DIR is not configured. Clients would receive "
                        "nothing. Check config/sakuraupdater-common.toml (or enable "
                        "sakura.manage_config).",
                        version,
                    )
                    return False
                log.info(
                    "SakuraUpdater commit %s verified via /updateList (%d files)", version, files
                )
                return True
            time.sleep(1.0)
        log.error(
            "Commit %s not visible via /updateList within %.0fs", version, self.commit_timeout
        )
        return False

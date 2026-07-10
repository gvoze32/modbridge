"""SakuraAdapter against a mock HTTP server that mimics the mod's /updateList."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from modbridge.adapters.sakura import SakuraAdapter
from tests.conftest import make_config, make_fabric_jar
from tests.fakes import FakeSupervisor

FULL_PATHS = [
    {
        "model": "mirror",
        "targetPath": "mods",
        "files": [{"sourcePath": "mods/a.jar", "targetPath": "mods/a.jar", "md5": "x"}],
    }
]


class ConsoleSupervisor(FakeSupervisor):
    """A supervisor whose console `commit` command lands in the fake HTTP db."""

    def __init__(self, db: dict[str, Any], paths: list[dict[str, Any]]) -> None:
        super().__init__(running=True)
        self.db = db
        self.paths = paths

    def send_command(self, command: str) -> None:
        super().send_command(command)
        parts = command.split()
        if len(parts) >= 3 and parts[1] == "commit":
            version = parts[2]
            self.db[version] = {
                "version": version,
                "time": "t",
                "description": "d",
                "paths": self.paths,
            }


def make_adapter(
    server_dir: Path, paths: list[dict[str, Any]]
) -> tuple[SakuraAdapter, ConsoleSupervisor]:
    db: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/heartbeat":
            return httpx.Response(200, text="OK")
        if request.url.path == "/updateList":
            body = json.loads(request.content or b"{}")
            version = body.get("version")
            if version:
                return httpx.Response(200, json=db.get(version, {}))
            latest = list(db.values())[-1] if db else {}
            return httpx.Response(200, json=latest)
        return httpx.Response(404)

    config = make_config(server_dir)
    supervisor = ConsoleSupervisor(db, paths)
    adapter = SakuraAdapter(config, supervisor, transport=httpx.MockTransport(handler))
    return adapter, supervisor


def test_commit_verified_with_files(server_dir: Path, tmp_path: Path) -> None:
    make_fabric_jar(server_dir / "mods" / "a.jar", "a", "1.0")
    changelog = tmp_path / "cl.md"
    changelog.write_text("# hi")
    adapter, supervisor = make_adapter(server_dir, FULL_PATHS)
    assert adapter.commit("2026.07.10", changelog)
    assert supervisor.commands == [f"sakuraupdater commit 2026.07.10 {changelog}"]


def test_commit_rejected_when_manifest_empty(server_dir: Path, tmp_path: Path) -> None:
    make_fabric_jar(server_dir / "mods" / "a.jar", "a", "1.0")  # mods exist on disk…
    changelog = tmp_path / "cl.md"
    changelog.write_text("# hi")
    adapter, _ = make_adapter(server_dir, paths=[])  # …but the commit carries nothing
    assert not adapter.commit("2026.07.10", changelog)


def test_commit_with_empty_mods_dir_allows_empty_manifest(
    server_dir: Path, tmp_path: Path
) -> None:
    changelog = tmp_path / "cl.md"
    changelog.write_text("# hi")
    adapter, _ = make_adapter(server_dir, paths=[])
    assert adapter.commit("2026.07.10", changelog)


def test_heartbeat_and_next_version(server_dir: Path, tmp_path: Path) -> None:
    make_fabric_jar(server_dir / "mods" / "a.jar", "a", "1.0")
    changelog = tmp_path / "cl.md"
    changelog.write_text("# hi")
    adapter, _ = make_adapter(server_dir, FULL_PATHS)
    assert adapter.is_healthy()
    today = date(2026, 7, 10)
    assert adapter.next_version(today) == "2026.07.10"
    assert adapter.commit("2026.07.10", changelog)
    assert adapter.latest_version() == "2026.07.10"
    assert adapter.next_version(today) == "2026.07.10-2"

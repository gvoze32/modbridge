from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

from modbridge.deps.github import GitHubClient
from modbridge.deps.installer import DependencyError, DependencyManager
from tests.conftest import make_config

MAINTAINER_RELEASES = [
    {
        "tag_name": "v1.0.4-beta",
        "draft": False,
        "assets": [
            {
                "name": "server.maintainer.by.woflo.1.0.4.jar",
                "browser_download_url": "https://dl.example/maintainer.jar",
                "size": 5,
            }
        ],
    }
]

SAKURA_RELEASES = [
    {
        "tag_name": "v0.3.0",
        "draft": False,
        "assets": [
            {
                "name": "sakuraupdater-0.3.0+1.21.1.jar",
                "browser_download_url": "https://dl.example/sakura-1.21.jar",
                "size": 5,
            },
            {
                "name": "sakuraupdater-0.3.0+1.20.1.jar",
                "browser_download_url": "https://dl.example/sakura-1.20.jar",
                "size": 5,
            },
        ],
    }
]


def transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "minecraft-server-maintainer/releases" in url:
            return httpx.Response(200, json=MAINTAINER_RELEASES)
        if "SakuraUpdater/releases" in url:
            return httpx.Response(200, json=SAKURA_RELEASES)
        if url.startswith("https://dl.example/"):
            return httpx.Response(200, content=b"jar!\n")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def manager_for(server_dir: Path, **dep_overrides: object) -> DependencyManager:
    config = make_config(
        server_dir,
        dependencies={"minecraft_version": "1.21.1", **dep_overrides},  # type: ignore[dict-item]
    )
    return DependencyManager(config, github=GitHubClient(transport=transport()))


def test_installs_missing_maintainer_and_sakura(server_dir: Path) -> None:
    manager = manager_for(server_dir)
    actions = manager.ensure_all()
    assert len(actions) == 2
    assert (server_dir / "maintainer.jar").read_bytes() == b"jar!\n"
    assert (server_dir / "mods" / "sakuraupdater-0.3.0+1.21.1.jar").is_file()
    recorded = json.loads((server_dir / ".modbridge" / "deps.json").read_text())
    assert recorded["maintainer"]["tag"] == "v1.0.4-beta"
    assert recorded["sakura"]["tag"] == "v0.3.0"


def test_noop_when_already_installed(server_dir: Path) -> None:
    manager = manager_for(server_dir)
    manager.ensure_all()
    assert manager.ensure_all() == []


def test_update_skips_when_tag_unchanged(server_dir: Path) -> None:
    manager = manager_for(server_dir)
    manager.ensure_all()
    assert manager.ensure_all(update=True) == []


def test_update_replaces_old_sakura_jar(server_dir: Path) -> None:
    manager = manager_for(server_dir)
    old = server_dir / "mods" / "sakuraupdater-0.2.2+1.21.1.jar"
    old.write_bytes(b"old")
    actions = manager.ensure_all(update=True)
    assert any("SakuraUpdater" in a for a in actions)
    assert not old.exists()
    assert (server_dir / "mods" / "sakuraupdater-0.3.0+1.21.1.jar").is_file()


def test_sakura_asset_matched_by_mc_version(server_dir: Path) -> None:
    manager = manager_for(server_dir, minecraft_version="1.20.1")
    manager.ensure_sakura()
    assert (server_dir / "mods" / "sakuraupdater-0.3.0+1.20.1.jar").is_file()


def test_sakura_mc_version_from_current_version_txt(server_dir: Path) -> None:
    (server_dir / "current_version.txt").write_text("1.21.1\n")
    manager = manager_for(server_dir, minecraft_version=None)
    manager.ensure_sakura()
    assert (server_dir / "mods" / "sakuraupdater-0.3.0+1.21.1.jar").is_file()


def test_sakura_ambiguous_without_mc_version(server_dir: Path) -> None:
    manager = manager_for(server_dir, minecraft_version=None)
    with pytest.raises(DependencyError, match="minecraft_version"):
        manager.ensure_sakura()


def test_sakura_no_matching_asset(server_dir: Path) -> None:
    manager = manager_for(server_dir, minecraft_version="1.19.2")
    with pytest.raises(DependencyError, match=re.escape("1.19.2")):
        manager.ensure_sakura()

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from modbridge.config.schema import Config
from modbridge.pipeline.context import RunContext, RunOptions
from modbridge.state.store import StateStore
from tests.fakes import FakeDistributor, FakeNotifier, FakeSupervisor, FakeUpdater

FIXED_NOW = datetime(2026, 7, 9, 4, 30, 0)


def make_fabric_jar(path: Path, mod_id: str, version: str, name: str | None = None) -> Path:
    """Create a minimal mod jar with fabric.mod.json metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps({"id": mod_id, "name": name or mod_id.title(), "version": version}),
        )
    return path


def make_neoforge_jar(
    path: Path, mod_id: str, version: str, name: str | None = None
) -> Path:
    """Create a minimal mod jar with META-INF/neoforge.mods.toml metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    toml = (
        f'[[mods]]\nmodId = "{mod_id}"\n'
        f'displayName = "{name or mod_id.title()}"\nversion = "{version}"\n'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/neoforge.mods.toml", toml)
    return path


@pytest.fixture
def server_dir(tmp_path: Path) -> Path:
    d = tmp_path / "server"
    (d / "mods").mkdir(parents=True)
    return d


def make_config(server_dir: Path, **overrides: Any) -> Config:
    data: dict[str, Any] = {
        "server": {"directory": str(server_dir)},
        "maintainer": {"jar": "maintainer.jar", "accept_eula": True},
        "maintenance": {"countdown": [3, 1]},
    }
    data.update(overrides)
    return Config.model_validate(data)


def make_context(
    config: Config,
    updater: FakeUpdater,
    supervisor: FakeSupervisor,
    distributor: FakeDistributor,
    notifier: FakeNotifier,
    options: RunOptions | None = None,
) -> RunContext:
    options = options or RunOptions()
    store = StateStore(config.state_dir)
    ctx = RunContext(
        config=config,
        options=options,
        updater=updater,
        supervisor=supervisor,
        distributor=distributor,
        notifiers=[notifier],
        store=store,
        state=store.load(),
        run_id="test-run",
        now=lambda: FIXED_NOW,
        sleep=lambda s: None,
    )
    return ctx

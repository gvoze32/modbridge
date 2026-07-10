from __future__ import annotations

from pathlib import Path

import pytest

from modbridge.adapters.sakura_config import (
    read_sakura_config,
    render_sakura_config,
    sakura_config_path,
    sakura_config_synced,
    write_sakura_config,
)
from modbridge.pipeline.engine import PipelineEngine
from tests.conftest import make_config, make_context, make_fabric_jar
from tests.fakes import FakeDistributor, FakeNotifier, FakeSupervisor, FakeUpdater


def test_render_read_roundtrip_with_regex_entry(tmp_path: Path) -> None:
    entries = ("mods:mirror", r"mods:ignore:.*sodium.*\.jar$")
    text = render_sakura_config(25564, entries)
    file = tmp_path / "sakuraupdater-common.toml"
    file.write_text(text)
    port, sync = read_sakura_config(file)
    assert port == 25564
    assert sync == list(entries)


def test_synced_detection(server_dir: Path) -> None:
    config = make_config(server_dir)
    assert sakura_config_synced(config)  # fixture writes the synced default
    sakura_config_path(config).write_text('port = 1234\nSYNC_DIR = []\n')
    assert not sakura_config_synced(config)
    write_sakura_config(config)
    assert sakura_config_synced(config)


def test_unreadable_file_counts_as_drift(server_dir: Path) -> None:
    config = make_config(server_dir)
    sakura_config_path(config).write_text('SYNC_DIR = ["broken')  # invalid TOML
    assert not sakura_config_synced(config)


def test_invalid_sync_dirs_rejected(server_dir: Path) -> None:
    with pytest.raises(ValueError, match="sync_dirs"):
        make_config(server_dir, sakura={"sync_dirs": ["mods:pull"]})  # 'pull' isn't real


def test_config_drift_triggers_maintenance_cycle(server_dir: Path) -> None:
    """No mod updates, everything published — but the SakuraUpdater config file is
    missing: the pipeline must restart the server once and write the file."""
    make_fabric_jar(server_dir / "mods" / "sodium.jar", "sodium", "0.5.0")
    config = make_config(server_dir)
    sakura_config_path(config).unlink()  # simulate first run / drifted config

    from tests.test_pipeline import committed_state_for

    committed_state_for(config, server_dir)
    supervisor = FakeSupervisor(running=True)
    ctx = make_context(config, FakeUpdater(), supervisor, FakeDistributor(), FakeNotifier())

    outcome = PipelineEngine(ctx).run()
    assert outcome.success
    assert supervisor.stop_calls == 1  # maintenance restart happened
    assert sakura_config_synced(config)  # and the file now exists, in sync
    port, sync = read_sakura_config(sakura_config_path(config))
    assert (port, sync) == (25564, ["mods:mirror"])

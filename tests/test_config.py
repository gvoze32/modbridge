from pathlib import Path

import pytest

from modbridge.config.schema import Config, ConfigError, load_config
from tests.conftest import make_config


def test_minimal_config(server_dir: Path) -> None:
    cfg = make_config(server_dir)
    assert cfg.server_dir == server_dir
    assert cfg.mods_dir == server_dir / "mods"
    assert cfg.maintainer_jar == server_dir / "maintainer.jar"
    assert cfg.state_dir == server_dir / ".modbridge"
    assert cfg.sakura.port == 25564


def test_eula_must_be_acknowledged(server_dir: Path) -> None:
    with pytest.raises(ValueError, match="accept_eula"):
        Config.model_validate(
            {"server": {"directory": str(server_dir)}, "maintainer": {"jar": "m.jar"}}
        )


def test_countdown_must_descend(server_dir: Path) -> None:
    with pytest.raises(ValueError, match="descending"):
        make_config(server_dir, maintenance={"countdown": [10, 30]})


def test_bad_window_rejected(server_dir: Path) -> None:
    with pytest.raises(ValueError, match="schedule window"):
        make_config(server_dir, schedule={"window": "four-to-five"})


def test_absolute_paths_kept(server_dir: Path, tmp_path: Path) -> None:
    jar = tmp_path / "elsewhere" / "maintainer.jar"
    cfg = make_config(server_dir, maintainer={"jar": str(jar), "accept_eula": True})
    assert cfg.maintainer_jar == jar


def test_load_config_yaml(server_dir: Path, tmp_path: Path) -> None:
    cfg_file = tmp_path / "modbridge.yaml"
    cfg_file.write_text(
        f"""
server:
  directory: {server_dir}
maintainer:
  jar: maintainer.jar
  accept_eula: true
schedule:
  window: "04:00-05:00"
"""
    )
    cfg = load_config(cfg_file)
    assert cfg.schedule.window == "04:00-05:00"


def test_load_config_errors(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError, match="Cannot read"):
        load_config(missing)
    bad = tmp_path / "bad.yaml"
    bad.write_text("just a string")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(bad)

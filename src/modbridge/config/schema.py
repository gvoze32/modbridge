"""YAML configuration schema and loader.

All relative paths in the config are resolved against ``server.directory`` so a
single config file works regardless of the cron working directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from modbridge.schedule import ScheduleWindow


class ConfigError(Exception):
    """Raised for unreadable or invalid configuration."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServerConfig(_Model):
    directory: Path
    tmux_session: str = "mc"
    start_command: str = "./run.sh"
    log_file: Path = Path("logs/latest.log")
    ready_pattern: str = r"Done \("
    startup_timeout: float = Field(default=300.0, gt=0)
    stop_timeout: float = Field(default=120.0, gt=0)

    @field_validator("directory")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return v.expanduser()


class MaintainerConfig(_Model):
    jar: Path
    java: str = "java"
    timeout: float = Field(default=1800.0, gt=0)
    # Running unattended requires --yes, which auto-accepts the Minecraft EULA on
    # fresh installs. We refuse to run until the admin acknowledges this explicitly.
    accept_eula: bool = False


class SakuraConfig(_Model):
    host: str = "127.0.0.1"
    port: int = Field(default=25564, ge=1, le=65535)
    command: str = "sakuraupdater"
    commit_timeout: float = Field(default=30.0, gt=0)
    startup_grace: float = Field(default=20.0, ge=0)


class MaintenanceConfig(_Model):
    countdown: tuple[int, ...] = (60, 30, 10, 5, 4, 3, 2, 1)
    message: str = "[Update] Server restarting for mod updates in {s}s"
    skip_if_empty: bool = True

    @field_validator("countdown")
    @classmethod
    def _descending_positive(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if any(s <= 0 for s in v):
            raise ValueError("countdown values must be positive seconds")
        if list(v) != sorted(v, reverse=True):
            raise ValueError("countdown must be in descending order, e.g. [60, 30, 10]")
        return v


class ScheduleConfig(_Model):
    window: str | None = None

    @field_validator("window")
    @classmethod
    def _parseable(cls, v: str | None) -> str | None:
        if v is not None:
            ScheduleWindow.parse(v)  # raises ValueError with a helpful message
        return v

    def parsed_window(self) -> ScheduleWindow | None:
        return ScheduleWindow.parse(self.window) if self.window else None


class ChangelogConfig(_Model):
    template: Path | None = None
    title: str = "Server Update {version}"
    modrinth_enrichment: bool = False


class NotificationsConfig(_Model):
    discord_webhook: str | None = None
    on_success: bool = True
    on_failure: bool = True


class Config(_Model):
    server: ServerConfig
    maintainer: MaintainerConfig
    sakura: SakuraConfig = SakuraConfig()
    maintenance: MaintenanceConfig = MaintenanceConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    changelog: ChangelogConfig = ChangelogConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    state_directory: Path = Path(".modbridge")
    log_level: str = "INFO"
    # Manual mod changes (dropped in by hand) still require a restart before commit,
    # so clients never receive mods the running server hasn't loaded yet.
    restart_on_manual_changes: bool = True

    @model_validator(mode="after")
    def _check_eula_ack(self) -> Self:
        if not self.maintainer.accept_eula:
            raise ValueError(
                "maintainer.accept_eula must be set to true. Unattended updates run the "
                "maintainer with --yes, which accepts the Minecraft EULA "
                "(https://aka.ms/MinecraftEULA) on your behalf."
            )
        return self

    # --- resolved paths (relative entries anchor at the server directory) ---

    def _resolve(self, p: Path) -> Path:
        p = p.expanduser()
        return p if p.is_absolute() else self.server.directory / p

    @property
    def server_dir(self) -> Path:
        return self.server.directory

    @property
    def mods_dir(self) -> Path:
        return self.server_dir / "mods"

    @property
    def maintainer_jar(self) -> Path:
        return self._resolve(self.maintainer.jar)

    @property
    def server_log(self) -> Path:
        return self._resolve(self.server.log_file)

    @property
    def state_dir(self) -> Path:
        return self._resolve(self.state_directory)


def load_config(path: Path) -> Config:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(data).__name__}")
    try:
        return Config.model_validate(data)
    except ValueError as exc:
        raise ConfigError(f"Invalid configuration in {path}:\n{exc}") from exc

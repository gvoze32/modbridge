"""Manages SakuraUpdater's server-side config file.

The mod registers its NeoForge COMMON config *without* a filename, so the file
it actually reads is ``config/sakuraupdater-common.toml`` — the upstream README
documents ``sakuraupdater-server.toml``, which is never read in mod mode. To
spare admins that trap, ModBridge owns the real file: declare ``sakura.port``
and ``sakura.sync_dirs`` in modbridge.yaml and the file is written for you
during the next safe window (server stopped).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import tomllib
from pathlib import Path

from modbridge.config.schema import Config

log = logging.getLogger(__name__)


def sakura_config_path(config: Config) -> Path:
    return config.server_dir / "config" / "sakuraupdater-common.toml"


def render_sakura_config(port: int, sync_dirs: tuple[str, ...]) -> str:
    # json.dumps produces valid TOML basic strings (escapes backslashes in regexes).
    entries = "".join(f"    {json.dumps(d)},\n" for d in sync_dirs)
    return (
        "# Managed by ModBridge (sakura.manage_config) — manual edits will be overwritten.\n"
        "# This IS the file the SakuraUpdater mod reads (its README names the wrong file).\n"
        "\n"
        "# The port of the embedded HTTP file server.\n"
        f"port = {port}\n"
        "\n"
        '# Sync directories, format "target:mode[:source...]".\n'
        "# Modes: mirror (full sync, deletes extras), push (copy only), ignore (regex).\n"
        f"SYNC_DIR = [\n{entries}]\n"
    )


def read_sakura_config(path: Path) -> tuple[int | None, list[str] | None]:
    """(port, sync_dirs) as currently on disk, or (None, None) if unreadable."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None, None
    port = data.get("port")
    sync = data.get("SYNC_DIR")
    return (
        port if isinstance(port, int) else None,
        [str(s) for s in sync] if isinstance(sync, list) else None,
    )


def sakura_config_synced(config: Config) -> bool:
    port, sync = read_sakura_config(sakura_config_path(config))
    return port == config.sakura.port and sync == list(config.sakura.sync_dirs)


def write_sakura_config(config: Config) -> Path:
    path = sakura_config_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_sakura_config(config.sakura.port, config.sakura.sync_dirs)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".sakura-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    log.info(
        "Wrote %s (port=%d, %d sync dirs)", path, config.sakura.port, len(config.sakura.sync_dirs)
    )
    return path

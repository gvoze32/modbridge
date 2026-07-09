"""Automatic installation and updating of ModBridge's two upstream tools:

- Minecraft Server Maintainer jar (worflor/minecraft-server-maintainer releases)
  -> saved to the configured ``maintainer.jar`` path.
- SakuraUpdater server-side mod (NamelessXiaoJiang/SakuraUpdater releases)
  -> placed in ``mods/``, matched against the server's Minecraft version.
  Replacing it in mods/ means the new version is also *published to players*
  through the normal pipeline commit.

Installed release tags are recorded in ``<state_dir>/deps.json`` so update
checks can compare tags instead of re-downloading.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from modbridge.config.schema import Config
from modbridge.deps.github import GitHubClient, GitHubError, ReleaseAsset

log = logging.getLogger(__name__)


class DependencyError(Exception):
    pass


class DependencyManager:
    def __init__(self, config: Config, github: GitHubClient | None = None) -> None:
        self.config = config
        self.github = github or GitHubClient()
        self.deps_file = config.state_dir / "deps.json"

    # --- installed-version bookkeeping ---

    def _installed(self) -> dict[str, Any]:
        try:
            data = json.loads(self.deps_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _record(self, key: str, asset: ReleaseAsset) -> None:
        data = self._installed()
        data[key] = {"tag": asset.tag, "asset": asset.name}
        self.deps_file.parent.mkdir(parents=True, exist_ok=True)
        self.deps_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # --- maintainer ---

    def ensure_maintainer(self, update: bool = False) -> str | None:
        """Install the maintainer jar if missing (or outdated with update=True).
        Returns a human-readable action message, or None if nothing was done."""
        dest = self.config.maintainer_jar
        if dest.is_file() and not update:
            return None
        repo = self.config.dependencies.maintainer_repo
        assets = self.github.latest_release_assets(repo)
        jars = [a for a in assets if a.name.endswith(".jar") and "sources" not in a.name]
        if not jars:
            raise DependencyError(f"No jar asset in the latest release of {repo}")
        asset = jars[0]
        if dest.is_file() and self._installed().get("maintainer", {}).get("tag") == asset.tag:
            return None
        self.github.download(asset, dest)
        self._record("maintainer", asset)
        return f"Maintainer {asset.tag} installed to {dest.name}"

    # --- SakuraUpdater ---

    def _minecraft_version(self) -> str | None:
        configured = self.config.dependencies.minecraft_version
        if configured:
            return configured
        try:
            version = (self.config.server_dir / "current_version.txt").read_text().strip()
            return version or None
        except OSError:
            return None

    def _existing_sakura_jars(self) -> list[Path]:
        if not self.config.mods_dir.is_dir():
            return []
        return sorted(
            p for p in self.config.mods_dir.glob("*.jar") if "sakuraupdater" in p.name.lower()
        )

    def _pick_sakura_asset(self, assets: list[ReleaseAsset]) -> ReleaseAsset:
        jars = [a for a in assets if a.name.endswith(".jar") and "sources" not in a.name]
        if not jars:
            raise DependencyError("No jar asset in the latest SakuraUpdater release")
        mc = self._minecraft_version()
        if mc:
            # Try the full version first ("1.21.1"), then major.minor ("1.21").
            for needle in (mc, ".".join(mc.split(".")[:2])):
                matches = [a for a in jars if needle in a.name]
                if matches:
                    return matches[0]
            names = ", ".join(a.name for a in jars)
            raise DependencyError(
                f"No SakuraUpdater asset matches Minecraft {mc}. Available: {names}. "
                "Set dependencies.minecraft_version explicitly if detection is wrong."
            )
        if len(jars) == 1:
            return jars[0]
        names = ", ".join(a.name for a in jars)
        raise DependencyError(
            "Cannot pick a SakuraUpdater asset: Minecraft version unknown and the release "
            f"has several jars ({names}). Set dependencies.minecraft_version in the config."
        )

    def ensure_sakura(self, update: bool = False) -> str | None:
        existing = self._existing_sakura_jars()
        if existing and not update:
            return None
        repo = self.config.dependencies.sakura_repo
        asset = self._pick_sakura_asset(self.github.latest_release_assets(repo))
        if existing and self._installed().get("sakura", {}).get("tag") == asset.tag:
            return None
        dest = self.config.mods_dir / asset.name
        self.github.download(asset, dest)
        for old in existing:
            if old != dest:
                old.unlink(missing_ok=True)
                log.info("Removed old SakuraUpdater jar %s", old.name)
        self._record("sakura", asset)
        return f"SakuraUpdater {asset.tag} installed to mods/{asset.name}"

    # --- combined ---

    def ensure_all(self, update: bool = False) -> list[str]:
        """Install/update both dependencies; returns action messages (may be empty).
        Raises DependencyError/GitHubError on hard failures."""
        actions: list[str] = []
        for step in (self.ensure_maintainer, self.ensure_sakura):
            message = step(update=update)
            if message:
                actions.append(message)
        return actions


__all__ = ["DependencyError", "DependencyManager", "GitHubError"]

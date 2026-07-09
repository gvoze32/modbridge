"""Minimal GitHub releases client: list release assets, download them atomically."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


class GitHubError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    tag: str
    name: str
    download_url: str
    size: int


class GitHubClient:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            timeout=60.0,
            follow_redirects=True,
            headers={
                "User-Agent": "modbridge",
                "Accept": "application/vnd.github+json",
            },
            transport=transport,
        )

    def latest_release_assets(self, repo: str) -> list[ReleaseAsset]:
        """Assets of the newest non-draft release (prereleases included: some
        upstreams, like the maintainer, only ship beta tags)."""
        try:
            resp = self._client.get(
                f"https://api.github.com/repos/{repo}/releases", params={"per_page": 10}
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"Cannot reach GitHub for {repo}: {exc}") from exc
        if resp.status_code != 200:
            raise GitHubError(f"GitHub API for {repo} returned {resp.status_code}")
        releases: list[dict[str, Any]] = resp.json()
        for release in releases:
            if release.get("draft"):
                continue
            tag = release.get("tag_name", "")
            return [
                ReleaseAsset(
                    tag=tag,
                    name=asset["name"],
                    download_url=asset["browser_download_url"],
                    size=asset.get("size", 0),
                )
                for asset in release.get("assets", [])
            ]
        raise GitHubError(f"No published releases found for {repo}")

    def download(self, asset: ReleaseAsset, dest: Path) -> None:
        """Stream the asset to a temp file, then atomically move it into place."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".download-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f, self._client.stream("GET", asset.download_url) as resp:
                if resp.status_code != 200:
                    raise GitHubError(
                        f"Download of {asset.name} failed with HTTP {resp.status_code}"
                    )
                for chunk in resp.iter_bytes():
                    f.write(chunk)
            os.replace(tmp_name, dest)
            log.info("Downloaded %s (%s) -> %s", asset.name, asset.tag, dest)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

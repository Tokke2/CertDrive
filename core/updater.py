# -*- coding: utf-8 -*-
"""GitHub Releases update discovery; checks are best-effort and non-blocking."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


REPOSITORY = "Tokke2/CertDrive"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"


@dataclass(frozen=True, slots=True)
class ReleaseUpdate:
    version: str
    name: str
    url: str
    notes: str
    commit_sha: str = ""


def _version_key(value: str) -> tuple[int, ...] | None:
    """Parse common v-prefixed release tags without accepting arbitrary text."""
    normalized = value.strip().lstrip("vV")
    match = re.fullmatch(r"(\d+(?:\.\d+){0,3})(?:[-+][0-9A-Za-z.-]+)?", normalized)
    if not match:
        return None
    try:
        parts = [int(part) for part in match.group(1).split(".")]
        return tuple((parts + [0, 0, 0, 0])[:4])
    except ValueError:
        return None


def get_current_commit(root: Path | str) -> str | None:
    """Read the installed checkout's commit if Git metadata is available."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(Path(root)), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = completed.stdout.strip().lower()
    return commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None


def _request_json(url: str, *, timeout: float) -> dict[str, object]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "IntygsDrive-ReleaseChecker",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
        raise
    if not isinstance(payload, dict):
        raise ValueError("GitHub returnerade ett oväntat svar")
    return payload


def check_latest_release(
    current_version: str,
    *,
    current_commit: str | None = None,
    timeout: float = 4.0,
) -> ReleaseUpdate | None:
    """Return a newer release or newer code published under the same version.

    Versions are deliberately allowed to remain unchanged. For a same-version
    release, installed Git checkouts compare their HEAD SHA with the release tag
    SHA, so republishing v0.0.1 with new code is still detected. Builds without
    Git metadata fall back to semantic-version comparison.
    """
    current = _version_key(current_version)
    if current is None:
        raise ValueError(f"Ogiltigt installerat versionsnummer: {current_version!r}")

    payload = _request_json(LATEST_RELEASE_API, timeout=timeout)
    tag = str(payload.get("tag_name", "")).strip()
    latest = _version_key(tag)
    if not tag or latest is None:
        raise ValueError("Senaste GitHub Release saknar ett giltigt versionsnummer")
    if payload.get("draft") or payload.get("prerelease") or latest < current:
        return None

    release_commit = ""
    if latest == current:
        if not current_commit:
            return None
        commit_payload = _request_json(
            f"https://api.github.com/repos/{REPOSITORY}/commits/{quote(tag, safe='')}",
            timeout=timeout,
        )
        release_commit = str(commit_payload.get("sha", "")).strip().lower()
        installed_commit = current_commit.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", release_commit):
            raise ValueError("GitHub kunde inte ange commit för releasetaggen")
        if release_commit == installed_commit:
            return None

    # Never navigate to arbitrary URLs from an API response.
    release_url = str(payload.get("html_url", ""))
    if not release_url.startswith(f"https://github.com/{REPOSITORY}/releases/"):
        release_url = RELEASES_PAGE
    name = str(payload.get("name") or tag).strip()[:160]
    notes = str(payload.get("body") or "").strip()[:4000]
    return ReleaseUpdate(
        version=tag,
        name=name,
        url=release_url,
        notes=notes,
        commit_sha=release_commit,
    )

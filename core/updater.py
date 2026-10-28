# -*- coding: utf-8 -*-
"""Safe, staged updates from the rolling GitHub Release."""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

REPOSITORY = "Tokke2/CertDrive"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_UNPACKED_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_FILES = 800
UPDATE_INTERVAL_HOURS = 12
_STAGING_NAME = ".update-staging"
_STATE_NAME = "update_state.json"
_PENDING_NAME = "pending.json"
_RUNTIME_DIRS = frozenset({"core", "plugins", "Icon"})
_RUNTIME_ROOT_FILES = frozenset({"main.pyw", "main_guards.py"})
_RUNTIME_SUFFIXES = frozenset({".py", ".pyw", ".svg", ".png", ".ico"})


@dataclass(frozen=True, slots=True)
class ReleaseUpdate:
    version: str
    name: str
    url: str
    notes: str
    commit_sha: str = ""


@dataclass(frozen=True, slots=True)
class UpdateStageResult:
    staged: bool
    commit_sha: str
    version: str
    message: str


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


def _state_path(root: Path) -> Path:
    return root / "js" / _STATE_NAME


def _load_state_commit(root: Path) -> str | None:
    try:
        value = json.loads(_state_path(root).read_text(encoding="utf-8"))
        commit = str(value.get("commit_sha", "")).strip().lower() if isinstance(value, dict) else ""
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return commit if _COMMIT_RE.fullmatch(commit) else None


def get_current_commit(root: Path | str) -> str | None:
    """Return installed code identity from update state or available Git metadata."""
    app_root = Path(root)
    recorded = _load_state_commit(app_root)
    if recorded:
        return recorded
    try:
        completed = subprocess.run(
            ["git", "-C", str(app_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = completed.stdout.strip().lower()
    return commit if _COMMIT_RE.fullmatch(commit) else None


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
    """Return a newer release or changed code published under the same version.

    When the local build has no commit identity, callers compare the downloaded
    source archive with local runtime files before deciding whether to install.
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

    commit_payload = _request_json(
        f"https://api.github.com/repos/{REPOSITORY}/commits/{quote(tag, safe='')}",
        timeout=timeout,
    )
    release_commit = str(commit_payload.get("sha", "")).strip().lower()
    if not _COMMIT_RE.fullmatch(release_commit):
        raise ValueError("GitHub kunde inte ange commit för releasetaggen")
    installed_commit = (current_commit or "").strip().lower()
    if latest == current and installed_commit == release_commit:
        return None

    release_url = str(payload.get("html_url", ""))
    if not release_url.startswith(f"https://github.com/{REPOSITORY}/releases/"):
        release_url = RELEASES_PAGE
    name = str(payload.get("name") or tag).strip()[:160]
    notes = str(payload.get("body") or "").strip()[:4000]
    return ReleaseUpdate(tag, name, release_url, notes, release_commit)


def _is_runtime_path(relative: PurePosixPath) -> bool:
    if not relative.parts or any(
        part in {".", ".."} or part.startswith(".") or ":" in part or "\\" in part
        for part in relative.parts
    ):
        return False
    if "__pycache__" in relative.parts or relative.suffix.casefold() not in _RUNTIME_SUFFIXES:
        return False
    if len(relative.parts) == 1:
        return relative.name in _RUNTIME_ROOT_FILES
    return relative.parts[0] in _RUNTIME_DIRS


def _archive_runtime_files(archive_bytes: bytes) -> dict[PurePosixPath, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("GitHub-uppdateringen är inte ett giltigt ZIP-arkiv") from exc
    with archive:
        result: dict[PurePosixPath, bytes] = {}
        total = 0
        for info in archive.infolist():
            if info.is_dir():
                continue
            if len(result) >= _MAX_ARCHIVE_FILES:
                raise ValueError("Uppdateringen innehåller för många filer")
            if info.file_size < 0 or info.file_size > _MAX_UNPACKED_BYTES:
                raise ValueError("En fil i uppdateringen är orimligt stor")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ValueError("Uppdateringen innehåller en otillåten symbolisk länk")
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts or len(member.parts) < 2:
                continue
            # GitHub zipballs wrap the repository files in one generated root folder.
            relative = PurePosixPath(*member.parts[1:])
            if not _is_runtime_path(relative):
                continue
            total += info.file_size
            if total > _MAX_UNPACKED_BYTES:
                raise ValueError("Uppdateringens innehåll överskrider storleksgränsen")
            result[relative] = archive.read(info)
        if not result:
            raise ValueError("Uppdateringsarkivet innehåller inga appfiler")
        return result


def _safe_target(root: Path, relative: PurePosixPath) -> Path:
    resolved_root = root.resolve()
    target = resolved_root.joinpath(*relative.parts)
    parent = target.parent
    if not parent.resolve().is_relative_to(resolved_root):
        raise ValueError(f"Uppdateringssökvägen lämnar appmappen: {relative}")
    parent.mkdir(parents=True, exist_ok=True)
    if not parent.resolve().is_relative_to(resolved_root):
        raise ValueError(f"Uppdateringssökvägen lämnar appmappen: {relative}")
    if target.is_symlink():
        raise ValueError(f"Appfilen är en symbolisk länk och uppdateras inte: {relative}")
    return target


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _record_installed_commit(root: Path, release: ReleaseUpdate) -> None:
    _write_json_atomic(
        _state_path(root),
        {
            "version": release.version,
            "commit_sha": release.commit_sha,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )


def stage_release_archive(
    root: Path | str,
    release: ReleaseUpdate,
    archive_bytes: bytes,
) -> UpdateStageResult:
    """Compare a GitHub source zip, then stage only allow-listed runtime files."""
    app_root = Path(root).resolve()
    if not _COMMIT_RE.fullmatch(release.commit_sha):
        raise ValueError("Releasen saknar en giltig commit-SHA")
    if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
        raise ValueError("Uppdateringsarkivet överskrider storleksgränsen")
    if not (app_root / "main.pyw").is_file() or not (app_root / "core").is_dir():
        raise RuntimeError("Automatisk uppdatering stöds bara för källkodsversionen av appen")
    files = _archive_runtime_files(archive_bytes)
    current = True
    for relative, content in files.items():
        target = app_root.joinpath(*relative.parts)
        try:
            if not target.is_file() or target.read_bytes() != content:
                current = False
                break
        except OSError:
            current = False
            break

    staging_root = app_root / "js" / _STAGING_NAME
    pending_path = staging_root / _PENDING_NAME
    if current:
        _record_installed_commit(app_root, release)
        pending_path.unlink(missing_ok=True)
        for child in staging_root.iterdir() if staging_root.is_dir() else ():
            if child.name != _PENDING_NAME:
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        return UpdateStageResult(False, release.commit_sha, release.version, "already-current")

    stage_dir = staging_root / release.commit_sha
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    files_dir = stage_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        destination = files_dir.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    _write_json_atomic(
        pending_path,
        {
            "version": release.version,
            "commit_sha": release.commit_sha,
            "stage_dir": release.commit_sha,
            "staged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )
    for child in staging_root.iterdir():
        if child.name not in {_PENDING_NAME, release.commit_sha}:
            shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
    return UpdateStageResult(True, release.commit_sha, release.version, "staged")


def stage_release_update(
    root: Path | str,
    release: ReleaseUpdate,
    *,
    timeout: float = 30.0,
) -> UpdateStageResult:
    """Download a pinned GitHub commit archive and stage it for safe shutdown."""
    url = f"https://api.github.com/repos/{REPOSITORY}/zipball/{quote(release.commit_sha, safe='')}"
    request = Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "IntygsDrive-Updater"},
    )
    with urlopen(request, timeout=timeout) as response:
        final_host = response.geturl().split("/", 3)[2].casefold()
        if final_host not in {"api.github.com", "codeload.github.com"}:
            raise ValueError("Uppdateringsarkivet kom från en otillåten värd")
        archive_bytes = response.read(_MAX_ARCHIVE_BYTES + 1)
    if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
        raise ValueError("Uppdateringsarkivet överskrider storleksgränsen")
    return stage_release_archive(root, release, archive_bytes)


def _read_pending_update(root: Path) -> tuple[Path, ReleaseUpdate] | None:
    staging_root = root / "js" / _STAGING_NAME
    pending_path = staging_root / _PENDING_NAME
    try:
        data = json.loads(pending_path.read_text(encoding="utf-8"))
        commit = str(data["commit_sha"]).lower()
        version = str(data["version"])
        stage_name = str(data["stage_dir"])
        if not _COMMIT_RE.fullmatch(commit) or stage_name != commit:
            return None
        stage_dir = (staging_root / stage_name / "files").resolve()
        if stage_dir.parent.parent != staging_root.resolve():
            return None
        release = ReleaseUpdate(version, f"IntygsDrive {version}", RELEASES_PAGE, "", commit)
        if stage_dir.is_dir():
            return stage_dir, release
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return None


def launch_pending_update_helper(root: Path | str, parent_pid: int) -> bool:
    """Start a detached, quiet helper that installs staged files after shutdown."""
    app_root = Path(root).resolve()
    if getattr(sys, "frozen", False) or _read_pending_update(app_root) is None:
        return False
    helper_code = (
        "import sys; from pathlib import Path; "
        "from core.updater import run_update_helper; "
        "run_update_helper(Path(sys.argv[1]), int(sys.argv[2]))"
    )
    command = [sys.executable, "-c", helper_code, str(app_root), str(int(parent_pid))]
    options: dict[str, object] = {
        "cwd": str(app_root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen(command, **options)
        return True
    except OSError:
        return False


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _append_helper_log(root: Path, message: str) -> None:
    try:
        log_path = root / "js" / "update-helper.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}\n")
    except OSError:
        pass


def run_update_helper(root: Path | str, parent_pid: int, *, wait_timeout: float = 48 * 60 * 60) -> bool:
    """Wait for the app to close, then atomically apply the staged source update."""
    app_root = Path(root).resolve()
    deadline = time.monotonic() + max(1.0, wait_timeout)
    while _pid_is_running(int(parent_pid)) and time.monotonic() < deadline:
        time.sleep(0.5)
    if _pid_is_running(int(parent_pid)):
        _append_helper_log(app_root, "Staged update left pending: app did not close before timeout.")
        return False

    pending = _read_pending_update(app_root)
    if pending is None:
        return False
    files_dir, release = pending
    changes: list[tuple[Path, Path | None]] = []
    backup_dir = files_dir.parent / "rollback"
    try:
        for source in sorted(path for path in files_dir.rglob("*") if path.is_file()):
            if source.is_symlink():
                raise ValueError(f"Symbolisk länk i staging: {source}")
            relative = PurePosixPath(source.relative_to(files_dir).as_posix())
            if not _is_runtime_path(relative):
                raise ValueError(f"Otillåten fil i staging: {relative}")
            target = _safe_target(app_root, relative)
            backup: Path | None = None
            if target.exists():
                backup = backup_dir.joinpath(*relative.parts)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            changes.append((target, backup))
            temporary = target.with_name(f".{target.name}.update-tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        _record_installed_commit(app_root, release)
    except Exception as exc:
        for target, backup in reversed(changes):
            try:
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    temporary = target.with_name(f".{target.name}.rollback-tmp")
                    shutil.copy2(backup, temporary)
                    os.replace(temporary, target)
            except OSError:
                pass
        _append_helper_log(app_root, f"Update failed and rollback attempted: {type(exc).__name__}: {exc}")
        return False

    for folder in (app_root / "core", app_root / "plugins"):
        for cache in folder.rglob("__pycache__") if folder.is_dir() else ():
            shutil.rmtree(cache, ignore_errors=True)
    staging_root = app_root / "js" / _STAGING_NAME
    shutil.rmtree(staging_root, ignore_errors=True)
    _append_helper_log(app_root, f"Installed {release.version} ({release.commit_sha}).")
    return True

"""GitHub Release discovery, verified download, and self-update support."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from version import __version__


REPOSITORY = "JCH2333/fenix_to_ini"
GITHUB_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
GITHUB_RELEASE_URL = f"https://github.com/{REPOSITORY}/releases"
MIRROR_PREFIXES = (
    "https://gh-proxy.com/",
    "https://ghfast.top/",
)
USER_AGENT = f"fenix-to-ini/{__version__}"
MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024
MAX_EXTRACTED_SIZE = 100 * 1024 * 1024
MAX_PACKAGE_FILES = 500
MANIFEST_NAME = "update-manifest.json"
REQUIRED_PROGRAM_FILES = {"gui.py", "main.py", "update_manager.py", "version.py"}
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    name: str
    page_url: str
    asset_name: str | None = None
    asset_url: str | None = None
    asset_sha256: str | None = None
    asset_size: int | None = None


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    update_available: bool
    release: ReleaseInfo | None = None
    error: str | None = None


class UpdateError(RuntimeError):
    """Raised when an update cannot be safely downloaded or installed."""


def parse_version(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch((value or "").strip())
    if not match:
        raise ValueError(f"不支持的版本号: {value!r}")
    return tuple(int(part) for part in match.groups())


def is_newer_version(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def _request_urls(url: str) -> tuple[str, ...]:
    return (url, *(prefix + url for prefix in MIRROR_PREFIXES))


def _open(request_url: str, opener, timeout: float):
    request = Request(
        request_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    return opener(request, timeout=timeout)


def _read_limited(response, limit: int) -> bytes:
    length = response.headers.get("Content-Length") if response.headers else None
    if length:
        try:
            if int(length) > limit:
                raise UpdateError("远程文件超过允许大小")
        except ValueError:
            pass

    chunks = []
    total = 0
    while True:
        chunk = response.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise UpdateError("远程文件超过允许大小")
        chunks.append(chunk)
    return b"".join(chunks)


def _fetch_latest_release(opener, timeout: float) -> dict:
    errors = []
    for request_url in _request_urls(GITHUB_API_URL):
        try:
            with _open(request_url, opener, timeout) as response:
                payload = json.loads(
                    _read_limited(response, 2 * 1024 * 1024).decode("utf-8")
                )
            if not isinstance(payload, dict):
                raise UpdateError("更新接口返回格式无效")
            return payload
        except (HTTPError, URLError, TimeoutError, OSError, ValueError,
                json.JSONDecodeError, UpdateError) as exc:
            errors.append(f"{request_url}: {exc}")
    raise UpdateError("GitHub 和国内镜像均无法访问") from (
        RuntimeError("; ".join(errors)) if errors else None
    )


def _release_from_payload(payload: dict, current_version: str) -> ReleaseInfo:
    tag_name = str(payload.get("tag_name") or "").strip()
    latest_tuple = parse_version(tag_name)
    version = ".".join(str(part) for part in latest_tuple)
    page_url = f"{GITHUB_RELEASE_URL}/tag/{tag_name}"
    name = str(payload.get("name") or tag_name)

    if not is_newer_version(version, current_version):
        return ReleaseInfo(version, tag_name, name, page_url)

    expected_asset = f"fenix_to_ini-v{version}.zip"
    selected = None
    for asset in payload.get("assets") or ():
        if isinstance(asset, dict) and asset.get("name") == expected_asset:
            selected = asset
            break
    if not selected:
        raise UpdateError(f"最新版本缺少一键更新包: {expected_asset}")

    digest = str(selected.get("digest") or "")
    if not digest.lower().startswith("sha256:"):
        raise UpdateError("一键更新包缺少 GitHub SHA-256 校验值")
    sha256 = digest.split(":", 1)[1].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise UpdateError("一键更新包 SHA-256 校验值无效")

    asset_url = str(selected.get("browser_download_url") or "")
    if not asset_url.startswith(
        f"https://github.com/{REPOSITORY}/releases/download/"
    ):
        raise UpdateError("一键更新包下载地址无效")
    asset_size = int(selected.get("size") or 0)
    if asset_size <= 0 or asset_size > MAX_DOWNLOAD_SIZE:
        raise UpdateError("一键更新包大小无效")

    return ReleaseInfo(
        version=version,
        tag_name=tag_name,
        name=name,
        page_url=page_url,
        asset_name=expected_asset,
        asset_url=asset_url,
        asset_sha256=sha256,
        asset_size=asset_size,
    )


def check_for_update(
    current_version: str = __version__,
    *,
    opener=None,
    timeout: float = 6.0,
) -> UpdateCheckResult:
    """Check GitHub and mirrors for a newer stable release."""
    opener = opener or urlopen
    try:
        parse_version(current_version)
        payload = _fetch_latest_release(opener, timeout)
        release = _release_from_payload(payload, current_version)
        return UpdateCheckResult(
            current_version=current_version,
            update_available=is_newer_version(release.version, current_version),
            release=release,
        )
    except (ValueError, TypeError, UpdateError) as exc:
        return UpdateCheckResult(
            current_version=current_version,
            update_available=False,
            error=str(exc),
        )


def download_update(
    release: ReleaseInfo,
    *,
    opener=None,
    timeout: float = 20.0,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    """Download and SHA-256 verify an update asset."""
    if not release.asset_url or not release.asset_sha256 or not release.asset_size:
        raise UpdateError("Release 没有可用的一键更新包")
    opener = opener or urlopen
    errors = []

    for request_url in _request_urls(release.asset_url):
        temp_path = None
        try:
            request = Request(request_url, headers={"User-Agent": USER_AGENT})
            with opener(request, timeout=timeout) as response:
                declared = response.headers.get("Content-Length") \
                    if response.headers else None
                if declared and int(declared) > MAX_DOWNLOAD_SIZE:
                    raise UpdateError("更新包超过允许大小")
                handle = tempfile.NamedTemporaryFile(
                    prefix="fenix_to_ini_update_", suffix=".zip", delete=False
                )
                temp_path = Path(handle.name)
                digest = hashlib.sha256()
                received = 0
                with handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > MAX_DOWNLOAD_SIZE:
                            raise UpdateError("更新包超过允许大小")
                        handle.write(chunk)
                        digest.update(chunk)
                        if progress_callback:
                            progress_callback(received, release.asset_size)
            if received != release.asset_size:
                raise UpdateError(
                    f"更新包大小不一致: {received}/{release.asset_size}"
                )
            if digest.hexdigest().lower() != release.asset_sha256.lower():
                raise UpdateError("更新包 SHA-256 校验失败")
            validate_update_package(temp_path, release.version)
            return temp_path
        except (HTTPError, URLError, TimeoutError, OSError, ValueError,
                UpdateError) as exc:
            errors.append(f"{request_url}: {exc}")
            if temp_path:
                temp_path.unlink(missing_ok=True)
    raise UpdateError("GitHub 和国内镜像均无法下载有效更新包") from (
        RuntimeError("; ".join(errors)) if errors else None
    )


def _safe_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(
        part in ("", ".", "..") for part in path.parts
    ):
        raise UpdateError(f"更新包包含不安全路径: {name}")
    if path.parts[0] in {".git", "backups", "diagnostics_tmp", "output"}:
        raise UpdateError(f"更新包包含禁止路径: {name}")
    return path


def _load_package_manifest(package: zipfile.ZipFile) -> dict:
    try:
        payload = json.loads(package.read(MANIFEST_NAME).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("更新包清单缺失或无效") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
        raise UpdateError("更新包清单格式无效")
    return payload


def validate_update_package(package_path: Path | str,
                            expected_version: str) -> dict[str, str]:
    """Validate ZIP paths, manifest, version, sizes, and per-file hashes."""
    package_path = Path(package_path)
    try:
        package = zipfile.ZipFile(package_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError("更新包不是有效的 ZIP 文件") from exc

    with package:
        infos = package.infolist()
        if len(infos) > MAX_PACKAGE_FILES:
            raise UpdateError("更新包文件数量超过限制")
        total_size = sum(info.file_size for info in infos)
        if total_size > MAX_EXTRACTED_SIZE:
            raise UpdateError("更新包解压后超过允许大小")
        names = set()
        for info in infos:
            path = _safe_member_path(info.filename)
            names.add(path.as_posix())
            if info.is_dir():
                continue
            unix_mode = (info.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise UpdateError("更新包不能包含符号链接")

        manifest = _load_package_manifest(package)
        if manifest.get("version") != expected_version:
            raise UpdateError("更新包版本与 Release 不一致")
        files = manifest["files"]
        if not REQUIRED_PROGRAM_FILES.issubset(files):
            raise UpdateError("更新包缺少必要程序文件")

        validated = {}
        for name, expected_hash in files.items():
            safe_name = _safe_member_path(str(name)).as_posix()
            if safe_name == MANIFEST_NAME or safe_name not in names:
                raise UpdateError(f"更新包清单引用了缺失文件: {safe_name}")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", str(expected_hash)):
                raise UpdateError(f"更新包文件校验值无效: {safe_name}")
            actual_hash = hashlib.sha256(package.read(safe_name)).hexdigest()
            if actual_hash.lower() != str(expected_hash).lower():
                raise UpdateError(f"更新包文件校验失败: {safe_name}")
            validated[safe_name] = actual_hash

        version_source = package.read("version.py").decode("utf-8")
        version_match = re.search(
            r'^__version__\s*=\s*["\']([^"\']+)["\']',
            version_source,
            re.MULTILINE,
        )
        if not version_match or version_match.group(1) != expected_version:
            raise UpdateError("更新包内部程序版本不一致")
        return validated


def apply_update_package(package_path: Path | str, install_dir: Path | str,
                         expected_version: str) -> Path:
    """Install a verified package with backup and rollback on failure."""
    package_path = Path(package_path)
    install_dir = Path(install_dir).resolve()
    files = validate_update_package(package_path, expected_version)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = install_dir / "backups" / f"program_update_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    replaced = []
    created = []

    try:
        with zipfile.ZipFile(package_path) as package:
            for relative_name in sorted(files):
                relative = Path(*PurePosixPath(relative_name).parts)
                destination = (install_dir / relative).resolve()
                if install_dir not in destination.parents:
                    raise UpdateError(f"安装目标越界: {relative_name}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    backup = backup_dir / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, backup)
                    replaced.append((destination, backup))
                else:
                    created.append(destination)

                temporary = destination.with_name(destination.name + ".update-new")
                with package.open(relative_name) as source, open(temporary, "wb") as target:
                    shutil.copyfileobj(source, target)
                os.replace(temporary, destination)
    except Exception:
        for destination in reversed(created):
            destination.unlink(missing_ok=True)
        for destination, backup in reversed(replaced):
            shutil.copy2(backup, destination)
        raise
    return backup_dir


def _wait_for_parent(parent_pid: int, timeout_seconds: int = 60):
    if parent_pid <= 0:
        return
    if os.name == "nt":
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(
            synchronize, False, parent_pid
        )
        if handle:
            try:
                ctypes.windll.kernel32.WaitForSingleObject(
                    handle, timeout_seconds * 1000
                )
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        return

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            os.kill(parent_pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def _write_result_file(success: bool, message: str, version: str) -> Path:
    fd, name = tempfile.mkstemp(prefix="fenix_to_ini_update_result_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(
            {"success": success, "message": message, "version": version},
            handle,
            ensure_ascii=False,
        )
    return Path(name)


def _start_gui(install_dir: Path, result_path: Path):
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(
        [sys.executable, str(install_dir / "gui.py"),
         "--update-result", str(result_path)],
        cwd=install_dir,
        creationflags=creationflags,
        close_fds=True,
    )


def launch_update_installer(package_path: Path | str,
                            install_dir: Path | str,
                            release: ReleaseInfo,
                            parent_pid: int) -> None:
    """Launch the detached updater; caller should then close the GUI."""
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--apply",
            str(Path(package_path).resolve()),
            str(Path(install_dir).resolve()),
            release.version,
            str(parent_pid),
        ],
        cwd=Path(install_dir),
        creationflags=creationflags,
        close_fds=True,
    )


def _installer_main(package_path: Path, install_dir: Path,
                    version: str, parent_pid: int) -> int:
    _wait_for_parent(parent_pid)
    try:
        backup_dir = apply_update_package(package_path, install_dir, version)
        result = _write_result_file(
            True, f"已更新到 v{version}\n备份位置: {backup_dir}", version
        )
        return_code = 0
    except Exception as exc:
        result = _write_result_file(False, f"自动更新失败: {exc}", version)
        return_code = 1
    finally:
        package_path.unlink(missing_ok=True)
    _start_gui(install_dir, result)
    return return_code


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("package", nargs="?")
    parser.add_argument("install_dir", nargs="?")
    parser.add_argument("version", nargs="?")
    parser.add_argument("parent_pid", nargs="?", type=int)
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    if not args.apply or not all(
        (args.package, args.install_dir, args.version, args.parent_pid)
    ):
        raise SystemExit(2)
    raise SystemExit(_installer_main(
        Path(args.package), Path(args.install_dir),
        args.version, args.parent_pid,
    ))

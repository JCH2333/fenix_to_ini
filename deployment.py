"""Target-specific deployment of a staged DFDv2 navigation database."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from db_utils import check_integrity, open_target, vacuum
from tables.toliss import sanitize_toliss_data


WINDOWS_EPOCH_OFFSET_SECONDS = 11644473600


@dataclass(frozen=True)
class DeploymentProfile:
    key: str
    label: str
    write_cycle_json: bool
    as346_compatibility: bool = False


DEPLOYMENT_PROFILES = {
    "ini_a340": DeploymentProfile("ini_a340", "iniBuilds A340", True),
    "ini_a350": DeploymentProfile("ini_a350", "iniBuilds A350", True),
    "as346": DeploymentProfile("as346", "Aerosoft AS346", True, True),
    "c919": DeploymentProfile("c919", "C919", True),
}


@dataclass(frozen=True)
class DeploymentResult:
    profile: DeploymentProfile
    database_paths: tuple[Path, ...]
    backup_directory: Path
    sha256: str


def find_package_layout(db_path: str) -> Path | None:
    """Find the nearest package layout.json above a navigation database."""
    current = Path(db_path).resolve().parent
    for directory in (current, *current.parents):
        candidate = directory / "layout.json"
        if candidate.is_file():
            return candidate
    return None


def _layout_date(path: Path) -> int:
    """Convert a file modification time to the MSFS FILETIME representation."""
    return int((path.stat().st_mtime + WINDOWS_EPOCH_OFFSET_SECONDS) * 10_000_000)


def update_package_layout(db_path: str, cycle_path: str) -> str | None:
    """Update size/date records when db.s3db is inside a Community package."""
    layout_path = find_package_layout(db_path)
    if layout_path is None:
        return None

    package_root = layout_path.parent
    payload = json.loads(layout_path.read_text(encoding="utf-8"))
    entries = {
        entry.get("path", "").replace("\\", "/").casefold(): entry
        for entry in payload.get("content", [])
    }

    for file_path in (Path(db_path).resolve(), Path(cycle_path).resolve()):
        relative = file_path.relative_to(package_root).as_posix()
        entry = entries.get(relative.casefold())
        if entry is None:
            raise ValueError(f"layout.json is missing package file: {relative}")
        entry["size"] = file_path.stat().st_size
        entry["date"] = _layout_date(file_path)

    layout_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(layout_path)


def is_simulator_running() -> bool:
    """Return whether MSFS 2024 is running before touching its data files."""
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq FlightSimulator2024.exe", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "flightsimulator2024.exe" in result.stdout.casefold()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _backup_existing(path: Path, backup_directory: Path) -> None:
    if not path.exists():
        return
    destination = backup_directory / path.name
    suffix = 2
    while destination.exists():
        destination = backup_directory / f"{path.stem}_{suffix}{path.suffix}"
        suffix += 1
    shutil.copy2(path, destination)


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_target_cycle_json(staged_database: Path, target_database: Path) -> Path:
    source = staged_database.with_name("cycle.json")
    if not source.is_file():
        raise FileNotFoundError(f"暂存周期元数据不存在: {source}")
    target = target_database.with_name("cycle.json")
    payload = _read_json(source)
    existing = _read_json(target)
    for key in ("name", "format"):
        if existing.get(key):
            payload[key] = existing[key]
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def _prepare_database(staged_database: Path, profile: DeploymentProfile,
                      temporary_directory: Path) -> Path:
    prepared = temporary_directory / staged_database.name
    shutil.copy2(staged_database, prepared)
    if not profile.as346_compatibility:
        return prepared

    connection = open_target(str(prepared))
    try:
        sanitize_toliss_data(connection)
        vacuum(connection)
        if not check_integrity(connection):
            raise sqlite3.DatabaseError("AS346 部署副本完整性校验失败")
    finally:
        connection.close()
    return prepared


def deploy_staged_database(
    staged_database: str | Path,
    profile_key: str,
    target_paths: list[str] | tuple[str, ...],
    *,
    backup_root: str | Path | None = None,
    require_simulator_closed: bool = True,
) -> DeploymentResult:
    """Deploy one staged database to every loading location of one aircraft.

    The source is copied once and never modified.  AS346 gets its compatibility
    cleanup only in a private temporary copy; iniBuilds and C919 receive the
    byte-identical standard DFDv2 staging output.
    """
    if profile_key not in DEPLOYMENT_PROFILES:
        raise ValueError(f"未知部署目标: {profile_key}")
    profile = DEPLOYMENT_PROFILES[profile_key]
    staged_database = Path(staged_database).resolve()
    if not staged_database.is_file():
        raise FileNotFoundError(f"暂存导航数据库不存在: {staged_database}")
    if require_simulator_closed and is_simulator_running():
        raise RuntimeError("检测到 Microsoft Flight Simulator 2024 正在运行，请完全退出游戏后再部署。")

    targets = tuple(dict.fromkeys(Path(path).resolve() for path in target_paths))
    if not targets:
        raise ValueError(f"未检测到 {profile.label} 的导航数据位置")
    for target in targets:
        if not target.is_file():
            raise FileNotFoundError(f"目标导航数据库不存在: {target}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    root = Path(backup_root) if backup_root else Path(__file__).resolve().parent / "backups"
    backup_directory = root / f"deploy_{timestamp}" / profile.key
    backup_directory.mkdir(parents=True, exist_ok=False)
    temporary_directory = backup_directory / "prepared"
    temporary_directory.mkdir()
    prepared = _prepare_database(staged_database, profile, temporary_directory)
    expected_hash = _sha256(prepared)

    touched: list[tuple[Path, Path, Path | None, Path | None]] = []
    try:
        for index, target in enumerate(targets, start=1):
            target_backup = backup_directory / f"target_{index}"
            target_backup.mkdir()
            _backup_existing(target, target_backup)
            for suffix in ("-wal", "-shm"):
                _backup_existing(Path(str(target) + suffix), target_backup)

            cycle_path = target.with_name("cycle.json")
            layout_path = find_package_layout(str(target))
            if profile.write_cycle_json:
                _backup_existing(cycle_path, target_backup)
            if layout_path:
                _backup_existing(layout_path, target_backup)

            touched.append((target, target_backup, cycle_path if profile.write_cycle_json else None, layout_path))

            replacement = target.with_name(target.name + ".deploy-new")
            shutil.copy2(prepared, replacement)
            os.replace(replacement, target)
            for suffix in ("-wal", "-shm"):
                Path(str(target) + suffix).unlink(missing_ok=True)

            if _sha256(target) != expected_hash:
                raise RuntimeError(f"部署后校验失败: {target}")
            if profile.write_cycle_json:
                written_cycle = _write_target_cycle_json(staged_database, target)
                if layout_path:
                    update_package_layout(str(target), str(written_cycle))
    except Exception:
        for target, backup, cycle_path, layout_path in reversed(touched):
            original = backup / target.name
            if original.exists():
                shutil.copy2(original, target)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(target) + suffix)
                original_sidecar = backup / sidecar.name
                if original_sidecar.exists():
                    shutil.copy2(original_sidecar, sidecar)
                else:
                    sidecar.unlink(missing_ok=True)
            if cycle_path:
                original_cycle = backup / cycle_path.name
                if original_cycle.exists():
                    shutil.copy2(original_cycle, cycle_path)
                else:
                    cycle_path.unlink(missing_ok=True)
            if layout_path:
                original_layout = backup / layout_path.name
                if original_layout.exists():
                    shutil.copy2(original_layout, layout_path)
        raise
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)

    return DeploymentResult(profile, targets, backup_directory, expected_hash)

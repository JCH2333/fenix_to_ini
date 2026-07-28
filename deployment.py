"""Helpers for deploying converted data into an MSFS Community package."""

import json
from pathlib import Path


WINDOWS_EPOCH_OFFSET_SECONDS = 11644473600


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

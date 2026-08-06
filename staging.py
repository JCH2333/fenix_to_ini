"""Create and manage the local DFDv2 navigation-data staging area."""

from __future__ import annotations

import os
from pathlib import Path

from main import run_conversion


STAGING_DIRECTORY = Path(__file__).resolve().parent / "output" / "staged"
STAGING_DATABASE_NAME = "fenix_naip_dfdv2.s3db"


def staging_database_path() -> Path:
    """Return the stable local database path used before deployment."""
    return STAGING_DIRECTORY / STAGING_DATABASE_NAME


def clear_staging_sidecars() -> None:
    """Discard stale SQLite sidecars left by an interrupted staging run."""
    database = staging_database_path()
    for suffix in ("-wal", "-shm"):
        (Path(str(database) + suffix)).unlink(missing_ok=True)


def create_staged_navigation_data(
    src_path: str,
    template_path: str,
    csv_path: str | None = None,
    *,
    skip_procedures: bool = False,
    skip_rte: bool = False,
    progress_callback=None,
) -> str:
    """Convert Fenix data once into the local, target-neutral DFDv2 copy."""
    STAGING_DIRECTORY.mkdir(parents=True, exist_ok=True)
    clear_staging_sidecars()
    database = staging_database_path()
    return run_conversion(
        src_path=src_path,
        dst_path=template_path,
        csv_path=csv_path,
        output_path=str(database),
        skip_procedures=skip_procedures,
        skip_rte=skip_rte,
        no_backup=True,
        overwrite_mode=False,
        target_profile="generic",
        progress_callback=progress_callback,
    ) or str(database)

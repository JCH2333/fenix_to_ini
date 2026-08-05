#!/usr/bin/env python3
"""
Fenix -> iniBuilds Navigation Data Converter

Converts Chinese airspace navigation data from Fenix A320 format (nd.db3)
to iniBuilds DFDv2 format (db.s3db), merging new data into the existing
iniBuilds database.

Usage:
    CLI:  python main.py [--src PATH] [--dst PATH] [--csv PATH] [--overwrite]
    GUI:  python gui.py

AIRAC Cycle: 2607
"""

import sys
import os
import argparse
import shutil
from datetime import datetime, timezone
from typing import Callable

# Ensure script directory is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_utils import (
    open_source, open_target, count_rows, vacuum, check_integrity
)
from merge import report_changes

from tables.header import convert_header
from tables.airports import convert_airports
from tables.runways import convert_runways
from tables.navaids import convert_navaids
from tables.waypoints import convert_waypoints
from tables.airways import convert_airways
from tables.localizers import convert_localizers
from tables.procedures import convert_procedures
from tables.rest import (
    convert_holdings, convert_gls, convert_markers,
    convert_grid_mora, convert_airport_comm
)
from tables.empty_tables import create_empty_tables
from tables.toliss import is_toliss_target, sanitize_toliss_data
from rte_seg import parse_rte_seg, resolve_coordinates, merge_rte_seg_to_airways
from region_lookup import RegionLookup
from naip_metadata import NaipProcedureMetadata
from deployment import find_package_layout, update_package_layout


# Default log/print callback (can be overridden by GUI)
_log_func: Callable[[str], None] = print


def log(msg: str = ""):
    """Send message to current log handler (print by default, GUI text widget when set)."""
    _log_func(msg)


def set_log_callback(cb: Callable[[str], None]):
    """Set a custom log callback (e.g., for GUI integration)."""
    global _log_func
    _log_func = cb


ProgressCallback = Callable[[int, int, str], None]  # (current_phase, total_phases, phase_name)


def _write_cycle_json(target_dir: str, cycle_info: dict):
    """
    Write cycle.json alongside the output s3db.

    Format matches Navigraph DFDv2 cycle.json:
    {
        "cycle": "2607",
        "revision": "2",
        "name": "iniBuilds DFD v2",
        "format": "dfdv2",
        "validityPeriod": "2026-07-09/2026-08-05"
    }
    """
    import json

    cycle = cycle_info.get('cycle', '2607')
    start_d = cycle_info.get('start_d', '09')
    start_m = cycle_info.get('start_m', '07')
    end_d = cycle_info.get('end_d', '05')
    end_m = cycle_info.get('end_m', '08')
    # Year: Fenix uses 2-digit, Navigraph uses 4-digit
    yy = cycle_info.get('start_y', '26')
    year = f"20{yy}" if len(yy) == 2 else yy

    rev = str(cycle_info.get('revision', '1'))

    json_path = os.path.join(os.path.dirname(target_dir), 'cycle.json')
    existing = {}
    if os.path.isfile(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except (OSError, ValueError):
            existing = {}

    cycle_json = {
        "cycle": cycle,
        "revision": rev,
        "name": existing.get("name", "iniBuilds DFD v2"),
        "format": existing.get("format", "dfdv2"),
        "validityPeriod": (
            f"{year}-{start_m}-{start_d}/"
            f"{('20' + cycle_info.get('end_y', yy)) if len(cycle_info.get('end_y', yy)) == 2 else cycle_info.get('end_y', yy)}-{end_m}-{end_d}"
        )
    }

    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(cycle_json, f, indent=2, ensure_ascii=False)
        log(f"Updated cycle.json: {json_path}")
        return json_path
    except Exception as e:
        log(f"WARNING: Could not write cycle.json: {e}")
        raise


def _copy_target_cycle_json(dst_path: str, working_path: str) -> str | None:
    """Copy the target's DFDv2 metadata beside an isolated output database."""
    source_path = os.path.join(os.path.dirname(os.path.abspath(dst_path)), 'cycle.json')
    if not os.path.isfile(source_path):
        return None

    target_path = os.path.join(
        os.path.dirname(os.path.abspath(working_path)), 'cycle.json'
    )
    shutil.copy2(source_path, target_path)
    return target_path


def run_conversion(
    src_path: str,
    dst_path: str,
    csv_path: str | None = None,
    *,
    output_path: str | None = None,
    skip_procedures: bool = False,
    skip_rte: bool = False,
    no_backup: bool = False,
    overwrite_mode: bool = True,
    progress_callback: ProgressCallback | None = None,
    dry_run: bool = False,
) -> str | None:
    """
    Run the full Fenix -> iniBuilds conversion pipeline.

    Args:
        src_path:  Path to Fenix nd.db3
        dst_path:  Path to iniBuilds db.s3db (the target to enhance)
        csv_path:  Path to RTE_SEG.csv (optional)
        output_path: Where to write the enhanced s3db. If None, overwrites dst_path.
        skip_procedures: Skip Phase 7 (terminal procedures) for speed.
        skip_rte: Skip RTE_SEG.csv processing.
        no_backup: Skip backup creation.
        overwrite_mode: If True, copy dst -> work on copy -> write back to dst.
        progress_callback: Called with (phase, total, label) for progress tracking.
        dry_run: Analyze only, don't write changes.

    Returns:
        Path to the output s3db, or None if dry_run.
    """
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"Source database not found: {src_path}")
    if not os.path.exists(dst_path):
        raise FileNotFoundError(f"Destination database not found: {dst_path}")

    log("=" * 60)
    log("  Fenix -> iniBuilds Navigation Data Converter")
    log("  China Region Supplement (NAIP)")
    log("=" * 60)
    log(f"  Source:      {src_path}")
    log(f"  Destination: {dst_path}")
    if output_path:
        log(f"  Output:      {output_path}")
    if csv_path:
        log(f"  CSV:         {csv_path}")
    log(f"  Overwrite:   {overwrite_mode}")
    log("=" * 60)

    # Phase count depends on options
    total_phases = 9  # all phases except optional RTE and procedures
    if csv_path and not skip_rte:
        total_phases += 1  # 5b
    if not skip_procedures:
        total_phases += 1
    phase = 0

    def advance(label: str):
        nonlocal phase
        phase += 1
        if progress_callback:
            progress_callback(phase, total_phases, label)
        log()
        log(f"[{phase}/{total_phases}] {label}")
        log("-" * 40)

    # --- Setup destination ---
    working_path = os.path.abspath(output_path) if output_path else dst_path
    backup_path = None
    dry_run_path = None

    if dry_run:
        import tempfile
        fd, dry_run_path = tempfile.mkstemp(
            prefix='fenix_to_ini_dry_run_', suffix='.s3db',
            dir=os.path.dirname(os.path.abspath(__file__)),
        )
        os.close(fd)
        shutil.copy2(dst_path, dry_run_path)
        working_path = dry_run_path
        log("DRY RUN MODE - using a disposable database copy")
    else:
        if os.path.abspath(working_path) != os.path.abspath(dst_path):
            os.makedirs(os.path.dirname(working_path), exist_ok=True)

        if not no_backup and os.path.exists(working_path):
            # Back up the database and any Community package metadata that the
            # conversion will replace.
            backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            companion_paths = [working_path]
            cycle_path = os.path.join(os.path.dirname(working_path), 'cycle.json')
            if os.path.exists(cycle_path):
                companion_paths.append(cycle_path)
            layout_path = find_package_layout(working_path)
            if layout_path:
                companion_paths.append(str(layout_path))

            for source_path in companion_paths:
                fname = os.path.basename(source_path)
                target_path = os.path.join(
                    backup_dir, f'{fname}.backup_{timestamp}'
                )
                log(f"Backup: {target_path}")
                shutil.copy2(source_path, target_path)
                if os.path.abspath(source_path) == os.path.abspath(working_path):
                    backup_path = target_path

        if os.path.abspath(working_path) != os.path.abspath(dst_path):
            shutil.copy2(dst_path, working_path)
            _copy_target_cycle_json(dst_path, working_path)

    dst_conn = open_target(working_path)

    src_conn = None

    try:
        # Capture pre-conversion counts
        log()
        report_changes(dst_conn, "BEFORE")

        # Open source
        src_conn = open_source(src_path)

        # === Phase 0 ===
        advance("Phase 0: Header & Metadata")
        cycle_info = convert_header(src_conn, dst_conn)

        # 初始化 2607 NAIP CSV 区域码交叉参考（用于修正 icao_code 区域分配）
        region_lookup = RegionLookup()
        procedure_metadata = NaipProcedureMetadata(region_lookup.csv_dir)

        # === Phase 1 ===
        advance("Phase 1: Airports")
        airport_lookup = convert_airports(src_conn, dst_conn)

        if not airport_lookup:
            log("WARNING: No Chinese airports found in source!")
        else:
            # === Phase 2 ===
            advance("Phase 2: Runways")
            runway_lookup = convert_runways(src_conn, dst_conn, airport_lookup)

            # === Phase 3 ===
            advance("Phase 3: Navaids")
            navaid_lookup = convert_navaids(src_conn, dst_conn, airport_lookup, region_lookup)

            # === Phase 4 ===
            advance("Phase 4: Waypoints")
            waypoint_lookup, terminal_wpt_ids = convert_waypoints(
                src_conn, dst_conn, airport_lookup, region_lookup
            )

            # === Phase 5 ===
            advance("Phase 5: Airways")
            convert_airways(src_conn, dst_conn, waypoint_lookup, navaid_lookup)

            # === Phase 5b ===
            if not skip_rte and csv_path and os.path.exists(csv_path):
                advance("Phase 5b: RTE_SEG.csv Airways")
                segments = parse_rte_seg(csv_path)
                if segments:
                    resolved = resolve_coordinates(
                        segments, waypoint_lookup, navaid_lookup
                    )
                    merge_rte_seg_to_airways(dst_conn, resolved)
            elif not skip_rte and csv_path:
                log(f"RTE_SEG.csv not found at {csv_path}, skipping")
            elif skip_rte:
                log("RTE_SEG.csv: SKIPPED")

            # === Phase 6 ===
            advance("Phase 6: Localizers / ILS")
            convert_localizers(src_conn, dst_conn, airport_lookup, runway_lookup)

            # === Phase 7 ===
            if not skip_procedures:
                advance("Phase 7: Terminal Procedures (SID/STAR/IAP)")
                convert_procedures(
                    src_conn, dst_conn,
                    airport_lookup, runway_lookup,
                    waypoint_lookup, navaid_lookup, procedure_metadata
                )
            else:
                log("Phase 7: Terminal Procedures - SKIPPED")

            # === Phase 8 ===
            advance("Phase 8: Holdings, GLS, Markers, MORA, Communications")
            convert_holdings(src_conn, dst_conn)
            convert_gls(src_conn, dst_conn, airport_lookup)
            convert_markers(src_conn, dst_conn, airport_lookup)
            convert_grid_mora(src_conn, dst_conn)
            convert_airport_comm(src_conn, dst_conn, airport_lookup)

        # === Phase 9 ===
        advance("Phase 9: Empty Tables & Schema")
        create_empty_tables(dst_conn, working_path)

        if is_toliss_target(dst_conn):
            log()
            log("=== ToLiss / AS346 Compatibility ===")
            toliss_stats = sanitize_toliss_data(dst_conn)
            log(
                "  ToLiss 兼容清洗: "
                f"补齐 {toliss_stats['ndb_magvar_defaulted']} 个 NDB 磁偏角，"
                f"移除 {toliss_stats['waypoints_removed']} 个超长航路点，"
                f"移除 {toliss_stats['airways_removed']} 条无效航路记录，"
                f"规范化 {toliss_stats['procedure_fields_normalized']} 个程序字段，"
                f"重排 {toliss_stats['runways_reordered']} 条跑道记录"
            )

        # --- Post-conversion ---
        report_changes(dst_conn, "AFTER")

        if not dry_run:
            log()
            log("Optimizing database (VACUUM)...")
            vacuum(dst_conn)

            # Write cycle.json to target directory
            cycle_json_path = _write_cycle_json(working_path, cycle_info)
            layout_path = update_package_layout(working_path, cycle_json_path)
            if layout_path:
                log(f"Updated MSFS package layout: {layout_path}")

        log()
        check_integrity(dst_conn)

        if not dry_run:
            log()
            log(f"Conversion complete! Updated: {working_path}")
            return working_path
        else:
            log()
            log("Dry run complete. No changes were made.")
            return None

    except Exception as e:
        log(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        if backup_path:
            log(f"Restore from backup at: {backup_path}")
        raise

    finally:
        if dst_conn:
            dst_conn.close()
        if src_conn:
            src_conn.close()
        if dry_run_path:
            for suffix in ('', '-wal', '-shm'):
                path = dry_run_path + suffix
                if os.path.exists(path):
                    os.remove(path)

    log("Done.")
    return working_path


# ---- CLI entry point ----
def main():
    parser = argparse.ArgumentParser(
        description='Fenix -> DFDv2 Navigation Data Converter (China Region)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --src ../nd.db3 --dst ../db.s3db --csv ../RTE_SEG.csv
  python main.py --overwrite --dst "C:/Users/.../inibuilds-aircraft-a340/work/NavigationData/db.s3db"
        """
    )
    parser.add_argument('--src', default=None,
                        help='Path to Fenix nd.db3')
    parser.add_argument('--dst', default=None,
                        help='Path to target DFDv2 s3db')
    parser.add_argument('--csv', default=None,
                        help='Path to RTE_SEG.csv')
    parser.add_argument('--output', default=None,
                        help='Output path (default: overwrite --dst)')
    parser.add_argument('--overwrite', action='store_true',
                        help='Overwrite destination in-place (creates backup first)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Analyze only, do not write changes')
    parser.add_argument('--skip-rte', action='store_true',
                        help='Skip RTE_SEG.csv processing')
    parser.add_argument('--skip-procedures', action='store_true',
                        help='Skip terminal procedure conversion (fast mode)')
    parser.add_argument('--no-backup', action='store_true',
                        help='Do not create backup of destination db')
    parser.add_argument('--auto-detect', action='store_true',
                        help='Auto-detect paths instead of using defaults')
    parser.add_argument(
        '--aircraft', choices=('ini-a340', 'as346'), default='ini-a340',
        help='Aircraft target used with --auto-detect (default: ini-a340)'
    )

    args = parser.parse_args()

    # Resolve paths
    from auto_detect import detect_all, print_detection_report

    if args.auto_detect:
        detected = detect_all()
        print_detection_report(detected)

        # Auto-select: prefer Fenix db + first MSFS2024 iniBuilds
        src_path = args.src or detected.get('fenix_db')
        csv_path = args.csv or detected.get('fenix_csv')

        ini_results = (
            detected.get('as346_s3db', {})
            if args.aircraft == 'as346'
            else detected.get('ini_s3db', {})
        )
        # Prefer MSFS2024 paths over MSFS2020. Exclude "目录存在，无s3db"
        # placeholder entries (directory found but no db.s3db written yet) —
        # those keys are still labeled "MSFS2024 - ..." so a naive '2024' in k
        # match would wrongly select a non-existent database and silently
        # fall through to MSFS2020 instead.
        msfs24_keys = [k for k in ini_results if '2024' in k and '无s3db' not in k]
        msfs20_keys = [k for k in ini_results if '2020' in k]
        selected_ini = None
        if msfs24_keys:
            selected_ini = ini_results[msfs24_keys[0]]
        elif msfs20_keys:
            selected_ini = ini_results[msfs20_keys[0]]
        dst_path = args.dst or selected_ini

        if not src_path:
            print("错误：无法自动检测 Fenix nd.db3")
            sys.exit(1)
        if not dst_path:
            print("错误：无法自动检测 iniBuilds db.s3db")
            sys.exit(1)
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        src_path = os.path.normpath(os.path.join(script_dir, args.src or '../nd.db3'))
        dst_path = os.path.normpath(os.path.join(script_dir, args.dst or '../db.s3db'))
        csv_path = args.csv
        if csv_path:
            csv_path = os.path.normpath(os.path.join(script_dir, csv_path))
        else:
            default_csv = os.path.normpath(os.path.join(script_dir, '../RTE_SEG.csv'))
            csv_path = default_csv if os.path.exists(default_csv) else None

    try:
        result = run_conversion(
            src_path=src_path,
            dst_path=dst_path,
            csv_path=csv_path,
            output_path=args.output,
            skip_procedures=args.skip_procedures,
            skip_rte=args.skip_rte,
            no_backup=args.no_backup,
            overwrite_mode=args.overwrite,
            dry_run=args.dry_run,
        )
        if result:
            print(f"\nOutput: {result}")
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nConversion failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

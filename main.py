#!/usr/bin/env python3
"""
Fenix → iniBuilds Navigation Data Converter

Converts Chinese airspace navigation data from Fenix A320 format (nd.db3)
to iniBuilds DFDv2 format (db.s3db), merging new data into the existing
iniBuilds database.

Usage:
    python main.py [--src PATH] [--dst PATH] [--csv PATH] [--dry-run]

AIRAC Cycle: 2607
"""

import sys
import os
import argparse
import time
import shutil
from datetime import datetime, timezone

# Ensure parent directory is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_utils import (
    open_source, open_target, copy_target_template,
    count_rows, vacuum, check_integrity
)
from merge import report_changes

# Table conversion modules
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
from rte_seg import parse_rte_seg, resolve_coordinates, merge_rte_seg_to_airways


def main():
    parser = argparse.ArgumentParser(
        description='Fenix → iniBuilds Navigation Data Converter (China Region)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --dry-run
  python main.py --src ../nd.db3 --dst ../db.s3db --csv ../RTE_SEG.csv
        """
    )
    parser.add_argument('--src', default='../nd.db3',
                        help='Path to Fenix nd.db3 (default: ../nd.db3)')
    parser.add_argument('--dst', default='../db.s3db',
                        help='Path to iniBuilds db.s3db (default: ../db.s3db)')
    parser.add_argument('--csv', default='../RTE_SEG.csv',
                        help='Path to RTE_SEG.csv (default: ../RTE_SEG.csv)')
    parser.add_argument('--output', default=None,
                        help='Output path for modified db.s3db (default: overwrite --dst)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Analyze only, do not write changes')
    parser.add_argument('--skip-rte', action='store_true',
                        help='Skip RTE_SEG.csv processing')
    parser.add_argument('--skip-procedures', action='store_true',
                        help='Skip terminal procedure conversion (fast mode)')
    parser.add_argument('--no-backup', action='store_true',
                        help='Do not create backup of destination db')

    args = parser.parse_args()

    # Resolve paths relative to script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.normpath(os.path.join(script_dir, args.src))
    dst_path = os.path.normpath(os.path.join(script_dir, args.dst))
    csv_path = os.path.normpath(os.path.join(script_dir, args.csv))
    output_path = args.output

    if not os.path.exists(src_path):
        print(f"ERROR: Source database not found: {src_path}")
        sys.exit(1)
    if not os.path.exists(dst_path):
        print(f"ERROR: Destination database not found: {dst_path}")
        sys.exit(1)

    print("=" * 60)
    print("  Fenix → iniBuilds Navigation Data Converter")
    print("  AIRAC Cycle 2607 — China Region Supplement")
    print("=" * 60)
    print(f"  Source:      {src_path}")
    print(f"  Destination: {dst_path}")
    print(f"  CSV:         {csv_path}")
    print(f"  Dry run:     {args.dry_run}")
    print("=" * 60)

    # Open source (read-only)
    print("\nOpening source database (read-only)...")
    src_conn = open_source(src_path)

    # Prepare destination
    if args.dry_run:
        print("DRY RUN MODE — no changes will be written")
        dst_conn = open_target(dst_path)
        working_path = dst_path
    else:
        # Create backup
        if not args.no_backup:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            backup_path = dst_path + f'.backup_{timestamp}'
            print(f"Creating backup: {backup_path}")
            shutil.copy2(dst_path, backup_path)

        # Open destination for writing
        dst_conn = open_target(dst_path)
        working_path = dst_path

    try:
        # Capture pre-conversion counts
        print("\n--- Pre-conversion counts ---")
        report_changes(dst_conn, "BEFORE")

        # === Phase 0: Header ===
        print("\n" + "=" * 40)
        print("Phase 0: Header & Metadata")
        convert_header(src_conn, dst_conn)

        # === Phase 1: Airports ===
        airport_lookup = convert_airports(src_conn, dst_conn)

        if not airport_lookup:
            print("\nWARNING: No Chinese airports found in source!")
        else:
            # === Phase 2: Runways ===
            runway_lookup = convert_runways(src_conn, dst_conn, airport_lookup)

            # === Phase 3: Navaids ===
            navaid_lookup = convert_navaids(src_conn, dst_conn, airport_lookup)

            # === Phase 4: Waypoints ===
            waypoint_lookup, terminal_wpt_ids = convert_waypoints(
                src_conn, dst_conn, airport_lookup
            )

            # === Phase 5: Airways ===
            convert_airways(src_conn, dst_conn, waypoint_lookup, navaid_lookup)

            # === Phase 5b: RTE_SEG CSV ===
            if not args.skip_rte and os.path.exists(csv_path):
                print("\n--- Phase 5b: RTE_SEG.csv ---")
                segments = parse_rte_seg(csv_path)
                if segments:
                    resolved = resolve_coordinates(
                        segments, waypoint_lookup, navaid_lookup
                    )
                    merge_rte_seg_to_airways(dst_conn, resolved)
            elif args.skip_rte:
                print("\n--- Phase 5b: RTE_SEG.csv (SKIPPED) ---")
            else:
                print(f"\n--- Phase 5b: RTE_SEG.csv not found at {csv_path} ---")

            # === Phase 6: Localizers ===
            convert_localizers(src_conn, dst_conn, airport_lookup, runway_lookup)

            # === Phase 7: Terminal Procedures ===
            if not args.skip_procedures:
                convert_procedures(
                    src_conn, dst_conn,
                    airport_lookup, runway_lookup,
                    waypoint_lookup, navaid_lookup
                )
            else:
                print("\n=== Phase 7: Terminal Procedures (SKIPPED) ===")

            # === Phase 8: Other tables ===
            convert_holdings(src_conn, dst_conn)
            convert_gls(src_conn, dst_conn, airport_lookup)
            convert_markers(src_conn, dst_conn, airport_lookup)
            convert_grid_mora(src_conn, dst_conn)
            convert_airport_comm(src_conn, dst_conn, airport_lookup)

        # === Phase 9: Empty tables ===
        create_empty_tables(dst_conn, dst_path)

        # === Post-conversion ===
        # Capture post-conversion counts
        print("\n--- Post-conversion counts ---")
        report_changes(dst_conn, "AFTER")

        # Vacuum to optimize
        if not args.dry_run:
            vacuum(dst_conn)

        # Integrity check
        print("\n--- Integrity Check ---")
        check_integrity(dst_conn)

        if not args.dry_run:
            print(f"\nConversion complete! Updated database: {working_path}")
        else:
            print("\nDry run complete. No changes were made.")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        if not args.dry_run and not args.no_backup:
            print(f"You can restore from backup at: {dst_path}.backup_*")
        sys.exit(1)

    finally:
        dst_conn.close()
        src_conn.close()

    print("\nDone.")


if __name__ == '__main__':
    main()

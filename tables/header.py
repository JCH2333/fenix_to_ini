"""
Phase 0: Generate tbl_hdr_header from Fenix config table.
"""

import sqlite3
from datetime import datetime, timezone


def convert_header(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection):
    """
    Update tbl_hdr_header in destination with cycle info from Fenix config.

    If the header table already has a row, update it; otherwise insert.
    Only runs if the header doesn't already exist for this cycle.
    """
    # Read Fenix config
    config = {}
    for row in src_conn.execute("SELECT key, val FROM config"):
        config[row['key']] = row['val']

    cycle_name = config.get('CycleName', '2607')
    cycle = cycle_name[:4]  # "2607n2" → "2607"
    start = config.get('CycleStartDate', '09JUL26')
    end = config.get('CycleEndDate', '05AUG26')
    # Format: DDMMMYYYY → DDbMMbYYYY (no separators, consistent with Navigraph)
    effective = f"{start[:2]}{start[2:5]}{start[5:]}{end[:2]}{end[2:5]}{end[5:]}"

    # Check if header exists
    existing = dst_conn.execute("SELECT COUNT(*) FROM tbl_hdr_header").fetchone()[0]
    if existing > 0:
        print(f"  [tbl_hdr_header] already has {existing} row(s), updating cycle info")
        dst_conn.execute("""
            UPDATE tbl_hdr_header
            SET cycle = ?, effective_fromto = ?, parsed_at = ?
        """, (cycle, effective, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')))
    else:
        dst_conn.execute("""
            INSERT INTO tbl_hdr_header
            (creator, cycle, data_provider, dataset_version, dataset,
             effective_fromto, parsed_at, revision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            'Navigraph',
            cycle,
            'JEPPESEN',
            '2.0.24.1017',
            'NG_FWDFD',
            effective,
            datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ'),
            '001'
        ))
        print(f"  [tbl_hdr_header] created with cycle {cycle}")

    dst_conn.commit()

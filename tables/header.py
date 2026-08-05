"""
Phase 0: Generate tbl_hdr_header from Fenix config table.
"""

import sqlite3
import re


# Fenix date format: DDMMMYY (e.g. '09JUL26')
# Navigraph DFDv2 format: DDMMDDMMYY (e.g. '0907050826')
MONTH_MAP = {
    'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
    'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
    'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12',
}


def split_cycle_name(cycle_name: str) -> tuple[str, str, str]:
    """Return DFDv2 cycle, JSON revision and three-digit header revision."""
    match = re.fullmatch(r"(\d{4})(?:n(\d+))?", cycle_name.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Unsupported Fenix cycle name: {cycle_name}")
    cycle = match.group(1)
    revision_number = int(match.group(2) or "1")
    return cycle, str(revision_number), f"{revision_number:03d}"


def fenix_date_to_parts(date_str: str) -> tuple[str, str, str]:
    """
    Convert Fenix date to Navigraph format parts.

    '09JUL26' -> ('09', '07', '26')
    """
    day = date_str[:2]
    month_abbr = date_str[2:5].upper()
    year = date_str[5:]
    month = MONTH_MAP.get(month_abbr, '01')
    yy = year[-2:] if len(year) >= 2 else year.zfill(2)
    return day, month, yy


def convert_header(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection):
    """
    Update tbl_hdr_header in destination with cycle info from Fenix config.

    Reads CycleName, CycleStartDate, CycleEndDate from Fenix config table
    and writes proper Navigraph DFDv2 header format.
    """
    # Read Fenix config
    config = {}
    for row in src_conn.execute("SELECT key, val FROM config"):
        config[row['key']] = row['val']

    raw_cycle = config.get('CycleName', '2607')
    cycle, revision, header_revision = split_cycle_name(raw_cycle)

    start_raw = config.get('CycleStartDate', '09JUL26')
    end_raw = config.get('CycleEndDate', '05AUG26')

    # Convert to Navigraph DDMMDDMMYY format
    start_d, start_m, start_y = fenix_date_to_parts(start_raw)
    end_d, end_m, end_y = fenix_date_to_parts(end_raw)
    effective = f"{start_d}{start_m}{end_d}{end_m}{end_y}"  # e.g. '0907050826'

    print(f"  Fenix cycle: {raw_cycle} -> DFDv2 {cycle} R{revision} "
          f"({start_raw} - {end_raw})")
    print(f"  Navigraph effective_fromto: {effective}")

    deterministic_parsed_at = (
        f"20{start_y}-{start_m}-{start_d} 00:00:00Z"
    )

    # Check if header exists
    existing = dst_conn.execute(
        "SELECT cycle, parsed_at FROM tbl_hdr_header LIMIT 1"
    ).fetchone()
    if existing:
        parsed_at = (
            existing[1]
            if existing[0] == cycle and existing[1]
            else deterministic_parsed_at
        )
        print(f"  [tbl_hdr_header] updating existing row with cycle {cycle}")
        dst_conn.execute("""
            UPDATE tbl_hdr_header
            SET cycle = ?, effective_fromto = ?, parsed_at = ?, revision = ?
        """, (cycle, effective, parsed_at, header_revision))
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
            deterministic_parsed_at,
            header_revision
        ))
        print(f"  [tbl_hdr_header] created with cycle {cycle}")

    dst_conn.commit()

    # Return cycle info for cycle.json generation
    return {
        'cycle': cycle,
        'revision': revision,
        'start_raw': start_raw,
        'end_raw': end_raw,
        'start_d': start_d,
        'start_m': start_m,
        'start_y': start_y,
        'end_d': end_d,
        'end_m': end_m,
        'end_y': end_y,
    }

"""
Merge module: deduplication and row counting utilities.

Used to report changes and verify that only new Chinese data was added.
"""

import sqlite3


def count_by_prefix(conn: sqlite3.Connection, table: str,
                    id_column: str, prefixes: tuple[str, ...]) -> int:
    """Count rows matching ICAO prefixes in a table."""
    placeholders = ','.join(['?' for _ in prefixes])
    sql = f"""
        SELECT COUNT(*) FROM {table}
        WHERE SUBSTR({id_column}, 1, 2) IN ({placeholders})
           OR {id_column} IN ('OPGT', 'VHHX')
    """
    return conn.execute(sql, list(prefixes)).fetchone()[0]


def report_changes(conn: sqlite3.Connection, title: str):
    """Print a summary of row counts for key tables."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

    tables_to_check = [
        ('tbl_pa_airports', 'airport_identifier', 'Airports'),
        ('tbl_pg_runways', 'airport_identifier', 'Runways'),
        ('tbl_d_vhfnavaids', None, 'VHF Navaids'),
        ('tbl_db_enroute_ndbnavaids', None, 'NDB Navaids'),
        ('tbl_ea_enroute_waypoints', None, 'Enroute Waypoints'),
        ('tbl_pc_terminal_waypoints', None, 'Terminal Waypoints'),
        ('tbl_er_enroute_airways', None, 'Airway Segments'),
        ('tbl_pi_localizers_glideslopes', 'airport_identifier', 'Localizers'),
        ('tbl_pd_sids', 'airport_identifier', 'SIDs'),
        ('tbl_pe_stars', 'airport_identifier', 'STARs'),
        ('tbl_pf_iaps', 'airport_identifier', 'IAPs'),
        ('tbl_ep_holdings', None, 'Holdings'),
        ('tbl_pt_gls', 'airport_identifier', 'GLS'),
        ('tbl_pm_localizer_marker', 'airport_identifier', 'Markers'),
        ('tbl_as_grid_mora', None, 'Grid MORA'),
        ('tbl_pv_airport_communication', 'airport_identifier', 'Airport Comm'),
    ]

    cn_prefixes = ('ZB', 'ZG', 'ZH', 'ZJ', 'ZL', 'ZP', 'ZS', 'ZU', 'ZW', 'ZY')

    for table, id_col, label in tables_to_check:
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if id_col:
            cn = count_by_prefix(conn, table, id_col, cn_prefixes)
            print(f"  {label:25s}: {total:>8d} total, {cn:>6d} Chinese")
        else:
            print(f"  {label:25s}: {total:>8d} rows")

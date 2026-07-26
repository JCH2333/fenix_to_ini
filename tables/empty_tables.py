"""
Phase 9: Create empty tables that exist in iniBuilds but not in Fenix.

These tables are required for iniBuilds compatibility but have no
corresponding Fenix source data.
"""

import sys
import os
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import sqlite3
from db_utils import create_table_from_template  # type: ignore[import-untyped]

# Tables that exist in iniBuilds db.s3db but have NO source in Fenix nd.db3
TABLES_NO_SOURCE = [
    'tbl_pb_gates',                  # Gate positions
    'tbl_tc_cruising_tables',        # Cruising tables
    'tbl_uc_controlled_airspace',    # Controlled airspace
    'tbl_uf_fir_uir',               # FIR/UIR boundaries
    'tbl_ur_restrictive_airspace',   # Restrictive airspace
    'tbl_ps_airport_msa',            # Minimum Safe Altitudes
    'tbl_pp_pathpoint',              # Pathpoint definitions (GLS)
    'tbl_pn_terminal_ndbnavaids',    # Terminal NDB navaids
    'tbl_ev_enroute_communication',  # Enroute communication
    'tbl_eu_enroute_airway_restriction',  # Airway restrictions
]


def create_empty_tables(dst_conn: sqlite3.Connection, ref_db_path: str):
    """
    Create empty tables in target database matching reference schema.

    Only creates tables that don't already exist in the destination.
    """
    print("\n=== Phase 9: Empty Tables ===")

    # Get list of existing tables in destination
    existing_tables = set()
    for row in dst_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ):
        existing_tables.add(row['name'])

    created = 0
    for table in TABLES_NO_SOURCE:
        if table in existing_tables:
            print(f"  [{table}] already exists, skipping")
            continue

        try:
            create_table_from_template(dst_conn, ref_db_path, table)
            created += 1
        except Exception as e:
            print(f"  [{table}] ERROR: {e}")

    print(f"  Created {created} new empty tables")
    return created

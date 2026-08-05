"""
Phase 1: Convert Fenix Airports → iniBuilds tbl_pa_airports.

Filters to Chinese airspace airports only.
Merges into existing db.s3db (skips duplicates).
"""

import sys
import os
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import sqlite3
from mappings import (  # type: ignore[import-untyped]
    get_airport_icao_code, is_cn_airport,
)
from db_utils import batch_upsert  # type: ignore[import-untyped]


# Columns in tbl_pa_airports (from schema analysis)
TBL_PA_COLUMNS = [
    'airport_identifier',      # VARCHAR(4) NOT NULL
    'airport_name',            # VARCHAR(30) NOT NULL
    'airport_ref_latitude',    # FLOAT NOT NULL
    'airport_ref_longitude',   # FLOAT NOT NULL
    'airport_type',            # VARCHAR(1) NOT NULL
    'area_code',               # VARCHAR(3) NOT NULL
    'ata_iata_code',           # VARCHAR(3)
    'city',                    # VARCHAR(24)
    'continent',               # VARCHAR(40)
    'country_3letter',         # VARCHAR(3)
    'country',                 # VARCHAR(40)
    'elevation',               # INT
    'fuel',                    # VARCHAR(14)
    'icao_code',               # VARCHAR(2) NOT NULL
    'ifr_capability',          # VARCHAR(1)
    'longest_runway_surface_code',  # VARCHAR(1) NOT NULL
    'magnetic_variation',      # REAL
    'speed_limit_altitude',    # VARCHAR(5)
    'speed_limit',             # SMALLINT
    'state_2letter',           # VARCHAR(2)
    'state',                   # VARCHAR(50)
    'time_zone',               # VARCHAR(3)
    'transition_altitude',     # INT
    'transition_level',        # INT
]


def convert_airports(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection):
    """
    Convert Chinese airspace airports from Fenix to iniBuilds.

    Uses UPSERT: existing airports get their fields refreshed with the
    latest Fenix data; new airports are inserted.
    """
    print("\n=== Phase 1: Airports ===")

    # Read all Fenix airports
    fenix_rows = src_conn.execute("""
        SELECT ID, Name, ICAO, PrimaryID, Latitude, Longtitude, Elevation,
               TransitionAltitude, TransitionLevel, SpeedLimit, SpeedLimitAltitude
        FROM Airports
        ORDER BY ID
    """).fetchall()

    # Filter Chinese airports
    cn_airports = []
    for row in fenix_rows:
        icao = row['ICAO']
        if icao and is_cn_airport(icao):
            cn_airports.append(row)

    print(f"  Fenix total airports: {len(fenix_rows)}")
    print(f"  Fenix Chinese airports: {len(cn_airports)}")

    # Get existing airport identifiers (for new vs. updated reporting)
    existing = set()
    for row in dst_conn.execute("SELECT airport_identifier FROM tbl_pa_airports"):
        existing.add(row['airport_identifier'])

    print(f"  Existing in target: {len(existing)} total")

    # Build rows for upsert
    upsert_rows = []
    new_count = 0
    updated_count = 0
    for a in cn_airports:
        icao = a['ICAO']
        if icao in existing:
            updated_count += 1
        else:
            new_count += 1

        # Determine area_code: use 'EEU' for Chinese airspace (matches existing data)
        # icao_code is first 2 chars of ICAO
        area_code = 'EEU'
        icao_code = get_airport_icao_code(icao)

        # Airport type: 'C' = civil (default)
        airport_type = 'C'

        # Transition altitude/level: 0 means not applicable → NULL
        trans_alt = a['TransitionAltitude'] if a['TransitionAltitude'] and a['TransitionAltitude'] > 0 else None
        trans_level = a['TransitionLevel'] if a['TransitionLevel'] and a['TransitionLevel'] > 0 else None

        # Speed limit
        speed_limit = a['SpeedLimit'] if a['SpeedLimit'] and a['SpeedLimit'] > 0 else None
        speed_limit_alt = str(a['SpeedLimitAltitude']) if a['SpeedLimitAltitude'] else None

        # Collect runway surfaces for longest_runway_surface_code
        # (handled later by runways, default to 'H' = hard surface)

        row_data = (
            icao,                                # airport_identifier
            (a['Name'] or '')[:30],              # airport_name (truncate to 30)
            a['Latitude'] or 0.0,                # airport_ref_latitude
            a['Longtitude'] or 0.0,              # airport_ref_longitude (note: Fenix typo)
            airport_type,                         # airport_type
            area_code,                            # area_code
            None,                                 # ata_iata_code
            None,                                 # city
            None,                                 # continent
            None,                                 # country_3letter
            None,                                 # country
            a['Elevation'],                      # elevation
            'NNNNNNNNNNNNNN',                    # fuel (all N = not available)
            icao_code,                            # icao_code
            'Y',                                  # ifr_capability
            'H',                                  # longest_runway_surface_code (default hard)
            None,                                 # magnetic_variation
            speed_limit_alt,                      # speed_limit_altitude
            speed_limit,                          # speed_limit
            None,                                 # state_2letter
            None,                                 # state
            None,                                 # time_zone
            trans_alt,                            # transition_altitude
            trans_level,                          # transition_level
        )
        upsert_rows.append(row_data)

    # Batch upsert (insert new, update existing)
    total = batch_upsert(dst_conn, 'tbl_pa_airports', TBL_PA_COLUMNS, upsert_rows,
                         conflict_columns=['airport_identifier'])

    print(f"  新增机场: {new_count}")
    print(f"  更新机场: {updated_count}")
    print(f"  合计处理: {total}")

    # Build lookup: AirportID → ICAO for downstream use
    airport_lookup = {}
    for a in cn_airports:
        airport_lookup[a['ID']] = a['ICAO']

    return airport_lookup

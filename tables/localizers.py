"""
Phase 6: Convert Fenix ILSes → iniBuilds tbl_pi_localizers_glideslopes.
"""

import sys
import os
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import sqlite3
from freq import decode_freq  # type: ignore[import-untyped]


TBL_PI_COLUMNS = [
    'airport_identifier',       # VARCHAR(4) NOT NULL
    'area_code',                # VARCHAR(3)
    'gs_angle',                 # REAL
    'gs_elevation',             # INT
    'gs_latitude',              # FLOAT
    'gs_longitude',             # FLOAT
    'icao_code',                # VARCHAR(2)
    'ils_mls_gls_category',     # VARCHAR(1)
    'llz_bearing',              # REAL
    'llz_frequency',            # REAL
    'llz_identifier',           # VARCHAR(4) NOT NULL
    'llz_latitude',             # FLOAT
    'llz_longitude',            # FLOAT
    'llz_truebearing',          # REAL
    'llz_width',                # REAL
    'runway_identifier',        # VARCHAR(3)
    'station_declination',      # REAL
]


def convert_localizers(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection,
                       airport_lookup: dict[int, str],
                       runway_lookup: dict[int, dict]):
    """
    Convert ILS localizers for Chinese airports.

    Args:
        airport_lookup: Dict mapping AirportID → ICAO
        runway_lookup: Dict mapping RunwayID → {icao, ident, true_heading, ...}
    """
    print("\n=== Phase 6: Localizers/Glideslopes ===")

    # Read all ILSes
    fenix_ils = src_conn.execute("""
        SELECT ID, RunwayID, Freq, GsAngle, Latitude, Longtitude,
               Category, Ident, LocCourse, CrossingHeight, HasDme, Elevation
        FROM ILSes
    """).fetchall()

    # Get existing localizers for dedup
    existing = set()
    for row in dst_conn.execute(
        "SELECT airport_identifier, llz_identifier, runway_identifier "
        "FROM tbl_pi_localizers_glideslopes"
    ):
        existing.add((row['airport_identifier'], row['llz_identifier'],
                      row['runway_identifier']))

    new_rows = []
    skipped = 0

    for ils in fenix_ils:
        rwy_id = ils['RunwayID']
        rwy = runway_lookup.get(rwy_id)

        if not rwy:
            continue  # Not a Chinese runway

        icao = rwy['icao']
        rwy_ident = rwy['ident']
        llz_ident = (ils['Ident'] or '').strip()

        if not llz_ident:
            continue

        dedup_key = (icao, llz_ident, rwy_ident)
        if dedup_key in existing:
            skipped += 1
            continue

        # Decode frequency
        freq = decode_freq(ils['Freq'], None)  # ILS is VHF

        # Glideslope is typically co-located with localizer
        # (but at the touchdown zone, not runway end)
        gs_lat = ils['Latitude']
        gs_lon = ils['Longtitude']

        area_code = 'EEU'
        icao_code = icao[:2]

        # Magnetic variation not available from Fenix ILS directly
        # Use 0 for station declination as default
        mag_var = 0.0

        row_data = (
            icao,                    # airport_identifier
            area_code,               # area_code
            ils['GsAngle'] or 3.0,   # gs_angle
            ils['Elevation'],        # gs_elevation
            gs_lat,                  # gs_latitude
            gs_lon,                  # gs_longitude
            icao_code,               # icao_code
            ils['Category'] or '1',  # ils_mls_gls_category
            ils['LocCourse'] or 0.0, # llz_bearing (magnetic)
            freq,                    # llz_frequency (MHz)
            llz_ident,               # llz_identifier
            ils['Latitude'] or 0.0,  # llz_latitude
            ils['Longtitude'] or 0.0,# llz_longitude
            (ils['LocCourse'] or 0.0) - mag_var,  # llz_truebearing
            5.0,                     # llz_width (standard ILS width)
            rwy_ident,               # runway_identifier
            0.0,                     # station_declination
        )
        new_rows.append(row_data)

    print(f"  Fenix total ILSes: {len(fenix_ils)}")
    print(f"  Chinese ILSes to insert: {len(new_rows)}, skipped: {skipped}")

    from db_utils import batch_insert  # type: ignore[import-untyped]
    batch_insert(dst_conn, 'tbl_pi_localizers_glideslopes', TBL_PI_COLUMNS, new_rows)

    return len(new_rows)

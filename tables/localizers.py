"""
Phase 6: Convert Fenix ILSes → iniBuilds tbl_pi_localizers_glideslopes.
"""

import sys
import os
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import sqlite3
from freq import decode_freq, is_valid_frequency  # type: ignore[import-untyped]
from geomag import get_magnetic_declination, apply_magnetic_variation  # type: ignore[import-untyped]


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

    Uses UPSERT: existing localizers are refreshed with the latest Fenix
    data, new localizers are inserted. Frequencies outside the valid ILS
    band (108-112 MHz) are rejected instead of written with bogus data.
    Magnetic bearing is computed via WMM instead of using true bearing.

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

    # Get existing localizers (for new vs. updated reporting)
    existing = set()
    for row in dst_conn.execute(
        "SELECT airport_identifier, llz_identifier, runway_identifier "
        "FROM tbl_pi_localizers_glideslopes"
    ):
        existing.add((row['airport_identifier'], row['llz_identifier'],
                      row['runway_identifier']))

    upsert_rows = []
    new_count = 0
    updated_count = 0
    freq_rejected = 0
    covered_airports = set()

    for ils in fenix_ils:
        rwy_id = ils['RunwayID']
        rwy = runway_lookup.get(rwy_id)

        if not rwy:
            continue  # Not a Chinese runway

        icao = rwy['icao']
        covered_airports.add(icao)
        rwy_ident = rwy['ident']
        llz_ident = (ils['Ident'] or '').strip()

        if not llz_ident:
            continue

        # Decode frequency
        freq = decode_freq(ils['Freq'], None)  # ILS is VHF

        # Reject implausible ILS frequencies instead of writing bogus data
        if not is_valid_frequency(freq, '8'):
            freq_rejected += 1
            continue

        dedup_key = (icao, llz_ident, rwy_ident)
        if dedup_key in existing:
            updated_count += 1
        else:
            new_count += 1

        # Glideslope is typically co-located with localizer
        # (but at the touchdown zone, not runway end)
        gs_lat = ils['Latitude']
        gs_lon = ils['Longtitude']

        area_code = 'EEU'
        icao_code = icao[:2]

        # Fenix's ILSes.LocCourse is already the MAGNETIC bearing (verified
        # against runway TrueHeading: the delta matches the local WMM
        # declination, e.g. ~-7.9° at ZBAA). So llz_bearing = LocCourse
        # directly, and llz_truebearing is derived by REMOVING the
        # declination (true = magnetic + declination).
        loc_course_mag = ils['LocCourse'] or 0.0
        mag_var = get_magnetic_declination(ils['Latitude'] or 0.0, ils['Longtitude'] or 0.0)
        if mag_var is None:
            mag_var = 0.0
            loc_course_true = loc_course_mag  # Fallback: mag ≈ true when WMM unavailable
        else:
            # true = magnetic + declination (inverse of apply_magnetic_variation)
            loc_course_true = apply_magnetic_variation(loc_course_mag, -mag_var)

        row_data = (
            icao,                    # airport_identifier
            area_code,               # area_code
            ils['GsAngle'] or 3.0,   # gs_angle
            ils['Elevation'],        # gs_elevation
            gs_lat,                  # gs_latitude
            gs_lon,                  # gs_longitude
            icao_code,               # icao_code
            ils['Category'] or '1',  # ils_mls_gls_category
            loc_course_mag,          # llz_bearing (magnetic, direct from Fenix)
            freq,                    # llz_frequency (MHz)
            llz_ident,               # llz_identifier
            ils['Latitude'] or 0.0,  # llz_latitude
            ils['Longtitude'] or 0.0,# llz_longitude
            loc_course_true,         # llz_truebearing (derived via WMM)
            5.0,                     # llz_width (standard ILS width)
            rwy_ident,               # runway_identifier
            mag_var,                 # station_declination
        )
        upsert_rows.append(row_data)

    print(f"  Fenix total ILSes: {len(fenix_ils)}")
    print(f"  频率越界被拒绝: {freq_rejected}")
    print(f"  新增 ILS: {new_count}")
    print(f"  更新 ILS: {updated_count}")

    from db_utils import batch_upsert  # type: ignore[import-untyped]
    airport_icaos = sorted(covered_airports)
    if airport_icaos:
        placeholders = ','.join('?' for _ in airport_icaos)
        dst_conn.execute(
            f"DELETE FROM tbl_pi_localizers_glideslopes "
            f"WHERE airport_identifier IN ({placeholders})",
            airport_icaos,
        )
        dst_conn.commit()
    total = batch_upsert(dst_conn, 'tbl_pi_localizers_glideslopes', TBL_PI_COLUMNS, upsert_rows,
                         conflict_columns=['airport_identifier', 'llz_identifier', 'runway_identifier'])

    return total

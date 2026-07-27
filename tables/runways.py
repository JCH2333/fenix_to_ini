"""
Phase 2: Convert Fenix Runways → iniBuilds tbl_pg_runways.

Joins with Airports for airport_identifier, with ILSes for llz_identifier.
"""

import sys
import os
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import sqlite3
from mappings import map_surface  # type: ignore[import-untyped]
from db_utils import batch_upsert  # type: ignore[import-untyped]
from geomag import get_magnetic_declination, apply_magnetic_variation  # type: ignore[import-untyped]


# Columns in tbl_pg_runways
TBL_PG_COLUMNS = [
    'airport_identifier',           # VARCHAR(4) NOT NULL
    'area_code',                    # VARCHAR(3)
    'displaced_threshold_distance', # SMALLINT
    'icao_code',                    # VARCHAR(2)
    'landing_threshold_elevation',  # INT
    'llz_identifier',               # VARCHAR(4)
    'llz_mls_gls_category',         # VARCHAR(1)
    'part_time_lights',             # VARCHAR(1)
    'runway_gradient',              # REAL
    'runway_identifier',            # VARCHAR(3) NOT NULL
    'runway_latitude',              # FLOAT
    'runway_length',                # REAL NOT NULL
    'runway_lights',                # VARCHAR(1)
    'runway_longitude',             # FLOAT
    'runway_magnetic_bearing',      # REAL
    'runway_true_bearing',          # REAL
    'runway_width',                 # SMALLINT NOT NULL
    'surface_code',                 # VARCHAR(3)
    'threshold_crossing_height',    # INT
    'traffic_pattern_altitude',     # INT
    'traffic_pattern',              # VARCHAR(1)
]


def convert_runways(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection,
                    airport_lookup: dict[int, str]):
    """
    Convert runways for Chinese airports.

    Uses UPSERT: existing runways are refreshed with the latest Fenix
    data, new runways are inserted. Magnetic bearing is computed from
    true bearing using the WMM (via pygeomag) instead of copying true
    bearing directly.

    Args:
        airport_lookup: Dict mapping Fenix AirportID → ICAO code
    """
    print("\n=== Phase 2: Runways ===")

    # Get the set of Chinese airport IDs
    cn_airport_ids = set(airport_lookup.keys())

    # Read all Fenix runways
    fenix_runways = src_conn.execute("""
        SELECT ID, AirportID, Ident, TrueHeading, Length, Width, Surface,
               Latitude, Longtitude, Elevation
        FROM Runways
        ORDER BY ID
    """).fetchall()

    # Read ILS info for llz_identifier lookup
    ils_info = {}
    for row in src_conn.execute("""
        SELECT ID, RunwayID, Freq, GsAngle, Latitude, Longtitude, Category,
               Ident, LocCourse, CrossingHeight, HasDme, Elevation
        FROM ILSes
    """):
        rwy_id = row['RunwayID']
        if rwy_id not in ils_info:
            ils_info[rwy_id] = row

    # Filter Chinese runways
    cn_runways = [r for r in fenix_runways if r['AirportID'] in cn_airport_ids]
    print(f"  Fenix total runways: {len(fenix_runways)}")
    print(f"  Fenix Chinese runways: {len(cn_runways)}")

    # Get existing runways (for new vs. updated reporting)
    existing = set()
    for row in dst_conn.execute(
        "SELECT airport_identifier, runway_identifier FROM tbl_pg_runways"
    ):
        existing.add((row['airport_identifier'], row['runway_identifier']))

    # Build rows
    upsert_rows = []
    new_count = 0
    updated_count = 0
    magvar_fail_count = 0

    for r in cn_runways:
        icao = airport_lookup.get(r['AirportID'])
        if not icao:
            continue

        # Format runway identifier: "03" → "RW03"
        ident = r['Ident'] or ''
        if ident and not ident.upper().startswith('RW'):
            rwy_ident = f"RW{ident.strip()}"
        else:
            rwy_ident = ident.strip()

        key = (icao, rwy_ident)
        if key in existing:
            updated_count += 1
        else:
            new_count += 1

        # Get ILS info
        ils = ils_info.get(r['ID'])
        llz_ident = ils['Ident'] if ils and ils['Ident'] else None
        llz_cat = ils['Category'] if ils and ils['Category'] else None
        tch = None
        if ils and ils['CrossingHeight']:
            try:
                tch = int(ils['CrossingHeight'])
            except (ValueError, TypeError):
                tch = None

        # Surface mapping
        surface = map_surface(r['Surface'])

        # Magnetic bearing = true bearing corrected by WMM magnetic declination
        true_hdg = r['TrueHeading'] or 0.0
        lat = r['Latitude'] or 0.0
        lon = r['Longtitude'] or 0.0
        mag_var = get_magnetic_declination(lat, lon)
        if mag_var is None:
            magvar_fail_count += 1
            mag_bearing = true_hdg  # Fallback: true ≈ mag when WMM unavailable
        else:
            mag_bearing = apply_magnetic_variation(true_hdg, mag_var)

        # Area code and ICAO code
        area_code = 'EEU'
        icao_code = icao[:2]

        row_data = (
            icao,                    # airport_identifier
            area_code,               # area_code
            0,                       # displaced_threshold_distance
            icao_code,               # icao_code
            r['Elevation'],          # landing_threshold_elevation
            llz_ident,               # llz_identifier
            llz_cat,                 # llz_mls_gls_category
            'N',                     # part_time_lights
            0.0,                     # runway_gradient
            rwy_ident,               # runway_identifier
            lat,                     # runway_latitude
            r['Length'] or 0,        # runway_length
            'N',                     # runway_lights (default no lights info)
            lon,                     # runway_longitude
            mag_bearing,             # runway_magnetic_bearing
            true_hdg,                # runway_true_bearing
            r['Width'] or 0,         # runway_width
            surface,                 # surface_code
            tch,                     # threshold_crossing_height
            None,                    # traffic_pattern_altitude
            'L',                     # traffic_pattern (L=left default)
        )
        upsert_rows.append(row_data)

    total = batch_upsert(dst_conn, 'tbl_pg_runways', TBL_PG_COLUMNS, upsert_rows,
                         conflict_columns=['airport_identifier', 'runway_identifier'])

    print(f"  新增跑道: {new_count}")
    print(f"  更新跑道: {updated_count}")
    print(f"  合计处理: {total}")
    if magvar_fail_count:
        print(f"  警告：{magvar_fail_count} 条跑道无法计算磁偏角，磁方位暂用真方位替代")

    # Build lookup: RunwayID → (ICAO, runway_identifier, true_heading)
    runway_lookup = {}
    for r in cn_runways:
        icao = airport_lookup.get(r['AirportID'])
        if icao:
            ident = r['Ident'] or ''
            if ident and not ident.upper().startswith('RW'):
                rwy_ident = f"RW{ident.strip()}"
            else:
                rwy_ident = ident.strip()
            runway_lookup[r['ID']] = {
                'icao': icao,
                'ident': rwy_ident,
                'true_heading': r['TrueHeading'] or 0.0,
                'length': r['Length'] or 0,
                'lat': r['Latitude'],
                'lon': r['Longtitude'],
                'elevation': r['Elevation'],
            }

    return runway_lookup

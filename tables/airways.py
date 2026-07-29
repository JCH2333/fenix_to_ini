"""
Phase 5: Convert Fenix Airways + AirwayLegs → iniBuilds tbl_er_enroute_airways.

Each AirwayLegs row becomes one airway segment row.
Only processes airways that pass through Chinese airspace.
"""

import sys
import os
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import sqlite3
from collections import defaultdict


# Columns for enroute airways
TBL_ER_COLUMNS = [
    'area_code',                    # VARCHAR(3) NOT NULL
    'crusing_table_identifier',     # VARCHAR(2)
    'direction_restriction',        # VARCHAR(1)
    'flightlevel',                  # VARCHAR(1)
    'icao_code',                    # VARCHAR(2)
    'inbound_course',               # REAL
    'inbound_distance',             # REAL
    'maximum_altitude',             # INT
    'minimum_altitude1',            # INT
    'minimum_altitude2',            # INT
    'outbound_course',              # REAL
    'route_identifier_postfix',     # VARCHAR(1)
    'route_identifier',             # VARCHAR(6)
    'route_type',                   # VARCHAR(1)
    'seqno',                        # INT
    'waypoint_description_code',    # VARCHAR(4)
    'waypoint_identifier',          # VARCHAR(5)
    'waypoint_latitude',            # FLOAT
    'waypoint_longitude',           # FLOAT
    'waypoint_ref_table',           # VARCHAR(2)
]

# Chinese airspace bounding box
CN_LAT_MIN, CN_LAT_MAX = 15.0, 55.0
CN_LON_MIN, CN_LON_MAX = 70.0, 140.0


def is_cn_airspace(lat: float, lon: float) -> bool:
    if lat is None or lon is None:
        return False
    return (CN_LAT_MIN <= lat <= CN_LAT_MAX and
            CN_LON_MIN <= lon <= CN_LON_MAX)


def compute_course_and_distance(lat1: float, lon1: float,
                                lat2: float, lon2: float) -> tuple[float, float]:
    """
    Compute initial bearing (course) and distance between two coordinates.
    Uses simple spherical geometry (Haversine + bearing formulas).

    Returns: (course_degrees, distance_nm)
    """
    import math

    if None in (lat1, lon1, lat2, lon2):
        return 0.0, 0.0

    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)

    # Haversine distance
    a = (math.sin((lat2_r - lat1_r) / 2) ** 2 +
         math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    dist_nm = 3440.065 * c

    # Bearing
    y = math.sin(dlon) * math.cos(lat2_r)
    x = (math.cos(lat1_r) * math.sin(lat2_r) -
         math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon))
    bearing = math.degrees(math.atan2(y, x)) % 360

    return bearing, dist_nm


def derive_area_icao(lat: float, lon: float) -> tuple[str, str]:
    """Derive area_code and icao_code from coordinates."""
    area_code = 'EEU'
    if lon < 97:
        icao_code = 'ZW'
    elif lon < 106:
        icao_code = 'ZL'
    elif lon < 114:
        icao_code = 'ZB'
    elif lon < 120:
        icao_code = 'ZS'
    elif lon < 128:
        icao_code = 'ZY'
    else:
        icao_code = 'ZY'
    return area_code, icao_code


def convert_airways(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection,
                    waypoint_lookup: dict[int, dict],
                    navaid_lookup: dict[int, dict]):
    """
    Convert airways to iniBuilds format.

    Args:
        waypoint_lookup: Dict mapping WaypointID → {ident, lat, lon, name}
        navaid_lookup: Dict mapping NavaidID → {ident, lat, lon, type, freq, name}
    """
    print("\n=== Phase 5: Airways ===")

    # Load all airways
    airways = {}
    for row in src_conn.execute("SELECT ID, Ident FROM Airways"):
        airways[row['ID']] = row['Ident']

    # Load airway legs
    airway_legs = src_conn.execute("""
        SELECT ID, AirwayID, Level, Waypoint1ID, Waypoint2ID, IsStart, IsEnd
        FROM AirwayLegs
        ORDER BY AirwayID, ID
    """).fetchall()

    print(f"  Fenix airways: {len(airways)}")
    print(f"  Fenix airway legs: {len(airway_legs)}")

    # Group legs by airway
    legs_by_airway = defaultdict(list)
    for leg in airway_legs:
        legs_by_airway[leg['AirwayID']].append(leg)

    # Build airway route sequences
    # Fenix airway legs are point-to-point segments.
    # We need to build ordered waypoint sequences for each airway.
    airway_routes = {}
    unrepresentable_routes = 0

    for airway_id, legs in legs_by_airway.items():
        airway_ident = (airways.get(airway_id) or '').strip()
        if not airway_ident:
            continue

        # AS346 stores route identifiers in a five-character fixed buffer.
        if airway_ident.startswith('XX') or len(airway_ident) > 5:
            if len(airway_ident) > 5:
                unrepresentable_routes += 1
            continue

        # Build point sequence from start to end
        # Find start leg
        start_leg = None
        for leg in legs:
            if leg['IsStart']:
                start_leg = leg
                break

        if not start_leg:
            continue

        # Follow the chain
        sequence = []
        current_leg = start_leg
        visited = set()

        while current_leg and current_leg['ID'] not in visited:
            visited.add(current_leg['ID'])
            wpt1_id = current_leg['Waypoint1ID']
            wpt2_id = current_leg['Waypoint2ID']

            # Add waypoint1 if not already in sequence
            if not sequence or sequence[-1] != wpt1_id:
                sequence.append(wpt1_id)
            sequence.append(wpt2_id)

            # Find next leg starting from waypoint2
            if not current_leg['IsEnd']:
                next_leg = None
                for leg in legs:
                    if (leg['Waypoint1ID'] == wpt2_id and
                            leg['ID'] not in visited):
                        next_leg = leg
                        break
                current_leg = next_leg
            else:
                break

        if sequence:
            airway_routes[airway_ident] = sequence

    print(f"  Airways with valid route sequences: {len(airway_routes)}")
    if unrepresentable_routes:
        print(
            "  目标格式无法表示的超长航路名: "
            f"{unrepresentable_routes}"
        )

    # Collect existing airway data for dedup
    existing_airways = defaultdict(list)
    for row in dst_conn.execute(
        "SELECT route_identifier, waypoint_identifier, waypoint_latitude, "
        "waypoint_longitude FROM tbl_er_enroute_airways"
    ):
        existing_airways[
            (row['route_identifier'], row['waypoint_identifier'])
        ].append((row['waypoint_latitude'], row['waypoint_longitude']))

    # Generate airway rows
    new_rows = []
    total_segments = 0

    for airway_ident, wpt_ids in airway_routes.items():
        # Determine route type from original leg Level
        level = 'H'  # Default high altitude
        route_type = 'H'

        has_cn_point = False
        segments = []
        previous_point = None

        for i, wpt_id in enumerate(wpt_ids):
            # Look up waypoint
            wpt = waypoint_lookup.get(wpt_id)
            if not wpt:
                # Try navaid lookup
                nav = navaid_lookup.get(wpt_id)
                if nav:
                    wpt = {'ident': nav['ident'], 'lat': nav['lat'],
                           'lon': nav['lon'], 'name': nav['name']}

            if not wpt:
                previous_point = None
                continue

            ident = (wpt['ident'] or '').strip()
            lat = wpt['lat']
            lon = wpt['lon']
            if (not ident or len(ident) > 5 or lat is None or lon is None
                    or not (-90 <= lat <= 90) or not (-180 <= lon <= 180)):
                previous_point = None
                continue

            if is_cn_airspace(lat, lon):
                has_cn_point = True

            seqno = (i + 1) * 10  # 10, 20, 30, ...

            # Compute inbound/outbound courses
            inbound_course = None
            outbound_course = None
            inbound_dist = None

            if previous_point is not None:
                prev_lat, prev_lon = previous_point
                inbound_course, inbound_dist = compute_course_and_distance(
                    prev_lat, prev_lon, lat, lon
                )
                if inbound_dist > 1000.0:
                    inbound_course = None
                    inbound_dist = None

            if i < len(wpt_ids) - 1:
                next_id = wpt_ids[i + 1]
                next_wpt = waypoint_lookup.get(next_id)
                if not next_wpt:
                    nav = navaid_lookup.get(next_id)
                    if nav:
                        next_wpt = {'lat': nav['lat'], 'lon': nav['lon']}
                if next_wpt:
                    outbound_course, outbound_distance = compute_course_and_distance(
                        lat, lon, next_wpt['lat'], next_wpt['lon']
                    )
                    if outbound_distance > 1000.0:
                        outbound_course = None

            area_code, icao_code = derive_area_icao(lat, lon)
            wpt_ref_table = 'EA'  # Enroute waypoint

            segments.append((
                area_code, 'XX', None, level, icao_code,
                inbound_course, inbound_dist,
                99999, 6000, None,
                outbound_course, None,
                airway_ident, route_type, seqno,
                'E   ', ident, lat, lon, wpt_ref_table,
            ))
            previous_point = (lat, lon)

        if has_cn_point:
            for seg in segments:
                point_key = (seg[12], seg[16])
                existing_coordinates = existing_airways.get(point_key, ())
                if any(
                    lat is not None and lon is not None
                    and compute_course_and_distance(
                        seg[17], seg[18], lat, lon
                    )[1] < 5.0
                    for lat, lon in existing_coordinates
                ):
                    continue
                new_rows.append(seg)
                existing_airways[point_key].append((seg[17], seg[18]))
            total_segments += len(segments)

    # Reorder to match column order
    rows_ordered = [
        (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9],
         r[10], r[11], r[12], r[13], r[14], r[15], r[16], r[17], r[18], r[19])
        for r in new_rows
    ]

    from db_utils import batch_insert  # type: ignore[import-untyped]
    print(f"  Airway segments to insert: {len(rows_ordered)}")
    batch_insert(dst_conn, 'tbl_er_enroute_airways', TBL_ER_COLUMNS, rows_ordered)

    return airway_routes

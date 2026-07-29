"""
RTE_SEG.csv parser and airway data merger.

Parses the NAIP route segment CSV (GBK encoding, DMS coordinates),
converts to decimal coordinates, resolves against Fenix waypoints,
and generates tbl_er_enroute_airways rows.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sqlite3
import csv
import math
from collections import defaultdict


def parse_dms(coord_str: str) -> float | None:
    """
    Parse DMS coordinate string to decimal degrees.

    Format examples:
        N300000.00 → N 30° 00' 00.00" → 30.0
        N404250.00 → N 40° 42' 50.00" → 40.713889
        E1171655.00 → E 117° 16' 55.00" → 117.281944
        W0731500.00 → W 73° 15' 00.00" → -73.25
    """
    if not coord_str or not coord_str.strip():
        return None

    value = coord_str.strip().upper()
    direction = value[0]
    if direction in ('N', 'S', 'E', 'W'):
        numeric = value[1:]
        degree_digits = 2 if direction in ('N', 'S') else 3
        limit = 90 if direction in ('N', 'S') else 180
        sign = -1 if direction in ('S', 'W') else 1
    else:
        numeric = value
        whole = numeric.partition('.')[0]
        degree_digits = len(whole) - 4
        limit = 180
        sign = 1

    whole, separator, fraction = numeric.partition('.')
    if (degree_digits not in (2, 3)
            or len(whole) != degree_digits + 4
            or not whole.isdigit()
            or (separator and not fraction.isdigit())):
        return None

    degrees = int(whole[:degree_digits])
    minutes = int(whole[degree_digits:degree_digits + 2])
    seconds_text = whole[degree_digits + 2:]
    if separator:
        seconds_text += f'.{fraction}'
    seconds = float(seconds_text)

    if (minutes >= 60 or seconds >= 60 or degrees > limit
            or (degrees == limit and (minutes or seconds))):
        return None

    return sign * (degrees + minutes / 60.0 + seconds / 3600.0)


def parse_rte_seg(csv_path: str) -> list[dict]:
    """
    Parse RTE_SEG.csv and extract airway segment data.

    Returns list of dicts with keys:
        route_ident, start_ident, start_type, start_lat, start_lon,
        end_ident, end_type, end_lat, end_lon, valid_track, mag_track, reverse_track
    """
    print(f"\n=== Parsing RTE_SEG.csv: {csv_path} ===")

    segments = []
    skipped = 0

    with open(csv_path, 'r', encoding='gbk', errors='replace') as f:
        reader = csv.DictReader(f)

        for row in reader:
            # Extract key fields
            route_ident = (row.get('TXT_DESIG') or '').strip()
            if not route_ident or route_ident.startswith('XX'):
                continue

            start_ident = (row.get('CODE_POINT_START') or '').strip()
            end_ident = (row.get('CODE_POINT_END') or '').strip()
            start_type = (row.get('CODE_TYPE_START') or '').strip()
            end_type = (row.get('CODE_TYPE_END') or '').strip()

            start_lat = parse_dms(row.get('GEO_LAT_START_ACCURACY', ''))
            start_lon = parse_dms(row.get('GEO_LONG_START_ACCURACY', ''))
            end_lat = parse_dms(row.get('GEO_LAT_END_ACCURACY', ''))
            end_lon = parse_dms(row.get('GEO_LONG_END_ACCURACY', ''))

            if (None in (start_lat, start_lon, end_lat, end_lon)
                    or not start_ident or not end_ident):
                skipped += 1
                continue

            # Parse track values
            valid_track = None
            mag_track = None
            reverse_track = None
            try:
                vt = row.get('VAL_TRUE_TRACK')
                if vt:
                    valid_track = float(vt)
                mt = row.get('VAL_MAG_TRACK')
                if mt:
                    mag_track = float(mt)
                rt = row.get('VAL_REVERS_TRUE_TRACK')
                if rt:
                    reverse_track = float(rt)
            except (ValueError, TypeError):
                pass

            segments.append({
                'route_ident': route_ident,
                'start_ident': start_ident,
                'start_type': start_type,
                'start_lat': start_lat,
                'start_lon': start_lon,
                'end_ident': end_ident,
                'end_type': end_type,
                'end_lat': end_lat,
                'end_lon': end_lon,
                'valid_track': valid_track,
                'mag_track': mag_track,
                'reverse_track': reverse_track,
            })

    print(f"  Parsed {len(segments)} airway segments, skipped {skipped}")
    return segments


def resolve_coordinates(segments: list[dict],
                        waypoint_lookup: dict[int, dict],
                        navaid_lookup: dict[int, dict]) -> list[dict]:
    """
    Resolve CSV coordinates against Fenix waypoints/navaids.
    If a matching ident exists within 5 NM in the database, use DB coordinates.
    """
    # Build ident → coordinates lookup from waypoints and navaids
    ident_lookup = defaultdict(list)

    for wpt_id, wpt in waypoint_lookup.items():
        ident_lookup[wpt['ident']].append({
            'lat': wpt['lat'], 'lon': wpt['lon'],
            'ref_table': 'EA'
        })

    for nav_id, nav in navaid_lookup.items():
        ident_lookup[nav['ident']].append({
            'lat': nav['lat'], 'lon': nav['lon'],
            'ref_table': 'D '
        })

    resolved = []
    for seg in segments:
        # Resolve start point
        start_lat, start_lon, start_ref = _resolve_point(
            seg['start_ident'], seg['start_lat'], seg['start_lon'],
            ident_lookup
        )
        end_lat, end_lon, end_ref = _resolve_point(
            seg['end_ident'], seg['end_lat'], seg['end_lon'],
            ident_lookup
        )

        if start_lat is None or end_lat is None:
            continue

        resolved.append({
            **seg,
            'start_lat': start_lat,
            'start_lon': start_lon,
            'start_ref': start_ref,
            'end_lat': end_lat,
            'end_lon': end_lon,
            'end_ref': end_ref,
        })

    print(f"  Resolved {len(resolved)} segments after coordinate matching")
    return resolved


def _resolve_point(ident: str, csv_lat: float, csv_lon: float,
                   ident_lookup: dict) -> tuple[float | None, float | None, str]:
    """Resolve a single point, preferring DB coordinates within 5 NM."""
    candidates = ident_lookup.get(ident, [])

    best_dist = float('inf')
    best_lat = csv_lat
    best_lon = csv_lon
    best_ref = 'CS'  # CSV source

    for c in candidates:
        dist = _haversine_nm(csv_lat, csv_lon, c['lat'], c['lon'])
        if dist < 5.0 and dist < best_dist:
            best_dist = dist
            best_lat = c['lat']
            best_lon = c['lon']
            best_ref = c['ref_table']

    return best_lat, best_lon, best_ref


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in nautical miles."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin((lat2_r - lat1_r) / 2) ** 2 +
         math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2)
    return 3440.065 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def compute_course(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute initial bearing in degrees (0-360)."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(lat2_r)
    x = (math.cos(lat1_r) * math.sin(lat2_r) -
         math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon))
    return math.degrees(math.atan2(y, x)) % 360


def compute_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute distance in nautical miles."""
    return _haversine_nm(lat1, lon1, lat2, lon2)


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


def merge_rte_seg_to_airways(dst_conn: sqlite3.Connection,
                             segments: list[dict]):
    """
    Generate and insert airway rows from RTE_SEG segments into tbl_er_enroute_airways.
    """
    print("\n=== Merging RTE_SEG to Airways ===")

    # Group segments by route_ident to build sequences
    from collections import defaultdict
    routes = defaultdict(list)

    for seg in segments:
        rid = seg['route_ident']
        routes[rid].append(seg)

    # Get existing airway data for dedup
    existing = defaultdict(list)
    for row in dst_conn.execute(
        "SELECT route_identifier, waypoint_identifier, waypoint_latitude, "
        "waypoint_longitude FROM tbl_er_enroute_airways"
    ):
        existing[(row['route_identifier'], row['waypoint_identifier'])].append(
            (row['waypoint_latitude'], row['waypoint_longitude'])
        )

    # Generate rows
    new_rows = []
    unrepresentable_routes = 0

    for route_ident, segs in routes.items():
        if len(route_ident) > 5:
            unrepresentable_routes += 1
            continue
        # Sort segments to build sequence
        # Use a simple approach: chain segments where end of one is start of next
        sorted_segs = _build_route_sequence(segs)
        if not sorted_segs:
            continue

        route_type = 'H'  # Default

        for i, seg in enumerate(sorted_segs):
            seqno = (i + 1) * 10
            start_lat = seg['start_lat']
            start_lon = seg['start_lon']
            end_lat = seg['end_lat']
            end_lon = seg['end_lon']

            # Only include segments touching Chinese airspace
            cn_lat_min, cn_lat_max = 15.0, 55.0
            cn_lon_min, cn_lon_max = 70.0, 140.0
            if not (_in_bounds(start_lat, start_lon, cn_lat_min, cn_lat_max,
                               cn_lon_min, cn_lon_max) or
                    _in_bounds(end_lat, end_lon, cn_lat_min, cn_lat_max,
                               cn_lon_min, cn_lon_max)):
                continue

            area_code, icao_code = derive_area_icao(start_lat, start_lon)
            inbound_course = seg.get('reverse_track')
            outbound_course = seg.get('valid_track') or seg.get('mag_track')
            inbound_dist = compute_distance(start_lat, start_lon, end_lat, end_lon)

            if len(seg['start_ident']) > 5:
                continue

            point_key = (route_ident, seg['start_ident'])
            existing_coordinates = existing.get(point_key, ())
            if any(
                lat is not None and lon is not None
                and _haversine_nm(start_lat, start_lon, lat, lon) < 5.0
                for lat, lon in existing_coordinates
            ):
                continue

            new_rows.append((
                area_code,                             # area_code
                'XX',                                   # crusing_table_identifier
                None,                                   # direction_restriction
                'H',                                    # flightlevel
                icao_code,                              # icao_code
                inbound_course,                         # inbound_course
                inbound_dist,                           # inbound_distance
                99999,                                  # maximum_altitude
                6000,                                   # minimum_altitude1
                None,                                   # minimum_altitude2
                outbound_course,                        # outbound_course
                None,                                   # route_identifier_postfix
                route_ident,                            # route_identifier
                route_type,                             # route_type
                seqno,                                  # seqno
                'E   ',                                 # waypoint_description_code
                seg['start_ident'],                     # waypoint_identifier
                start_lat,                              # waypoint_latitude
                start_lon,                              # waypoint_longitude
                seg.get('start_ref', 'EA'),             # waypoint_ref_table
            ))
            existing[point_key].append((start_lat, start_lon))

    from db_utils import batch_insert  # type: ignore[import-untyped]
    from tables.airways import TBL_ER_COLUMNS  # type: ignore[import-untyped]

    print(f"  RTE_SEG airway segments to insert: {len(new_rows)}")
    if unrepresentable_routes:
        print(
            "  目标格式无法表示的超长航路名: "
            f"{unrepresentable_routes}"
        )

    if new_rows:
        batch_insert(dst_conn, 'tbl_er_enroute_airways',
                     TBL_ER_COLUMNS, new_rows)

    return len(new_rows)


def _build_route_sequence(segments: list[dict]) -> list[dict]:
    """Build an ordered sequence of airway segments."""
    if not segments:
        return []

    if len(segments) == 1:
        return segments

    # Build graph: start_ident → segment
    graph = {}
    for seg in segments:
        key = seg['start_ident']
        if key not in graph:
            graph[key] = []
        graph[key].append(seg)

    # Find start (point that is only a start, not an end)
    end_idents = {seg['end_ident'] for seg in segments}
    start_points = [seg for seg in segments
                    if seg['start_ident'] not in end_idents]

    if not start_points:
        # Cyclic - just pick the first
        start_points = [segments[0]]

    # Follow the chain
    ordered = []
    seen = set()
    current = start_points[0]
    while current and current['start_ident'] not in seen:
        seen.add(current['start_ident'])
        ordered.append(current)

        # Find next segment starting from current's end
        next_segs = graph.get(current['end_ident'], [])
        if next_segs:
            current = next_segs[0]
        else:
            break

        if len(seen) > len(segments):
            break  # Safety

    # If we didn't get everything, add remaining
    if len(ordered) < len(segments):
        for seg in segments:
            if seg not in ordered:
                ordered.append(seg)

    return ordered


def _in_bounds(lat: float, lon: float,
               lat_min: float, lat_max: float,
               lon_min: float, lon_max: float) -> bool:
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max

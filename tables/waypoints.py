"""
Phase 4: Convert Fenix Waypoints → iniBuilds enroute and terminal waypoint tables.

Splits waypoints: those referenced by TerminalLegs → tbl_pc_terminal_waypoints,
the rest → tbl_ea_enroute_waypoints (a waypoint can be in both).
"""

import sys
import os
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import sqlite3
from region_lookup import RegionLookup  # type: ignore[import-untyped]


# Columns for enroute waypoints
TBL_EA_COLUMNS = [
    'area_code',                # VARCHAR(3) NOT NULL
    'continent',                # VARCHAR(40)
    'country',                  # VARCHAR(40)
    'datum_code',               # VARCHAR(3)
    'icao_code',                # VARCHAR(2)
    'magnetic_variation',       # REAL
    'waypoint_identifier',      # VARCHAR(5) NOT NULL
    'waypoint_latitude',        # FLOAT NOT NULL
    'waypoint_longitude',       # FLOAT NOT NULL
    'waypoint_name',            # VARCHAR(25) NOT NULL
    'waypoint_type',            # VARCHAR(3) NOT NULL
    'waypoint_usage',           # VARCHAR(2)
]

# Columns for terminal waypoints
TBL_PC_COLUMNS = [
    'area_code',                # VARCHAR(3) NOT NULL
    'continent',                # VARCHAR(40)
    'country',                  # VARCHAR(40)
    'datum_code',               # VARCHAR(3)
    'icao_code',                # VARCHAR(2)
    'magnetic_variation',       # REAL
    'region_code',              # VARCHAR(4) NOT NULL
    'waypoint_identifier',      # VARCHAR(5) NOT NULL
    'waypoint_latitude',        # FLOAT NOT NULL
    'waypoint_longitude',       # FLOAT NOT NULL
    'waypoint_name',            # VARCHAR(25) NOT NULL
    'waypoint_type',            # VARCHAR(3) NOT NULL
]

# Chinese airspace bounding box
CN_LAT_MIN, CN_LAT_MAX = 15.0, 55.0
CN_LON_MIN, CN_LON_MAX = 70.0, 140.0


def is_cn_airspace(lat: float, lon: float) -> bool:
    if lat is None or lon is None:
        return False
    return (CN_LAT_MIN <= lat <= CN_LAT_MAX and
            CN_LON_MIN <= lon <= CN_LON_MAX)


def derive_area_icao(lat: float, lon: float, ident: str = '',
                     region_lookup: 'RegionLookup | None' = None,
                     nearest_apt: str | None = None) -> tuple[str, str, str]:
    """
    Derive area_code, icao_code, and region_code for a waypoint.

    Priority:
    1. Cross-reference against 2607 NAIP CSV FIR data (most accurate)
    2. Fall back to nearest Chinese airport's ICAO prefix
    3. Fall back to a coarse longitude-based bucket (least accurate, last resort)
    """
    area_code = 'EEU'

    # 1. Cross-reference against 2607 CSV FIR data
    if region_lookup is not None:
        icao_code = region_lookup.get_waypoint_icao(ident)
        if icao_code:
            return area_code, icao_code, icao_code

    # 2. Fall back to nearest airport's ICAO prefix
    if nearest_apt and len(nearest_apt) >= 2:
        icao_code = nearest_apt[:2]
        return area_code, icao_code, icao_code

    # 3. Last-resort coarse longitude bucket
    if lon < 97:
        icao_code = 'ZW'
    elif lon < 106:
        icao_code = 'ZL'
    elif lon < 114:
        icao_code = 'ZB'
    elif lon < 120:
        icao_code = 'ZS'
    else:
        icao_code = 'ZY'
    return area_code, icao_code, icao_code


def convert_waypoints(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection,
                      airport_lookup: dict[int, str],
                      region_lookup: 'RegionLookup | None' = None):
    """
    Convert Chinese airspace waypoints to iniBuilds format.

    Uses UPSERT: existing waypoints are refreshed with the latest Fenix
    data, new waypoints are inserted.

    Args:
        airport_lookup: Dict mapping Fenix AirportID → ICAO code
        region_lookup: Optional RegionLookup for accurate FIR-based icao_code
    """
    print("\n=== Phase 4: Waypoints ===")

    if region_lookup is None:
        region_lookup = RegionLookup()

    # Step 1: Get all waypoint IDs referenced in TerminalLegs (for Chinese terminals only)
    # First, get Chinese Terminal IDs
    cn_airport_ids = set(airport_lookup.keys())
    cn_terminal_ids = set()
    for row in src_conn.execute("""
        SELECT ID FROM Terminals WHERE AirportID IN ({})
    """.format(','.join('?' for _ in cn_airport_ids)),
        list(cn_airport_ids)
    ):
        cn_terminal_ids.add(row['ID'])

    # Get endpoint and RF-center waypoint IDs referenced by Chinese terminals.
    terminal_wpt_ids = set()
    terminal_waypoint_airports = {}
    if cn_terminal_ids:
        placeholders = ','.join('?' for _ in cn_terminal_ids)
        for row in src_conn.execute(f"""
            SELECT l.WptID AS WaypointID, t.AirportID
            FROM TerminalLegs l JOIN Terminals t ON t.ID = l.TerminalID
            WHERE l.TerminalID IN ({placeholders})
              AND l.WptID IS NOT NULL AND l.WptID > 0
            UNION
            SELECT l.CenterID AS WaypointID, t.AirportID
            FROM TerminalLegs l JOIN Terminals t ON t.ID = l.TerminalID
            WHERE l.TerminalID IN ({placeholders})
              AND l.CenterID IS NOT NULL AND l.CenterID > 0
        """, list(cn_terminal_ids) * 2):
            terminal_wpt_ids.add(row['WaypointID'])
            owner = airport_lookup.get(row['AirportID'])
            if owner:
                terminal_waypoint_airports.setdefault(
                    row['WaypointID'], set()
                ).add(owner)

    print(f"  Chinese terminal waypoints (from TerminalLegs): {len(terminal_wpt_ids)}")

    # Step 2: Read Fenix waypoints required by the Chinese dataset.  A broad
    # coordinate box is not a valid region boundary: it also covers large
    # parts of Central, South and East Asia.  Only NAIP-designated enroute
    # points belong in EA; procedure-only points belong in PC.
    fenix_waypoints = src_conn.execute("""
        SELECT ID, Ident, Collocated, Name, Latitude, Longtitude, NavaidID
        FROM Waypoints
        ORDER BY ID
    """).fetchall()
    enroute_waypoint_regions = {}
    for waypoint in fenix_waypoints:
        ident = (waypoint['Ident'] or '').strip()
        region = region_lookup.get_waypoint_icao(ident) if ident else None
        if region:
            enroute_waypoint_regions[waypoint['ID']] = region
    selected_waypoints = [
        waypoint for waypoint in fenix_waypoints
        if waypoint['ID'] in enroute_waypoint_regions
        or waypoint['ID'] in terminal_wpt_ids
    ]
    print(f"  Fenix total waypoints: {len(fenix_waypoints)}")
    print(f"  NAIP enroute waypoints: {len(enroute_waypoint_regions)}")
    print(f"  Selected Chinese waypoints: {len(selected_waypoints)}")

    # Chinese airport coordinates for nearest-airport fallback
    cn_airport_coords = {}
    for row in src_conn.execute("SELECT ID, ICAO, Latitude, Longtitude FROM Airports"):
        aid = row['ID']
        if aid in airport_lookup:
            cn_airport_coords[row['ICAO']] = (row['Latitude'], row['Longtitude'])

    # Step 3: Get existing waypoints (for new vs. updated reporting)
    existing_ea = set()
    for row in dst_conn.execute("SELECT waypoint_identifier FROM tbl_ea_enroute_waypoints"):
        existing_ea.add(row['waypoint_identifier'])

    existing_pc = set()
    for row in dst_conn.execute("SELECT waypoint_identifier, region_code FROM tbl_pc_terminal_waypoints"):
        existing_pc.add((row['waypoint_identifier'], row['region_code']))

    # Step 4: Build rows for enroute and terminal waypoints
    ea_rows = []
    pc_rows = []
    waypoint_lookup = {}
    ea_new = 0
    ea_updated = 0
    pc_new = 0
    pc_updated = 0

    for w in selected_waypoints:
        wpt_id = w['ID']
        ident = (w['Ident'] or '').strip()
        lat = w['Latitude'] or 0.0
        lon = w['Longtitude'] or 0.0
        name = (w['Name'] or ident or '')[:25]

        if not ident:
            continue

        # Find nearest Chinese airport for fallback region assignment
        owner_airports = terminal_waypoint_airports.get(wpt_id, set())
        candidates = owner_airports or {
            apt_icao for apt_icao, (apt_lat, apt_lon) in cn_airport_coords.items()
            if abs(lat - apt_lat) < 5 and abs(lon - apt_lon) < 5
        }
        nearest_apt = min(
            candidates,
            key=lambda apt_icao: (
                (lat - cn_airport_coords[apt_icao][0]) ** 2
                + (lon - cn_airport_coords[apt_icao][1]) ** 2
            ),
            default=None,
        )

        area_code, icao_code, region_code = derive_area_icao(lat, lon, ident, region_lookup, nearest_apt)
        waypoint_lookup[wpt_id] = {
            'ident': ident,
            'lat': lat,
            'lon': lon,
            'name': w['Name'] or '',
            'navaid_id': w['NavaidID'],
            'icao_code': icao_code,
            'region_code': region_code,
            'ref_table': ('EA' if wpt_id in enroute_waypoint_regions else 'PC'),
        }

        # Determine waypoint type and usage
        collocated = w['Collocated']
        if collocated == 1:
            wpt_type = 'V  '  # VOR waypoint
        else:
            wpt_type = 'C  '  # Combined/unnamed

        # Determine usage based on whether it's terminal
        is_terminal = wpt_id in terminal_wpt_ids

        if is_terminal:
            usage = 'RB'  # Both enroute and terminal
        else:
            usage = 'RH'  # High enroute only

        # Enroute waypoints listed by the NAIP source.  A terminal-only point
        # must not be promoted into the global EA table.
        # Column order: area_code, continent, country, datum_code, icao_code,
        #               magnetic_variation, waypoint_identifier, waypoint_latitude,
        #               waypoint_longitude, waypoint_name, waypoint_type, waypoint_usage
        if wpt_id in enroute_waypoint_regions:
            if ident in existing_ea:
                ea_updated += 1
            else:
                ea_new += 1
            ea_rows.append((
                area_code,        # area_code
                None,             # continent
                None,             # country
                'WGE',            # datum_code
                icao_code,        # icao_code
                None,             # magnetic_variation
                ident,            # waypoint_identifier
                lat,              # waypoint_latitude
                lon,              # waypoint_longitude
                name,             # waypoint_name
                wpt_type,         # waypoint_type
                usage,            # waypoint_usage
            ))

        # Terminal waypoints
        # Column order: area_code, continent, country, datum_code, icao_code,
        #               magnetic_variation, region_code, waypoint_identifier,
        #               waypoint_latitude, waypoint_longitude, waypoint_name, waypoint_type
        if is_terminal:
            pc_key = (ident, region_code)
            if pc_key in existing_pc:
                pc_updated += 1
            else:
                pc_new += 1
            pc_rows.append((
                area_code,        # area_code
                None,             # continent
                None,             # country
                'WGE',            # datum_code
                icao_code,        # icao_code
                None,             # magnetic_variation
                region_code,      # region_code
                ident,            # waypoint_identifier
                lat,              # waypoint_latitude
                lon,              # waypoint_longitude
                name,             # waypoint_name
                wpt_type,         # waypoint_type
            ))

    from db_utils import batch_merge_by_coordinates  # type: ignore[import-untyped]
    print(f"  航路点: 新增 {ea_new}, 更新 {ea_updated}")
    batch_merge_by_coordinates(
        dst_conn, 'tbl_ea_enroute_waypoints', TBL_EA_COLUMNS, ea_rows,
        'waypoint_identifier', 'waypoint_latitude', 'waypoint_longitude',
    )

    print(f"  终端航路点: 新增 {pc_new}, 更新 {pc_updated}")
    batch_merge_by_coordinates(
        dst_conn, 'tbl_pc_terminal_waypoints', TBL_PC_COLUMNS, pc_rows,
        'waypoint_identifier', 'waypoint_latitude', 'waypoint_longitude',
    )

    return waypoint_lookup, terminal_wpt_ids

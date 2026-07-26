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


def derive_area_icao(lat: float, lon: float) -> tuple[str, str, str]:
    """Derive area_code, icao_code, and region_code from coordinates."""
    area_code = 'EEU'
    if lon < 97:
        icao_code = 'ZW'
        region_code = 'ZW'
    elif lon < 106:
        icao_code = 'ZL'
        region_code = 'ZL'
    elif lon < 114:
        icao_code = 'ZB'
        region_code = 'ZB'
    elif lon < 120:
        icao_code = 'ZS'
        region_code = 'ZS'
    elif lon < 128:
        icao_code = 'ZY'
        region_code = 'ZY'
    else:
        icao_code = 'ZY'
        region_code = 'ZY'
    return area_code, icao_code, region_code


def convert_waypoints(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection,
                      airport_lookup: dict[int, str]):
    """
    Convert Chinese airspace waypoints to iniBuilds format.

    Args:
        airport_lookup: Dict mapping Fenix AirportID → ICAO code
    """
    print("\n=== Phase 4: Waypoints ===")

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

    # Get waypoint IDs referenced by Chinese terminal legs
    terminal_wpt_ids = set()
    if cn_terminal_ids:
        placeholders = ','.join('?' for _ in cn_terminal_ids)
        for row in src_conn.execute(f"""
            SELECT DISTINCT WptID FROM TerminalLegs
            WHERE TerminalID IN ({placeholders}) AND WptID IS NOT NULL AND WptID > 0
        """, list(cn_terminal_ids)):
            terminal_wpt_ids.add(row['WptID'])

    print(f"  Chinese terminal waypoints (from TerminalLegs): {len(terminal_wpt_ids)}")

    # Step 2: Read all Fenix waypoints in Chinese airspace
    fenix_waypoints = src_conn.execute("""
        SELECT ID, Ident, Collocated, Name, Latitude, Longtitude, NavaidID
        FROM Waypoints
        ORDER BY ID
    """).fetchall()

    # Filter by airspace
    cn_waypoints = [w for w in fenix_waypoints
                    if is_cn_airspace(w['Latitude'], w['Longtitude'])]
    print(f"  Fenix total waypoints: {len(fenix_waypoints)}")
    print(f"  Fenix Chinese airspace waypoints: {len(cn_waypoints)}")

    # Build waypoint lookup for downstream use
    waypoint_lookup = {}
    for w in cn_waypoints:
        waypoint_lookup[w['ID']] = {
            'ident': (w['Ident'] or '').strip(),
            'lat': w['Latitude'] or 0.0,
            'lon': w['Longtitude'] or 0.0,
            'name': w['Name'] or '',
        }

    # Step 3: Get existing waypoints for dedup
    existing_ea = set()
    for row in dst_conn.execute("SELECT waypoint_identifier FROM tbl_ea_enroute_waypoints"):
        existing_ea.add(row['waypoint_identifier'])

    existing_pc = set()
    for row in dst_conn.execute("SELECT waypoint_identifier, region_code FROM tbl_pc_terminal_waypoints"):
        existing_pc.add((row['waypoint_identifier'], row['region_code']))

    # Step 4: Build rows for enroute and terminal waypoints
    ea_rows = []
    pc_rows = []
    ea_skipped = 0
    pc_skipped = 0

    for w in cn_waypoints:
        wpt_id = w['ID']
        ident = (w['Ident'] or '').strip()
        lat = w['Latitude'] or 0.0
        lon = w['Longtitude'] or 0.0
        name = (w['Name'] or ident or '')[:25]

        if not ident:
            continue

        area_code, icao_code, region_code = derive_area_icao(lat, lon)

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

        # Enroute waypoints (all Chinese waypoints)
        # Column order: area_code, continent, country, datum_code, icao_code,
        #               magnetic_variation, waypoint_identifier, waypoint_latitude,
        #               waypoint_longitude, waypoint_name, waypoint_type, waypoint_usage
        if ident not in existing_ea:
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
        else:
            ea_skipped += 1

        # Terminal waypoints
        # Column order: area_code, continent, country, datum_code, icao_code,
        #               magnetic_variation, region_code, waypoint_identifier,
        #               waypoint_latitude, waypoint_longitude, waypoint_name, waypoint_type
        if is_terminal:
            pc_key = (ident, region_code)
            if pc_key not in existing_pc:
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
            else:
                pc_skipped += 1

    from db_utils import batch_insert  # type: ignore[import-untyped]
    print(f"  Enroute waypoints to insert: {len(ea_rows)}, skipped: {ea_skipped}")
    batch_insert(dst_conn, 'tbl_ea_enroute_waypoints', TBL_EA_COLUMNS, ea_rows)

    print(f"  Terminal waypoints to insert: {len(pc_rows)}, skipped: {pc_skipped}")
    batch_insert(dst_conn, 'tbl_pc_terminal_waypoints', TBL_PC_COLUMNS, pc_rows)

    return waypoint_lookup, terminal_wpt_ids

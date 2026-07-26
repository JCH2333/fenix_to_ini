"""
Phase 7: Convert Fenix Terminals + TerminalLegs → iniBuilds procedure tables.

This is the most complex conversion:
- Splits by Proc type: 1=STAR, 2=SID, 3=IAP
- Expands ALL transitions to each runway
- Resolves waypoint IDs to coordinates
- Parses altitude constraints
- Handles path terminators, course, distance, speed limits
"""

import sys
import os
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import sqlite3
from collections import defaultdict
from mappings import (  # type: ignore[import-untyped]
    PROC_TO_TABLE, map_path_terminator, parse_altitude
)


# Columns shared by SID and STAR tables (40 cols each)
TBL_PD_COLUMNS = [
    'airport_identifier',           # VARCHAR(4) NOT NULL
    'altitude_description',         # VARCHAR(1)
    'altitude1',                    # INT
    'altitude2',                    # INT
    'arc_radius',                   # REAL
    'area_code',                    # VARCHAR(3)
    'authorization_required',       # VARCHAR(1)
    'center_waypoint_icao_code',    # VARCHAR(2)
    'center_waypoint_latitude',     # FLOAT
    'center_waypoint_longitude',    # FLOAT
    'center_waypoint_ref_table',    # VARCHAR(2)
    'center_waypoint',              # VARCHAR(5)
    'course_flag',                  # VARCHAR(1)
    'course',                       # REAL
    'distance_time',                # REAL
    'path_termination',             # VARCHAR(2) NOT NULL
    'procedure_identifier',         # VARCHAR(6) NOT NULL
    'recommended_navaid_icao_code', # VARCHAR(2)
    'recommended_navaid_latitude',  # FLOAT
    'recommended_navaid_longitude', # FLOAT
    'recommended_navaid_ref_table', # VARCHAR(2)
    'recommended_navaid',           # VARCHAR(4)
    'rho',                          # REAL
    'rnp',                          # REAL
    'route_distance_holding_distance_time', # VARCHAR(1)
    'route_type',                   # VARCHAR(1) NOT NULL
    'seqno',                        # INT NOT NULL
    'speed_limit_description',      # VARCHAR(1)
    'speed_limit',                  # INT
    'theta',                        # REAL
    'transition_altitude',          # INT
    'transition_identifier',        # VARCHAR(5)
    'turn_direction',               # VARCHAR(1)
    'vertical_angle',               # REAL
    'waypoint_description_code',    # VARCHAR(4)
    'waypoint_icao_code',           # VARCHAR(2)
    'waypoint_identifier',          # VARCHAR(5)
    'waypoint_latitude',            # FLOAT
    'waypoint_longitude',           # FLOAT
    'waypoint_ref_table',           # VARCHAR(2)
]

# IAP table has all SID/STAR columns plus extra LNAV/VNAV columns
TBL_PF_COLUMNS = TBL_PD_COLUMNS + [
    'ctl',                          # VARCHAR(1)
    'gnss_fms_indication',          # VARCHAR(1)
    'lnav_authorized_sbas',         # VARCHAR(1)
    'lnav_level_service_name',      # VARCHAR(1)
    'lnav_vnav_authorized_sbas',    # VARCHAR(1)
    'lnav_vnav_level_service_name', # VARCHAR(1)
]


def normalize_runway(rwy: str) -> str:
    """Normalize runway identifier to 'RWxx' format."""
    if not rwy:
        return ''
    rwy = rwy.strip().upper()
    if not rwy.startswith('RW'):
        # Remove leading zeros but keep format RWxx
        rwy_num = rwy.lstrip('0') or '0'
        rwy = f"RW{rwy_num.zfill(2)}"
    return rwy


def convert_procedures(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection,
                       airport_lookup: dict[int, str],
                       runway_lookup: dict[int, dict],
                       waypoint_lookup: dict[int, dict],
                       navaid_lookup: dict[int, dict]):
    """
    Convert terminal procedures for Chinese airports.

    Args:
        airport_lookup: AirportID → ICAO
        runway_lookup: RunwayID → {icao, ident, true_heading, lat, lon, elevation}
        waypoint_lookup: WaypointID → {ident, lat, lon, name}
        navaid_lookup: NavaidID → {ident, lat, lon, type, freq, name}
    """
    print("\n=== Phase 7: Terminal Procedures ===")

    # Filter Chinese terminals
    cn_airport_ids = set(airport_lookup.keys())
    all_terminals = src_conn.execute("""
        SELECT ID, AirportID, Proc, ICAO, FullName, Name, Rwy, RwyID, IlsID
        FROM Terminals
        ORDER BY ID
    """).fetchall()

    cn_terminals = [t for t in all_terminals if t['AirportID'] in cn_airport_ids]
    print(f"  Fenix total terminals: {len(all_terminals)}")
    print(f"  Fenix Chinese terminals: {len(cn_terminals)}")

    # Build terminal ID set for fast lookup
    cn_terminal_ids = {t['ID'] for t in cn_terminals}

    # Load all terminal legs
    all_legs = src_conn.execute("""
        SELECT ID, TerminalID, Type, Transition, TrackCode,
               WptID, WptLat, WptLon, TurnDir,
               NavID, NavLat, NavLon, NavBear, NavDist,
               Course, Distance, Alt, Vnav,
               CenterID, CenterLat, CenterLon, WptDescCode
        FROM TerminalLegs
        ORDER BY TerminalID, ID
    """).fetchall()

    # Load TerminalLegsEx for speed limits
    legs_ex = {}
    for row in src_conn.execute("""
        SELECT ID, IsFlyOver, SpeedLimit, SpeedLimitDescription
        FROM TerminalLegsEx
    """):
        legs_ex[row['ID']] = {
            'is_flyover': row['IsFlyOver'],
            'speed_limit': row['SpeedLimit'],
            'speed_limit_desc': row['SpeedLimitDescription'],
        }

    # Filter legs for Chinese terminals
    cn_legs = [leg for leg in all_legs if leg['TerminalID'] in cn_terminal_ids]
    print(f"  Fenix Chinese terminal legs: {len(cn_legs)}")

    # Group legs by terminal
    legs_by_terminal = defaultdict(list)
    for leg in cn_legs:
        legs_by_terminal[leg['TerminalID']].append(leg)

    # Get existing procedures for dedup
    existing = _load_existing_procedures(dst_conn)

    # Convert each terminal
    sid_rows = []
    star_rows = []
    iap_rows = []
    stats = {'sid': 0, 'star': 0, 'iap': 0, 'skipped': 0}

    for terminal in cn_terminals:
        proc = str(terminal['Proc'])
        table_name = PROC_TO_TABLE.get(proc)
        if not table_name:
            continue

        legs = legs_by_terminal.get(terminal['ID'], [])
        if not legs:
            continue

        icao = airport_lookup.get(terminal['AirportID'], terminal['ICAO'] or '')
        proc_ident = (terminal['Name'] or '').strip()[:6]
        rwy = normalize_runway(terminal['Rwy'] or '')

        # Find all unique transitions and runways for this procedure
        transitions, runways = _collect_transitions_and_runways(legs, terminal, rwy)

        # If no specific runway, use the terminal's runway
        if not runways:
            runways = {rwy} if rwy else {'ALL'}

        # Process each transition+runway combination
        for transition in transitions:
            for runway in runways:
                # Get legs for this transition
                trans_legs = [l for l in legs
                              if l['Transition'] == transition or
                              l['Transition'] == 'ALL']

                if not trans_legs:
                    continue

                # Sort legs by their order in the procedure
                trans_legs.sort(key=lambda l: l['ID'])

                # Generate seqno
                for i, leg in enumerate(trans_legs):
                    seqno = (i + 1) * 10

                    row = _build_procedure_row(
                        leg, icao, proc_ident, transition, runway,
                        seqno, waypoint_lookup, navaid_lookup,
                        legs_ex.get(leg['ID'])
                    )

                    if row is None:
                        continue

                    # Dedup check
                    dedup_key = (
                        icao, proc_ident, transition or '',
                        runway, seqno, row[15]  # path_termination
                    )
                    if dedup_key in existing.get(table_name, set()):
                        stats['skipped'] += 1
                        continue

                    if table_name == 'tbl_pd_sids':
                        sid_rows.append(row)
                        stats['sid'] += 1
                    elif table_name == 'tbl_pe_stars':
                        star_rows.append(row)
                        stats['star'] += 1
                    elif table_name == 'tbl_pf_iaps':
                        # Add IAP-specific columns
                        iap_extra = (None, None, None, None, None, None)
                        iap_rows.append(row + iap_extra)
                        stats['iap'] += 1

    # Insert into target tables
    from db_utils import batch_insert  # type: ignore[import-untyped]

    if sid_rows:
        batch_insert(dst_conn, 'tbl_pd_sids', TBL_PD_COLUMNS, sid_rows)
    if star_rows:
        batch_insert(dst_conn, 'tbl_pe_stars', TBL_PD_COLUMNS, star_rows)
    if iap_rows:
        batch_insert(dst_conn, 'tbl_pf_iaps', TBL_PF_COLUMNS, iap_rows)

    print(f"  SIDs inserted: {stats['sid']}")
    print(f"  STARs inserted: {stats['star']}")
    print(f"  IAPs inserted: {stats['iap']}")
    print(f"  Skipped (existing): {stats['skipped']}")


def _collect_transitions_and_runways(legs: list, terminal, default_rwy: str) -> tuple[set, set]:
    """Collect unique transitions and runways from leg data."""
    transitions = set()
    runways = set()

    for leg in legs:
        trans = (leg['Transition'] or '').strip()
        if trans and trans != 'ALL':
            transitions.add(trans)

            # Check if transition is a runway reference
            if trans.upper().startswith('RW'):
                runways.add(trans.upper())
            elif len(trans) <= 3 and trans.isdigit():
                runways.add(f"RW{trans.zfill(2)}")

    # If no specific transitions found, use ALL
    if not transitions:
        transitions = {'ALL'}

    # If no runways found, use terminal's runway
    if not runways and default_rwy:
        runways = {default_rwy}

    return transitions, runways


def _build_procedure_row(leg, icao: str, proc_ident: str,
                         transition: str, runway: str, seqno: int,
                         waypoint_lookup: dict, navaid_lookup: dict,
                         leg_ex: dict | None) -> tuple | None:
    """Build a single procedure row from a TerminalLeg."""

    # Resolve waypoint
    wpt_id = leg['WptID']
    wpt_lat = leg['WptLat']
    wpt_lon = leg['WptLon']
    wpt_ident = None
    wpt_ref_table = None

    if wpt_id and wpt_id > 0:
        wpt = waypoint_lookup.get(wpt_id)
        if wpt:
            wpt_ident = wpt['ident']
            wpt_lat = wpt['lat'] if wpt_lat is None or wpt_lat == 0 else wpt_lat
            wpt_lon = wpt['lon'] if wpt_lon is None or wpt_lon == 0 else wpt_lon
            wpt_ref_table = 'PC'

    if not wpt_ident and not (wpt_lat and wpt_lon):
        # Skip legs without any waypoint reference
        return None

    # Resolve recommended navaid
    nav_id = leg['NavID']
    nav_ident = None
    nav_lat = leg['NavLat']
    nav_lon = leg['NavLon']
    nav_ref = None

    if nav_id and nav_id > 0:
        nav = navaid_lookup.get(nav_id)
        if nav:
            nav_ident = nav['ident']
            nav_lat = nav['lat'] if nav_lat is None or nav_lat == 0 else nav_lat
            nav_lon = nav['lon'] if nav_lon is None or nav_lon == 0 else nav_lon
            nav_ref = 'D '

    # Resolve center waypoint (for RF legs)
    center_id = leg['CenterID']
    center_ident = None
    center_lat = leg['CenterLat']
    center_lon = leg['CenterLon']
    center_ref = None

    if center_id and center_id > 0:
        wpt = waypoint_lookup.get(center_id)
        if wpt:
            center_ident = wpt['ident']
            center_lat = wpt['lat'] if center_lat is None or center_lat == 0 else center_lat
            center_lon = wpt['lon'] if center_lon is None or center_lon == 0 else center_lon
            center_ref = 'PC'

    # Path termination
    path_term = map_path_terminator(leg['Type'] or 'IF')

    # Altitude
    alt1, alt2, alt_desc = parse_altitude(leg['Alt'])

    # Speed limit
    speed_limit = None
    speed_limit_desc = None
    if leg_ex:
        speed_limit = leg_ex.get('speed_limit')
        speed_limit_desc = leg_ex.get('speed_limit_desc')

    # Area/ICAO codes
    area_code = 'EEU'
    icao_code = icao[:2] if icao else 'ZB'

    # Turn direction
    turn_dir = (leg['TurnDir'] or '').strip() or None

    # Course
    course = leg['Course'] if leg['Course'] and leg['Course'] != 0 else None

    # Distance/Time
    dist_time = leg['Distance'] if leg['Distance'] and leg['Distance'] != 0 else None

    # VNAV
    vnav = leg['Vnav'] if leg['Vnav'] and leg['Vnav'] != 0 else None

    # Route type: 1=primary, 2=secondary, 3=alternate
    route_type = '1'

    # Arc radius (for AF legs)
    arc_radius = leg['NavDist'] if path_term == 'AF' and leg['NavDist'] else None

    # RNP from waypoint description code
    rnp = None
    if leg['WptDescCode']:
        try:
            rnp_val = float(leg['WptDescCode'])
            if rnp_val > 0:
                rnp = rnp_val
        except (ValueError, TypeError):
            pass

    row = (
        icao,                           # airport_identifier
        alt_desc,                       # altitude_description
        alt1,                           # altitude1
        alt2,                           # altitude2
        arc_radius,                     # arc_radius
        area_code,                      # area_code
        None,                           # authorization_required
        icao_code,                      # center_waypoint_icao_code
        center_lat,                     # center_waypoint_latitude
        center_lon,                     # center_waypoint_longitude
        center_ref,                     # center_waypoint_ref_table
        center_ident,                   # center_waypoint
        None,                           # course_flag
        course,                         # course
        dist_time,                      # distance_time
        path_term,                      # path_termination
        proc_ident,                     # procedure_identifier
        icao_code,                      # recommended_navaid_icao_code
        nav_lat,                        # recommended_navaid_latitude
        nav_lon,                        # recommended_navaid_longitude
        nav_ref,                        # recommended_navaid_ref_table
        nav_ident,                      # recommended_navaid
        None,                           # rho
        rnp,                            # rnp
        None,                           # route_distance_holding_distance_time
        route_type,                     # route_type
        seqno,                          # seqno
        speed_limit_desc,               # speed_limit_description
        speed_limit,                    # speed_limit
        None,                           # theta
        None,                           # transition_altitude
        transition or None,             # transition_identifier
        turn_dir,                       # turn_direction
        vnav,                           # vertical_angle
        leg['WptDescCode'] or None,     # waypoint_description_code
        icao_code,                      # waypoint_icao_code
        wpt_ident,                      # waypoint_identifier
        wpt_lat,                        # waypoint_latitude
        wpt_lon,                        # waypoint_longitude
        wpt_ref_table,                  # waypoint_ref_table
    )

    return row


def _load_existing_procedures(dst_conn: sqlite3.Connection) -> dict:
    """Load existing procedure identifiers for dedup."""
    existing = {}
    for table in ['tbl_pd_sids', 'tbl_pe_stars', 'tbl_pf_iaps']:
        keys = set()
        try:
            rows = dst_conn.execute(f"""
                SELECT airport_identifier, procedure_identifier,
                       transition_identifier, runway_identifier, seqno, path_termination
                FROM {table}
            """).fetchall()
            for r in rows:
                keys.add((
                    r['airport_identifier'],
                    r['procedure_identifier'],
                    r['transition_identifier'] or '',
                    r['runway_identifier'] or '',
                    r['seqno'],
                    r['path_termination'],
                ))
        except sqlite3.OperationalError:
            pass
        existing[table] = keys
    return existing

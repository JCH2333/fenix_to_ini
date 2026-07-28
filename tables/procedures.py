"""
Phase 7: Convert Fenix Terminals + TerminalLegs → iniBuilds procedure tables.

This is the most complex conversion:
- Splits by Proc type: 1=STAR, 2=SID, 3=IAP
- Preserves independent transition, common, final, and missed-approach sections
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

    # Convert each terminal
    sid_rows = []
    star_rows = []
    iap_rows = []
    stats = {'sid': 0, 'star': 0, 'iap': 0}
    covered_airports = defaultdict(set)

    for terminal in cn_terminals:
        proc = str(terminal['Proc'])
        table_name = PROC_TO_TABLE.get(proc)
        if not table_name:
            continue

        legs = legs_by_terminal.get(terminal['ID'], [])
        if not legs:
            continue

        icao = airport_lookup.get(terminal['AirportID'], terminal['ICAO'] or '')
        covered_airports[table_name].add(icao)
        proc_ident = (terminal['Name'] or '').strip()[:6]
        rwy = normalize_runway(terminal['Rwy'] or '')

        # Each (route type, transition) pair is an independent DFD section.
        sections = defaultdict(list)
        for leg in legs:
            transition = normalize_transition(leg['Transition'], rwy)
            route_type = derive_route_type(proc, leg['Type'], transition, proc_ident)
            sections[(route_type, transition)].append(leg)

        for (route_type, transition), section_legs in sections.items():
            section_legs.sort(key=lambda leg: leg['ID'])

            # Generate seqno
            for i, leg in enumerate(section_legs):
                seqno = (i + 1) * 10

                row = _build_procedure_row(
                    leg, icao, proc_ident, transition, rwy, route_type,
                    seqno, waypoint_lookup, navaid_lookup,
                    legs_ex.get(leg['ID'])
                )

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

    replaced = _delete_airport_procedures(dst_conn, covered_airports)

    # Insert into target tables
    from db_utils import batch_insert  # type: ignore[import-untyped]

    if sid_rows:
        batch_insert(dst_conn, 'tbl_pd_sids', TBL_PD_COLUMNS, sid_rows)
    if star_rows:
        batch_insert(dst_conn, 'tbl_pe_stars', TBL_PD_COLUMNS, star_rows)
    if iap_rows:
        target_columns = {
            row[1] for row in dst_conn.execute("PRAGMA table_info(tbl_pf_iaps)")
        }
        missing_core = [column for column in TBL_PD_COLUMNS
                        if column not in target_columns]
        if missing_core:
            raise sqlite3.OperationalError(
                "tbl_pf_iaps is missing required columns: "
                + ", ".join(missing_core)
            )
        iap_columns = [column for column in TBL_PF_COLUMNS
                       if column in target_columns]
        column_indexes = [TBL_PF_COLUMNS.index(column) for column in iap_columns]
        projected_rows = [
            tuple(row[index] for index in column_indexes)
            for row in iap_rows
        ]
        batch_insert(dst_conn, 'tbl_pf_iaps', iap_columns, projected_rows)

    print(f"  新增 SID: {stats['sid']}")
    print(f"  新增 STAR: {stats['star']}")
    print(f"  新增 IAP: {stats['iap']}")
    print(f"  已替换中国程序记录: {sum(replaced.values())}")


def normalize_transition(transition: str | None, runway: str) -> str | None:
    """Convert Fenix transition values to DFDv2 identifiers."""
    value = (transition or '').strip()
    if not value:
        return None
    if value == 'ALL':
        return normalize_runway(runway) if runway else None
    return value


def derive_route_type(proc: str, fenix_type: str | None,
                      transition: str | None, proc_ident: str) -> str:
    """Map Fenix TerminalLegs.Type to the DFDv2 route_type."""
    route_type = (fenix_type or '').strip()
    if proc == '3' and route_type == '0':
        if transition:
            return 'A'
        inferred = proc_ident[:1].upper()
        return inferred if inferred in {'D', 'G', 'I', 'L', 'N', 'Q', 'R'} else '1'
    return route_type or '1'


def _delete_airport_procedures(dst_conn: sqlite3.Connection,
                               airports_by_table: dict[str, set[str]]) -> dict[str, int]:
    """Delete procedures only for airports that will be rebuilt."""
    removed = {}
    for table in ('tbl_pd_sids', 'tbl_pe_stars', 'tbl_pf_iaps'):
        values = sorted(airports_by_table.get(table, set()))
        if not values:
            removed[table] = 0
            continue
        placeholders = ','.join('?' for _ in values)
        before = dst_conn.total_changes
        dst_conn.execute(
            f"DELETE FROM {table} WHERE airport_identifier IN ({placeholders})",
            values,
        )
        removed[table] = dst_conn.total_changes - before
    dst_conn.commit()
    return removed


def _build_procedure_row(leg, icao: str, proc_ident: str,
                         transition: str | None, runway: str, route_type: str,
                         seqno: int,
                         waypoint_lookup: dict, navaid_lookup: dict,
                         leg_ex: dict | None) -> tuple:
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
            navaid_id = wpt.get('navaid_id')
            collocated_navaid = navaid_lookup.get(navaid_id) if navaid_id else None
            wpt_ident = collocated_navaid['ident'] if collocated_navaid else wpt['ident']
            wpt_lat = wpt['lat'] if wpt_lat is None or wpt_lat == 0 else wpt_lat
            wpt_lon = wpt['lon'] if wpt_lon is None or wpt_lon == 0 else wpt_lon
            wpt_ref_table = 'D ' if collocated_navaid else 'PC'

    if not wpt_ident and (leg['Alt'] or '').strip().upper() == 'MAP' and runway:
        wpt_ident = normalize_runway(runway)
        wpt_ref_table = 'PG'

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

    # TrackCode is the ARINC path terminator. Type is the route section.
    track_code = (leg['TrackCode'] or '').strip()
    if track_code.startswith('RWY'):
        track_code = 'TF'
    path_term = map_path_terminator(track_code or 'IF')

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
        transition,                     # transition_identifier
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

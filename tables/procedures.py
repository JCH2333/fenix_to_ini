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
import math
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

WAYPOINT_LATITUDE_INDEX = TBL_PD_COLUMNS.index('waypoint_latitude')
WAYPOINT_LONGITUDE_INDEX = TBL_PD_COLUMNS.index('waypoint_longitude')


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
                       navaid_lookup: dict[int, dict],
                       procedure_metadata=None):
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

    ils_lookup = {}
    for row in src_conn.execute("""
        SELECT ID, Ident, Latitude, Longtitude
        FROM ILSes
    """):
        ident = (row['Ident'] or '').strip()
        if ident:
            ils_lookup[row['ID']] = {
                'ident': ident,
                'lat': row['Latitude'],
                'lon': row['Longtitude'],
                'ref_table': 'PI',
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
    rnp_ar_runway_points = []

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
        has_rf = any(
            map_path_terminator((leg['TrackCode'] or '').strip()) == 'RF'
            for leg in legs
        )
        is_rnp_ar = bool(
            proc == '3'
            and (
                (has_rf and proc_ident.upper().startswith('R'))
                or (
                    procedure_metadata is not None
                    and procedure_metadata.is_rnp_ar(
                        icao, rwy, proc_ident, has_ils=bool(terminal['IlsID'])
                    )
                )
            )
        )
        uses_rnp_ar_runway_fix = (
            is_rnp_ar and proc_ident.upper().startswith('R')
        )

        # Each (route type, transition) pair is an independent DFD section.
        sections = defaultdict(list)
        for leg in legs:
            transition = normalize_transition(leg['Transition'], rwy)
            route_type = derive_route_type(proc, leg['Type'], transition, proc_ident)
            sections[(route_type, transition)].append(leg)

        section_endpoints = []
        for (route_type, transition), section_legs in sections.items():
            section_legs.sort(key=lambda leg: leg['ID'])
            section_legs = _deduplicate_section_legs(section_legs)
            map_index = _map_leg_index(section_legs)
            rnp_ar_faf_index = (
                _rnp_ar_faf_leg_index(section_legs, map_index)
                if is_rnp_ar else None
            )
            vertical_overrides = (
                _rnp_ar_vertical_overrides(
                    section_legs, rnp_ar_faf_index, map_index
                ) if is_rnp_ar else {}
            )
            first_path_term = map_path_terminator(
                (section_legs[0]['TrackCode'] or '').strip() or 'IF'
            )
            previous_waypoint_coords = None
            if first_path_term == 'RF':
                previous_waypoint_coords = _shared_section_endpoint(
                    section_endpoints
                )

            # Generate seqno
            for i, leg in enumerate(section_legs):
                seqno = (i + 1) * 10

                row = _build_procedure_row(
                    leg, icao, proc_ident, transition, rwy, route_type,
                    seqno, waypoint_lookup, navaid_lookup,
                    legs_ex.get(leg['ID']), previous_waypoint_coords,
                    (
                        ils_lookup.get(terminal['IlsID'])
                        if route_type == 'I'
                        and (map_index is None or i <= map_index)
                        else None
                    ),
                    is_rnp_ar,
                    uses_rnp_ar_runway_fix,
                    i == len(section_legs) - 1,
                    i == rnp_ar_faf_index,
                    vertical_overrides.get(leg['ID']),
                )
                current_waypoint_coords = (
                    row[WAYPOINT_LATITUDE_INDEX],
                    row[WAYPOINT_LONGITUDE_INDEX],
                )
                if _valid_coordinates(current_waypoint_coords):
                    previous_waypoint_coords = current_waypoint_coords
                if (
                    uses_rnp_ar_runway_fix
                    and (leg['Alt'] or '').strip().upper() == 'MAP'
                    and row[TBL_PD_COLUMNS.index('waypoint_identifier')]
                    and _valid_coordinates(current_waypoint_coords)
                ):
                    rnp_ar_runway_points.append({
                        'airport_identifier': icao,
                        'waypoint_identifier': row[
                            TBL_PD_COLUMNS.index('waypoint_identifier')
                        ],
                        'latitude': current_waypoint_coords[0],
                        'longitude': current_waypoint_coords[1],
                    })

                if table_name == 'tbl_pd_sids':
                    sid_rows.append(row)
                    stats['sid'] += 1
                elif table_name == 'tbl_pe_stars':
                    star_rows.append(row)
                    stats['star'] += 1
                elif table_name == 'tbl_pf_iaps':
                    # Add IAP-specific columns
                    # Aerosoft AS346 includes ctl and uses explicit N/Y values
                    # for every row. Fenix has no equivalent field, so use the
                    # official false/default value. A340 omits ctl and the
                    # target-column projection below drops it automatically.
                    iap_extra = ('N', None, None, None, None, None)
                    iap_rows.append(row + iap_extra)
                    stats['iap'] += 1

            if _valid_coordinates(previous_waypoint_coords):
                section_endpoints.append(previous_waypoint_coords)

    _merge_rnp_ar_runway_points(dst_conn, rnp_ar_runway_points)
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


def _deduplicate_section_legs(section_legs):
    """Remove exact consecutive duplicates found in some Fenix procedures."""
    result = []
    previous_signature = None
    for leg in section_legs:
        signature = tuple(leg[key] for key in leg.keys() if key != 'ID')
        if signature != previous_signature:
            result.append(leg)
        previous_signature = signature
    return result


def _shared_section_endpoint(section_endpoints):
    """Return the endpoint shared by all preceding transition sections."""
    if not section_endpoints:
        return None
    first = section_endpoints[0]
    if all(endpoint == first for endpoint in section_endpoints[1:]):
        return first
    return None


def _valid_coordinates(coordinates):
    return bool(
        coordinates
        and all(value is not None and math.isfinite(value)
                for value in coordinates)
    )


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
                          leg_ex: dict | None,
                          previous_waypoint_coords: tuple[float | None,
                                                          float | None] | None,
                          procedure_ils: dict | None = None,
                          is_rnp_ar: bool = False,
                          uses_rnp_ar_runway_fix: bool = False,
                          is_last_in_section: bool = False,
                          is_rnp_ar_faf: bool = False,
                          vertical_angle_override: float | None = None,
                          ) -> tuple:
    """Build a single procedure row from a TerminalLeg."""

    # Resolve waypoint
    wpt_id = leg['WptID']
    wpt_lat = leg['WptLat']
    wpt_lon = leg['WptLon']
    wpt_ident = None
    wpt_ref_table = None
    waypoint_icao_code = None

    if wpt_id and wpt_id > 0:
        wpt = waypoint_lookup.get(wpt_id)
        if wpt:
            navaid_id = wpt.get('navaid_id')
            collocated_navaid = navaid_lookup.get(navaid_id) if navaid_id else None
            wpt_ident = collocated_navaid['ident'] if collocated_navaid else wpt['ident']
            wpt_lat = wpt['lat']
            wpt_lon = wpt['lon']
            wpt_ref_table = (
                'D ' if collocated_navaid else wpt.get('ref_table', 'PC')
            )
            waypoint_icao_code = wpt.get('icao_code')

    if not wpt_ident and (leg['Alt'] or '').strip().upper() == 'MAP' and runway:
        wpt_ident = normalize_runway(runway)
        wpt_ref_table = 'PC' if uses_rnp_ar_runway_fix else 'PG'

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

    if nav_ident is None and procedure_ils:
        nav_ident = procedure_ils['ident']
        nav_lat = procedure_ils['lat']
        nav_lon = procedure_ils['lon']
        nav_ref = procedure_ils['ref_table']

    # Resolve center waypoint (for RF legs)
    center_id = leg['CenterID']
    center_ident = None
    center_lat = leg['CenterLat']
    center_lon = leg['CenterLon']
    center_ref = None
    center_icao_code = None

    if center_id and center_id > 0:
        wpt = waypoint_lookup.get(center_id)
        if wpt:
            center_ident = wpt['ident']
            center_lat = wpt['lat']
            center_lon = wpt['lon']
            center_ref = wpt.get('ref_table', 'PC')
            center_icao_code = wpt.get('icao_code')

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
    raw_vnav = vertical_angle_override or leg['Vnav']
    vnav = -abs(raw_vnav) if raw_vnav and raw_vnav != 0 else None

    # Arc geometry. ToLiss does not read the center-waypoint columns for RF
    # legs, so radius and travelled arc distance must be populated explicitly.
    arc_radius = leg['NavDist'] if path_term == 'AF' and leg['NavDist'] else None
    route_distance_type = None
    if path_term == 'RF':
        rf_geometry = _derive_rf_geometry(
            previous_waypoint_coords,
            (wpt_lat, wpt_lon),
            (center_lat, center_lon),
            turn_dir,
        )
        if rf_geometry:
            arc_radius, dist_time, turn_dir = rf_geometry
            route_distance_type = 'D'

    # RNP from waypoint description code
    rnp = None
    raw_waypoint_description = leg['WptDescCode']
    waypoint_description = None
    if raw_waypoint_description and str(raw_waypoint_description).strip():
        waypoint_description = str(raw_waypoint_description)[:4].ljust(4)
        try:
            rnp_val = float(raw_waypoint_description)
            if rnp_val > 0:
                rnp = rnp_val
        except (ValueError, TypeError):
            pass
    if is_rnp_ar:
        waypoint_description = _normalize_rnp_ar_description(
            raw_waypoint_description, wpt_ref_table, is_last_in_section,
            is_rnp_ar_faf,
        )

    center_icao_code = (center_icao_code or icao_code) if center_ident else None
    recommended_navaid_icao_code = icao_code if nav_ident else None
    waypoint_icao_code = (waypoint_icao_code or icao_code) if wpt_ident else None

    row = (
        icao,                           # airport_identifier
        alt_desc,                       # altitude_description
        alt1,                           # altitude1
        alt2,                           # altitude2
        arc_radius,                     # arc_radius
        area_code,                      # area_code
        'Y' if is_rnp_ar else None,     # authorization_required
        center_icao_code,               # center_waypoint_icao_code
        center_lat,                     # center_waypoint_latitude
        center_lon,                     # center_waypoint_longitude
        center_ref,                     # center_waypoint_ref_table
        center_ident,                   # center_waypoint
        None,                           # course_flag
        course,                         # course
        dist_time,                      # distance_time
        path_term,                      # path_termination
        proc_ident,                     # procedure_identifier
        recommended_navaid_icao_code,   # recommended_navaid_icao_code
        nav_lat,                        # recommended_navaid_latitude
        nav_lon,                        # recommended_navaid_longitude
        nav_ref,                        # recommended_navaid_ref_table
        nav_ident,                      # recommended_navaid
        None,                           # rho
        0.3 if is_rnp_ar else rnp,      # rnp
        route_distance_type,            # route_distance_holding_distance_time
        route_type,                     # route_type
        seqno,                          # seqno
        speed_limit_desc,               # speed_limit_description
        speed_limit,                    # speed_limit
        None,                           # theta
        None,                           # transition_altitude
        transition,                     # transition_identifier
        turn_dir,                       # turn_direction
        vnav,                           # vertical_angle
        waypoint_description,           # waypoint_description_code
        waypoint_icao_code,             # waypoint_icao_code
        wpt_ident,                      # waypoint_identifier
        wpt_lat,                        # waypoint_latitude
        wpt_lon,                        # waypoint_longitude
        wpt_ref_table,                  # waypoint_ref_table
    )

    return row


def _merge_rnp_ar_runway_points(dst_conn, points):
    """Ensure PC runway fixes referenced by RNP AR procedures exist."""
    if not points:
        return
    from db_utils import batch_merge_by_coordinates
    from tables.waypoints import TBL_PC_COLUMNS

    rows = []
    seen = set()
    for point in points:
        airport = point['airport_identifier']
        ident = point['waypoint_identifier']
        latitude = point['latitude']
        longitude = point['longitude']
        key = (airport, ident, round(latitude, 6), round(longitude, 6))
        if key in seen:
            continue
        seen.add(key)
        existing = dst_conn.execute(
            """
            SELECT * FROM tbl_pc_terminal_waypoints
            WHERE region_code=? AND waypoint_identifier=?
            ORDER BY ABS(waypoint_latitude-?) + ABS(waypoint_longitude-?)
            LIMIT 1
            """,
            (airport, ident, latitude, longitude),
        ).fetchone()
        if existing:
            values = {column: existing[column] for column in TBL_PC_COLUMNS}
            values.update(
                waypoint_latitude=latitude,
                waypoint_longitude=longitude,
            )
        else:
            values = {
                'area_code': 'EEU',
                'continent': 'ASIA',
                'country': 'CHINA',
                'datum_code': 'WGE',
                'icao_code': airport[:2],
                'magnetic_variation': None,
                'region_code': airport,
                'waypoint_identifier': ident,
                'waypoint_latitude': latitude,
                'waypoint_longitude': longitude,
                'waypoint_name': ident,
                'waypoint_type': 'W Z',
            }
        rows.append(tuple(values[column] for column in TBL_PC_COLUMNS))
    batch_merge_by_coordinates(
        dst_conn,
        'tbl_pc_terminal_waypoints',
        TBL_PC_COLUMNS,
        rows,
        'waypoint_identifier',
        'waypoint_latitude',
        'waypoint_longitude',
        match_columns=['region_code'],
    )


def _map_leg_index(section_legs) -> int | None:
    for index, leg in enumerate(section_legs):
        if (leg['Alt'] or '').strip().upper() == 'MAP':
            return index
    return None


def _rnp_ar_faf_leg_index(section_legs, map_index) -> int | None:
    if map_index is None:
        return None
    candidates = [
        index for index, leg in enumerate(section_legs[:map_index])
        if 'F' in (leg['WptDescCode'] or '').upper()
    ]
    constrained = [
        index for index in candidates
        if (section_legs[index]['Alt'] or '').strip()
    ]
    if constrained:
        return constrained[-1]
    return candidates[0] if candidates else None


def _rnp_ar_vertical_overrides(section_legs, faf_index,
                               map_index) -> dict[int, float]:
    """Carry the source MAP angle back over the final descent after the FAF."""
    if map_index is None:
        return {}
    angle = section_legs[map_index]['Vnav']
    if not angle:
        return {}
    start_index = faf_index + 1 if faf_index is not None else map_index
    return {
        section_legs[index]['ID']: angle
        for index in range(start_index, map_index + 1)
    }


def _normalize_rnp_ar_description(raw_value, ref_table, is_last, is_faf):
    """Map compact Fenix NAIP codes to four-character DFDv2 codes."""
    if not raw_value or not str(raw_value).strip():
        return None
    value = str(raw_value).strip().upper()
    if is_last and value == 'EE':
        return 'EE H'
    if value == 'EE':
        return 'E   '
    if value == 'EI':
        return 'E  I'
    if value == 'EF':
        return 'E  F' if is_faf else 'E   '
    if value == 'E':
        return 'E   '
    if value == 'V' and ref_table == 'PC':
        return 'E   '
    if value == 'E A':
        return 'E CA' if ref_table == 'EA' else 'E  A'
    if ref_table == 'PC' and value in {'GY M', 'G  M', 'G M'}:
        return 'EY M'
    return str(raw_value)[:4].ljust(4)


def _derive_rf_geometry(previous_waypoint, waypoint, center, turn_direction):
    """Return the ARINC RF radius and arc distance in nautical miles."""
    if not previous_waypoint:
        return None
    coordinates = (*previous_waypoint, *waypoint, *center)
    if any(value is None or not math.isfinite(value) for value in coordinates):
        return None

    previous_lat, previous_lon = previous_waypoint
    waypoint_lat, waypoint_lon = waypoint
    center_lat, center_lon = center
    radius = _great_circle_distance_nm(
        center_lat, center_lon, waypoint_lat, waypoint_lon
    )
    start_radius = _great_circle_distance_nm(
        center_lat, center_lon, previous_lat, previous_lon
    )
    start_bearing = _initial_bearing(
        center_lat, center_lon, previous_lat, previous_lon
    )
    end_bearing = _initial_bearing(
        center_lat, center_lon, waypoint_lat, waypoint_lon
    )
    right_sweep = (end_bearing - start_bearing) % 360.0
    left_sweep = (start_bearing - end_bearing) % 360.0
    if turn_direction == 'R':
        sweep = right_sweep
    elif turn_direction == 'L':
        sweep = left_sweep
    elif right_sweep <= left_sweep:
        turn_direction, sweep = 'R', right_sweep
    else:
        turn_direction, sweep = 'L', left_sweep
    arc_distance = ((start_radius + radius) / 2.0) * math.radians(sweep)
    if radius <= 0.0 or arc_distance <= 0.0:
        return None
    return round(radius, 2), round(arc_distance, 1), turn_direction


def _great_circle_distance_nm(lat1, lon1, lat2, lon2):
    """Calculate spherical great-circle distance in nautical miles."""
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    haversine = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2.0) ** 2
    )
    central_angle = 2.0 * math.asin(min(1.0, math.sqrt(haversine)))
    return 3440.065 * central_angle


def _initial_bearing(lat1, lon1, lat2, lon2):
    """Calculate the clockwise initial bearing in degrees."""
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)
    y = math.sin(delta_lon) * math.cos(lat2_rad)
    x = (
        math.cos(lat1_rad) * math.sin(lat2_rad)
        - math.sin(lat1_rad)
        * math.cos(lat2_rad)
        * math.cos(delta_lon)
    )
    return math.degrees(math.atan2(y, x)) % 360.0

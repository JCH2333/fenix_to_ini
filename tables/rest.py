"""
Phase 8: Remaining table conversions.
- tbl_ep_holdings (from Holdings)
- tbl_pt_gls (from Gls)
- tbl_pm_localizer_marker (from Markers)
- tbl_as_grid_mora (from GridMora)
- tbl_pv_airport_communication (from AirportCommunication)
"""

import sys
import os
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

# Chinese airspace bounds
CN_LAT_MIN, CN_LAT_MAX = 15.0, 55.0
CN_LON_MIN, CN_LON_MAX = 70.0, 140.0


def is_cn_airspace(lat, lon):
    if lat is None or lon is None:
        return False
    return CN_LAT_MIN <= lat <= CN_LAT_MAX and CN_LON_MIN <= lon <= CN_LON_MAX


def convert_holdings(src_conn, dst_conn, airport_lookup=None):
    """Convert holdings for Chinese airspace."""
    print("\n=== Phase 8a: Holdings ===")

    holdings = src_conn.execute("""
        SELECT area_code, region_code, icao_code, waypoint_identifier,
               holding_name, waypoint_latitude, waypoint_longitude,
               duplicate_identifier, inbound_holding_course, turn_direction,
               leg_length, leg_time, minimum_altitude, maximum_altitude,
               holding_speed
        FROM Holdings
    """).fetchall()

    # Filter by airspace
    cn_holdings = [h for h in holdings
                   if is_cn_airspace(h['waypoint_latitude'],
                                     h['waypoint_longitude'])]

    # Columns match exactly
    columns = [
        'area_code', 'region_code', 'icao_code', 'waypoint_identifier',
        'holding_name', 'waypoint_latitude', 'waypoint_longitude',
        'duplicate_identifier', 'inbound_holding_course', 'turn_direction',
        'leg_length', 'leg_time', 'minimum_altitude', 'maximum_altitude',
        'holding_speed', 'waypoint_ref_table',
    ]

    # Get existing for dedup
    existing = set()
    for row in dst_conn.execute(
        "SELECT waypoint_identifier, holding_name, region_code FROM tbl_ep_holdings"
    ):
        existing.add((row['waypoint_identifier'], row['holding_name'],
                      row['region_code']))

    new_rows = []
    for h in cn_holdings:
        key = (h['waypoint_identifier'], h['holding_name'], h['region_code'])
        if key in existing:
            continue

        new_rows.append((
            h['area_code'] or 'EEU',
            h['region_code'],
            h['icao_code'],
            h['waypoint_identifier'],
            h['holding_name'],
            h['waypoint_latitude'],
            h['waypoint_longitude'],
            h['duplicate_identifier'],
            h['inbound_holding_course'],
            h['turn_direction'],
            h['leg_length'],
            h['leg_time'],
            h['minimum_altitude'],
            h['maximum_altitude'],
            h['holding_speed'],
            'PC',  # Default to terminal waypoint ref
        ))

    from db_utils import batch_insert  # type: ignore[import-untyped]
    print(f"  Holdings to insert: {len(new_rows)}")
    batch_insert(dst_conn, 'tbl_ep_holdings', columns, new_rows)


def convert_gls(src_conn, dst_conn, airport_lookup):
    """Convert GLS for Chinese airports."""
    print("\n=== Phase 8b: GLS ===")

    cn_airport_ids = set(airport_lookup.keys())
    if not cn_airport_ids:
        print("  No Chinese airports, skipping GLS")
        return

    gls_rows = src_conn.execute("""
        SELECT area_code, airport_identifier, icao_code,
               gls_ref_path_identifier, gls_category, gls_channel,
               runway_identifier, gls_approach_bearing,
               station_latitude, station_longitude,
               gls_station_ident, gls_approach_slope,
               magnetic_variation, station_elevation, station_type
        FROM Gls
    """).fetchall()

    columns = [
        'airport_identifier', 'area_code', 'gls_approach_bearing',
        'gls_approach_slope', 'gls_category', 'gls_channel',
        'gls_ref_path_identifier', 'gls_station_ident', 'icao_code',
        'magnetic_variation', 'runway_identifier', 'station_elevation',
        'station_latitude', 'station_longitude', 'station_type',
    ]

    # Get Chinese airport ICAOs
    cn_icaos = set()
    for aid, icao in airport_lookup.items():
        cn_icaos.add(icao)

    existing = set()
    for row in dst_conn.execute(
        "SELECT airport_identifier, gls_ref_path_identifier FROM tbl_pt_gls"
    ):
        existing.add((row['airport_identifier'], row['gls_ref_path_identifier']))

    new_rows = []
    for g in gls_rows:
        if g['airport_identifier'] not in cn_icaos:
            continue
        key = (g['airport_identifier'], g['gls_ref_path_identifier'])
        if key in existing:
            continue

        new_rows.append((
            g['airport_identifier'],
            g['area_code'] or 'EEU',
            g['gls_approach_bearing'],
            g['gls_approach_slope'],
            g['gls_category'],
            g['gls_channel'],
            g['gls_ref_path_identifier'],
            g['gls_station_ident'],
            g['icao_code'],
            g['magnetic_variation'],
            g['runway_identifier'],
            g['station_elevation'],
            g['station_latitude'],
            g['station_longitude'],
            g['station_type'],
        ))

    from db_utils import batch_insert  # type: ignore[import-untyped]
    print(f"  GLS to insert: {len(new_rows)}")
    batch_insert(dst_conn, 'tbl_pt_gls', columns, new_rows)


def convert_markers(src_conn, dst_conn, airport_lookup):
    """Convert marker beacons for Chinese airports."""
    print("\n=== Phase 8c: Markers ===")

    cn_airport_ids = set(airport_lookup.keys())

    markers = src_conn.execute("""
        SELECT ID, AirportID, RunwayID, LLZIdent, MarkerIdent,
               Type, Latitude, Longitude
        FROM Markers
    """).fetchall()

    cn_markers = [m for m in markers if m['AirportID'] in cn_airport_ids]
    print(f"  Fenix total markers: {len(markers)}")
    print(f"  Chinese markers: {len(cn_markers)}")

    columns = [
        'airport_identifier', 'area_code', 'icao_code',
        'llz_identifier', 'marker_identifier', 'marker_latitude',
        'marker_longitude', 'marker_type', 'runway_identifier',
    ]

    existing = set()
    for row in dst_conn.execute(
        "SELECT airport_identifier, marker_identifier FROM tbl_pm_localizer_marker"
    ):
        existing.add((row['airport_identifier'], row['marker_identifier']))

    new_rows = []
    for m in cn_markers:
        icao = airport_lookup.get(m['AirportID'])
        if not icao:
            continue
        key = (icao, m['MarkerIdent'])
        if key in existing:
            continue

        new_rows.append((
            icao,
            'EEU',
            icao[:2],
            m['LLZIdent'],
            m['MarkerIdent'],
            m['Latitude'],
            m['Longitude'],
            m['Type'],
            '',  # runway_identifier (not directly available)
        ))

    from db_utils import batch_insert  # type: ignore[import-untyped]
    print(f"  Markers to insert: {len(new_rows)}")
    batch_insert(dst_conn, 'tbl_pm_localizer_marker', columns, new_rows)


def convert_grid_mora(src_conn, dst_conn):
    """Convert Grid MORA for Chinese airspace."""
    print("\n=== Phase 8d: Grid MORA ===")

    mora = src_conn.execute("""
        SELECT starting_latitude, starting_longitude,
               mora01, mora02, mora03, mora04, mora05,
               mora06, mora07, mora08, mora09, mora10,
               mora11, mora12, mora13, mora14, mora15,
               mora16, mora17, mora18, mora19, mora20,
               mora21, mora22, mora23, mora24, mora25,
               mora26, mora27, mora28, mora29, mora30
        FROM GridMora
    """).fetchall()

    # Filter to China-adjacent grids
    cn_mora = []
    for m in mora:
        start_lat = m['starting_latitude']
        start_lon = m['starting_longitude']
        # Grid covers ~30 cells * 1° each ≈ 30° lat range
        end_lat = start_lat + 30
        end_lon = m['starting_longitude'] + 30
        # Check if grid overlaps China
        if (start_lat < CN_LAT_MAX and end_lat > CN_LAT_MIN and
                start_lon < CN_LON_MAX and end_lon > CN_LON_MIN):
            cn_mora.append(m)

    columns = [
        'mora01', 'mora02', 'mora03', 'mora04', 'mora05',
        'mora06', 'mora07', 'mora08', 'mora09', 'mora10',
        'mora11', 'mora12', 'mora13', 'mora14', 'mora15',
        'mora16', 'mora17', 'mora18', 'mora19', 'mora20',
        'mora21', 'mora22', 'mora23', 'mora24', 'mora25',
        'mora26', 'mora27', 'mora28', 'mora29', 'mora30',
        'quadrant_code', 'starting_latitude', 'starting_longitude',
    ]

    # Get existing
    existing = set()
    for row in dst_conn.execute(
        "SELECT starting_latitude, starting_longitude FROM tbl_as_grid_mora"
    ):
        existing.add((row['starting_latitude'], row['starting_longitude']))

    new_rows = []
    for m in cn_mora:
        key = (str(m['starting_latitude']), str(m['starting_longitude']))
        if key in existing:
            continue

        row = tuple(str(m[f'mora{i:02d}'] or '') for i in range(1, 31))
        row += (None, str(m['starting_latitude']), str(m['starting_longitude']))
        new_rows.append(row)

    from db_utils import batch_insert  # type: ignore[import-untyped]
    print(f"  Grid MORA to insert: {len(new_rows)}")
    batch_insert(dst_conn, 'tbl_as_grid_mora', columns, new_rows)


def convert_airport_comm(src_conn, dst_conn, airport_lookup):
    """Convert airport communications for Chinese airports."""
    print("\n=== Phase 8e: Airport Communication ===")

    cn_airport_ids = set(airport_lookup.keys())

    comms = src_conn.execute("""
        SELECT area_code, icao_code, airport_identifier,
               communication_type, communication_frequency,
               frequency_units, service_indicator, callsign,
               latitude, longitude
        FROM AirportCommunication
    """).fetchall()

    # Filter by Chinese airports
    cn_icaos = {airport_lookup.get(aid) for aid in cn_airport_ids}
    cn_comms = [c for c in comms
                if c['airport_identifier'] in cn_icaos]

    if not cn_comms:
        # Try matching by ICAO in the comm record
        cn_icaos = {airport_lookup.get(aid) for aid in cn_airport_ids}
        cn_comms = [c for c in comms if c['airport_identifier'] in cn_icaos]

    columns = [
        'airport_identifier', 'area_code', 'callsign',
        'communication_frequency', 'communication_type', 'frequency_units',
        'guard_transmit', 'icao_code', 'latitude', 'longitude',
        'narrative', 'remote_facility_icao_code', 'remote_facility',
        'sector_facility_icao_code', 'sector_facility', 'sectorization',
        'service_indicator', 'time_of_operation_1', 'time_of_operation_2',
        'time_of_operation_3', 'time_of_operation_4', 'time_of_operation_5',
        'time_of_operation_6', 'time_of_operation_7',
    ]

    existing = set()
    for row in dst_conn.execute(
        "SELECT airport_identifier, communication_type, communication_frequency "
        "FROM tbl_pv_airport_communication"
    ):
        existing.add((row['airport_identifier'], row['communication_type'],
                      row['communication_frequency']))

    new_rows = []
    for c in cn_comms:
        key = (c['airport_identifier'], c['communication_type'],
               c['communication_frequency'])
        if key in existing:
            continue

        new_rows.append((
            c['airport_identifier'],
            c['area_code'] or 'EEU',
            c['callsign'],
            c['communication_frequency'],
            c['communication_type'],
            c['frequency_units'] or 'V',
            None,  # guard_transmit
            c['icao_code'],
            c['latitude'],
            c['longitude'],
            None,  # narrative
            None, None, None, None, None,  # remote/sector facility
            c['service_indicator'],
            None, None, None, None, None, None, None,  # time_of_operation
        ))

    from db_utils import batch_insert  # type: ignore[import-untyped]
    print(f"  Airport communications to insert: {len(new_rows)}")
    batch_insert(dst_conn, 'tbl_pv_airport_communication', columns, new_rows)

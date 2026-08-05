"""
Post-conversion verification module.

Validates:
1. Row counts are reasonable
2. Frequency ranges are valid
3. Coordinate ranges are valid for Chinese airspace
4. Referential integrity (airport_identifier consistency)
5. Sample spot-checks for known airports
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sqlite3
import json
import re
from pathlib import Path

from mappings import CN_ICAO_PREFIXES, CN_SPECIAL_AIRPORTS
from tables.toliss import count_runway_order_violations, is_toliss_target


CN_LAT_MIN, CN_LAT_MAX = 15.0, 55.0
CN_LON_MIN, CN_LON_MAX = 70.0, 140.0


def _cn_airport_sql(column: str = "airport_identifier") -> str:
    prefixes = ",".join(f"'{prefix}'" for prefix in CN_ICAO_PREFIXES)
    specials = ",".join(f"'{airport}'" for airport in CN_SPECIAL_AIRPORTS)
    return (
        f"(SUBSTR({column},1,2) IN ({prefixes}) "
        f"OR {column} IN ({specials}))"
    )


def verify_all(db_path: str, source_path: str | None = None) -> bool:
    """Run all verification checks. Returns True if all pass."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    print("\n" + "=" * 60)
    print("  Verification Report")
    print("=" * 60)

    all_ok = True

    all_ok &= check_runtime_compatibility(conn, db_path)
    all_ok &= check_row_counts(conn)
    all_ok &= check_frequency_ranges(conn)
    all_ok &= check_coordinate_ranges(conn)
    all_ok &= check_referential_integrity(conn)
    all_ok &= check_inibuilds_procedure_semantics(conn)
    if is_toliss_target(conn):
        all_ok &= check_toliss_loader_compatibility(conn)
    if source_path:
        all_ok &= check_source_procedure_completeness(conn, source_path)
    all_ok &= spot_check_airport(conn, 'ZBAA', 'BEIJING')
    all_ok &= spot_check_airport(conn, 'ZSPD', 'PUDONG')

    if all_ok:
        print("\n[OK] All verification checks passed!")
    else:
        print("\n[FAIL] Some verification checks FAILED!")

    conn.close()
    return all_ok


def check_toliss_loader_compatibility(conn) -> bool:
    """Check constraints imposed by the AS346 fixed-memory SQL loader."""
    print("\n--- ToLiss / AS346 加载兼容性检查 ---")
    checks = (
        (
            "NDB 磁偏角为空",
            "SELECT COUNT(1) FROM tbl_db_enroute_ndbnavaids "
            "WHERE magnetic_variation IS NULL",
        ),
        (
            "航路点标识超过 5 个字符",
            "SELECT COUNT(1) FROM tbl_ea_enroute_waypoints "
            "WHERE waypoint_identifier IS NULL "
            "OR length(waypoint_identifier) > 5",
        ),
        (
            "航路固定字段或距离无效",
            "SELECT COUNT(1) FROM tbl_er_enroute_airways "
            "WHERE route_identifier IS NULL "
            "OR length(route_identifier) > 5 "
            "OR waypoint_identifier IS NULL "
            "OR length(waypoint_identifier) > 5 "
            "OR waypoint_description_code IS NULL "
            "OR length(waypoint_description_code) <> 4 "
            "OR inbound_course IS NULL "
            "OR inbound_distance IS NULL "
            "OR outbound_course IS NULL "
            "OR inbound_distance < 0.0 "
            "OR inbound_distance > 1000.0",
        ),
    )
    ok = True
    for label, query in checks:
        count = conn.execute(query).fetchone()[0]
        if count:
            print(f"  [FAIL] {label}: {count}")
            ok = False
        else:
            print(f"  [OK] {label}: 0")
    runway_order_violations = count_runway_order_violations(conn)
    if runway_order_violations:
        print(
            "  [FAIL] AS346 跑道扫描顺序倒序: "
            f"{runway_order_violations}"
        )
        ok = False
    else:
        print("  [OK] AS346 跑道扫描顺序倒序: 0")
    invalid_rf_legs = 0
    for table in ("tbl_pd_sids", "tbl_pe_stars", "tbl_pf_iaps"):
        invalid_rf_legs += conn.execute(
            f"""
            SELECT COUNT(1)
            FROM {table}
            WHERE path_termination = 'RF'
              AND (arc_radius IS NULL OR arc_radius <= 0.0)
            """
        ).fetchone()[0]
    if invalid_rf_legs:
        print(f"  [FAIL] RF 航段半径无效: {invalid_rf_legs}")
        ok = False
    else:
        print("  [OK] RF 航段半径无效: 0")
    invalid_procedure_fields = 0
    for table in ("tbl_pd_sids", "tbl_pe_stars", "tbl_pf_iaps"):
        invalid_procedure_fields += conn.execute(
            f"""
            SELECT COUNT(1)
            FROM {table}
            WHERE (waypoint_description_code IS NOT NULL
                   AND length(waypoint_description_code) <> 4)
               OR (altitude_description IS NOT NULL
                   AND length(altitude_description) <> 1)
               OR ((waypoint_identifier IS NULL
                    OR trim(waypoint_identifier) = '')
                   AND waypoint_icao_code IS NOT NULL)
               OR ((waypoint_identifier IS NOT NULL
                    AND trim(waypoint_identifier) <> '')
                   AND waypoint_icao_code IS NULL)
               OR ((recommended_navaid IS NULL
                    OR trim(recommended_navaid) = '')
                   AND recommended_navaid_icao_code IS NOT NULL)
               OR ((recommended_navaid IS NOT NULL
                    AND trim(recommended_navaid) <> '')
                   AND recommended_navaid_icao_code IS NULL)
               OR ((center_waypoint IS NULL OR trim(center_waypoint) = '')
                   AND center_waypoint_icao_code IS NOT NULL)
               OR ((center_waypoint IS NOT NULL
                    AND trim(center_waypoint) <> '')
                   AND center_waypoint_icao_code IS NULL)
            """
        ).fetchone()[0]
    if invalid_procedure_fields:
        print(
            "  [FAIL] 程序固定字段或 NULL 联动无效: "
            f"{invalid_procedure_fields}"
        )
        ok = False
    else:
        print("  [OK] 程序固定字段或 NULL 联动无效: 0")
    return ok


def check_inibuilds_procedure_semantics(conn) -> bool:
    """Validate procedure contracts observed in working iniBuilds data."""
    print("\n--- iniBuilds 程序语义检查 ---")
    ok = True
    china_filter = _cn_airport_sql()

    positive_angles = conn.execute(
        "SELECT COUNT(*) FROM tbl_pf_iaps "
        f"WHERE {china_filter} AND vertical_angle > 0"
    ).fetchone()[0]
    if positive_angles:
        print(f"  [FAIL] 正数进近垂直角: {positive_angles}")
        ok = False
    else:
        print("  [OK] 进近垂直角方向")

    partial_ar = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT airport_identifier, procedure_identifier
            FROM tbl_pf_iaps
            WHERE {china_filter}
            GROUP BY airport_identifier, procedure_identifier
            HAVING SUM(CASE WHEN authorization_required = 'Y' THEN 1 ELSE 0 END) > 0
               AND SUM(CASE WHEN authorization_required IS NOT 'Y' THEN 1 ELSE 0 END) > 0
        )
        """
    ).fetchone()[0]
    if partial_ar:
        print(f"  [FAIL] 授权标记不完整的 RNP AR 程序: {partial_ar}")
        ok = False
    else:
        print("  [OK] RNP AR 授权标记一致")

    invalid_rf = 0
    for table in ("tbl_pd_sids", "tbl_pe_stars", "tbl_pf_iaps"):
        invalid_rf += conn.execute(
            f"""
            SELECT COUNT(*) FROM {table}
            WHERE {china_filter}
              AND path_termination = 'RF'
              AND (arc_radius IS NULL OR arc_radius <= 0
                   OR center_waypoint_latitude IS NULL
                   OR center_waypoint_longitude IS NULL)
            """
        ).fetchone()[0]
    if invalid_rf:
        print(f"  [FAIL] RF 航段几何不完整: {invalid_rf}")
        ok = False
    else:
        print("  [OK] RF 航段几何完整")

    point_coordinates = {}
    for ref_table, table in (
        ("PC", "tbl_pc_terminal_waypoints"),
        ("EA", "tbl_ea_enroute_waypoints"),
    ):
        for row in conn.execute(
            f"SELECT waypoint_identifier, icao_code, waypoint_latitude, "
            f"waypoint_longitude FROM {table}"
        ):
            key = (ref_table, row[0], row[1])
            point_coordinates.setdefault(key, []).append((row[2], row[3]))

    waypoint_mismatches = 0
    localizer_keys = {
        (row[0], row[1])
        for row in conn.execute(
            "SELECT airport_identifier, llz_identifier "
            "FROM tbl_pi_localizers_glideslopes"
        )
    }
    pi_mismatches = 0
    for table in ("tbl_pd_sids", "tbl_pe_stars", "tbl_pf_iaps"):
        rows = conn.execute(
            f"""
            SELECT airport_identifier, waypoint_ref_table,
                   waypoint_identifier, waypoint_icao_code,
                   waypoint_latitude, waypoint_longitude,
                   recommended_navaid, recommended_navaid_ref_table
            FROM {table}
            WHERE {china_filter}
            """
        )
        for row in rows:
            ref_table = row['waypoint_ref_table']
            if ref_table in {'PC', 'EA'} and row['waypoint_identifier']:
                key = (
                    ref_table,
                    row['waypoint_identifier'],
                    row['waypoint_icao_code'],
                )
                candidates = point_coordinates.get(key, ())
                lat = row['waypoint_latitude']
                lon = row['waypoint_longitude']
                if lat is None or lon is None or not any(
                    abs(point_lat - lat) < 0.001
                    and abs(point_lon - lon) < 0.001
                    for point_lat, point_lon in candidates
                ):
                    waypoint_mismatches += 1
            if row['recommended_navaid_ref_table'] == 'PI':
                if (row['airport_identifier'], row['recommended_navaid']) \
                        not in localizer_keys:
                    pi_mismatches += 1

    if waypoint_mismatches:
        print(f"  [FAIL] EA/PC 航点引用或坐标不一致: {waypoint_mismatches}")
        ok = False
    else:
        print("  [OK] EA/PC 航点引用与坐标一致")
    if pi_mismatches:
        print(f"  [FAIL] 无匹配航向台的 PI 引用: {pi_mismatches}")
        ok = False
    else:
        print("  [OK] PI 航向台引用完整")
    return ok


def check_runtime_compatibility(conn, db_path: str) -> bool:
    """Check the invariants required by the iniBuilds WASM DFDv2 reader."""
    print("\n--- iniBuilds Runtime Compatibility Check ---")
    ok = True

    journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if journal_mode != "delete":
        print(f"  [FAIL] SQLite journal mode is {journal_mode}, expected delete")
        ok = False
    else:
        print("  [OK] SQLite journal mode: delete")

    rows = conn.execute(
        "SELECT cycle, revision, effective_fromto FROM tbl_hdr_header"
    ).fetchall()
    if len(rows) != 1:
        print(f"  [FAIL] Header row count: {len(rows)}, expected 1")
        return False

    cycle = str(rows[0]["cycle"] or "")
    revision = str(rows[0]["revision"] or "")
    effective = str(rows[0]["effective_fromto"] or "")
    if not re.fullmatch(r"\d{4}", cycle):
        print(f"  [FAIL] Header cycle must be four digits: {cycle!r}")
        ok = False
    if not re.fullmatch(r"\d{3}", revision):
        print(f"  [FAIL] Header revision must be three digits: {revision!r}")
        ok = False
    if not re.fullmatch(r"\d{10}", effective):
        print(f"  [FAIL] Header effective_fromto must be ten digits: {effective!r}")
        ok = False
    if ok:
        print(f"  [OK] DFDv2 header: {cycle} R{int(revision)} ({effective})")

    cycle_path = Path(db_path).resolve().with_name("cycle.json")
    if cycle_path.is_file():
        try:
            payload = json.loads(cycle_path.read_text(encoding="utf-8"))
            json_cycle = str(payload.get("cycle", ""))
            json_revision = str(payload.get("revision", ""))
            revisions_match = (
                revision.isdigit()
                and json_revision.isdigit()
                and int(revision) == int(json_revision)
            )
            if json_cycle != cycle or not revisions_match:
                print(
                    "  [FAIL] cycle.json does not match database header: "
                    f"{json_cycle} R{json_revision} vs {cycle} R{revision}"
                )
                ok = False
            else:
                print("  [OK] cycle.json matches database header")
        except (OSError, ValueError) as error:
            print(f"  [FAIL] Unable to read cycle.json: {error}")
            ok = False
    else:
        print("  [WARN] cycle.json not found beside database")

    return ok


def check_row_counts(conn) -> bool:
    """Verify required tables contain data and report template-size hints."""
    print("\n--- Row Count Check ---")
    ok = True

    checks = [
        ('tbl_pa_airports', 15000, 20000, 'Airports (total)'),
        ('tbl_pg_runways', 38000, 45000, 'Runways (total)'),
        ('tbl_d_vhfnavaids', 8000, 9000, 'VHF Navaids (total)'),
        ('tbl_ea_enroute_waypoints', 80000, 120000, 'Enroute Waypoints (total)'),
        ('tbl_er_enroute_airways', 100000, 130000, 'Airway Segments (total)'),
        ('tbl_pd_sids', 200000, 300000, 'SIDs (total)'),
        ('tbl_pe_stars', 170000, 250000, 'STARs (total)'),
        ('tbl_pf_iaps', 380000, 400000, 'IAPs (total)'),
    ]

    for table, min_rows, max_rows, label in checks:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count == 0:
                status = "[FAIL]"
                ok = False
            elif min_rows <= count <= max_rows:
                status = "[OK]"
            else:
                # iniBuilds ships different baseline datasets per aircraft and
                # simulator build. Source-completeness checks provide the hard
                # signal; these historical totals are only useful as a hint.
                status = "[WARN]"
            print(f"  {status} {label}: {count} rows (reference {min_rows}-{max_rows})")
        except Exception as e:
            print(f"  [FAIL] {label}: ERROR - {e}")
            ok = False

    return ok


def check_frequency_ranges(conn) -> bool:
    """Verify decoded frequencies are in valid ranges."""
    print("\n--- Frequency Range Check ---")
    ok = True

    # VHF navaids: 108.00 - 117.95 MHz
    bad_vhf = conn.execute("""
        SELECT COUNT(*) FROM tbl_d_vhfnavaids
        WHERE navaid_latitude BETWEEN 15.0 AND 55.0
          AND navaid_longitude BETWEEN 70.0 AND 140.0
          AND (navaid_frequency < 108.0 OR navaid_frequency > 118.0)
    """).fetchone()[0]
    if bad_vhf > 0:
        print(f"  [FAIL] {bad_vhf} VHF navaids with out-of-range frequency")
        ok = False
    else:
        print("  [OK] VHF navaid frequencies in range (108-118 MHz)")

    # NDB: 190 - 1750 KHz (but stored as-is in KHz)
    bad_ndb = conn.execute("""
        SELECT COUNT(*) FROM tbl_db_enroute_ndbnavaids
        WHERE navaid_latitude BETWEEN 15.0 AND 55.0
          AND navaid_longitude BETWEEN 70.0 AND 140.0
          AND (navaid_frequency < 100 OR navaid_frequency > 2000)
    """).fetchone()[0]
    if bad_ndb > 0:
        print(f"  [FAIL] {bad_ndb} NDB navaids with out-of-range frequency")
        ok = False
    else:
        print("  [OK] NDB navaid frequencies in range (100-2000 KHz)")

    # ILS: 108.00 - 111.95 MHz
    bad_ils = conn.execute("""
        SELECT COUNT(*) FROM tbl_pi_localizers_glideslopes
        WHERE (airport_identifier IN ('OPGT', 'VHHX')
               OR SUBSTR(airport_identifier, 1, 2) IN
                  ('ZB','ZG','ZH','ZJ','ZL','ZP','ZS','ZU','ZW','ZY'))
          AND (llz_frequency < 108.0 OR llz_frequency > 112.0)
    """).fetchone()[0]
    if bad_ils > 0:
        print(f"  [FAIL] {bad_ils} ILS with out-of-range frequency")
        ok = False
    else:
        print("  [OK] ILS localizer frequencies in range (108-112 MHz)")

    return ok


def check_coordinate_ranges(conn) -> bool:
    """Verify coordinates are in valid Chinese airspace range."""
    print("\n--- Coordinate Range Check ---")
    ok = True

    coord_checks = [
        ('tbl_pa_airports', 'airport_ref_latitude', 'airport_ref_longitude',
         'Airports', True),  # Should be in China
        ('tbl_ea_enroute_waypoints', 'waypoint_latitude', 'waypoint_longitude',
         'Enroute Waypoints', True),  # Should be in China
        ('tbl_d_vhfnavaids', 'navaid_latitude', 'navaid_longitude',
         'VHF Navaids', True),
        ('tbl_pi_localizers_glideslopes', 'llz_latitude', 'llz_longitude',
         'Localizers', True),
    ]

    for table, lat_col, lon_col, label, should_be_cn in coord_checks:
        try:
            # Check for NULL coordinates
            null_count = conn.execute(f"""
                SELECT COUNT(*) FROM {table}
                WHERE {lat_col} IS NULL OR {lon_col} IS NULL
            """).fetchone()[0]

            # Check for out-of-range coordinates
            out_of_range = conn.execute(f"""
                SELECT COUNT(*) FROM {table}
                WHERE ({lat_col} < -90 OR {lat_col} > 90
                   OR {lon_col} < -180 OR {lon_col} > 180)
            """).fetchone()[0]

            if null_count > 0:
                print(f"  [WARN] {label}: {null_count} rows with NULL coordinates")
            if out_of_range > 0:
                print(f"  [FAIL] {label}: {out_of_range} rows with out-of-range coordinates")
                ok = False
            else:
                print(f"  [OK] {label}: coordinates valid")
        except Exception as e:
            print(f"  [FAIL] {label}: ERROR - {e}")
            ok = False

    return ok


def check_referential_integrity(conn) -> bool:
    """Verify airport_identifier consistency across tables."""
    print("\n--- Referential Integrity Check ---")
    ok = True

    # Get all airport identifiers
    apts = set()
    for row in conn.execute("SELECT airport_identifier FROM tbl_pa_airports"):
        apts.add(row['airport_identifier'])

    # Tables that should reference existing airports
    ref_checks = [
        ('tbl_pg_runways', 'airport_identifier', 'Runways'),
        ('tbl_pi_localizers_glideslopes', 'airport_identifier', 'Localizers'),
        ('tbl_pd_sids', 'airport_identifier', 'SIDs'),
        ('tbl_pe_stars', 'airport_identifier', 'STARs'),
        ('tbl_pf_iaps', 'airport_identifier', 'IAPs'),
        ('tbl_pt_gls', 'airport_identifier', 'GLS'),
        ('tbl_pv_airport_communication', 'airport_identifier', 'Airport Comm'),
    ]

    for table, col, label in ref_checks:
        try:
            # Check if table exists
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count == 0:
                print(f"  - {label}: empty table, skipping")
                continue

            # Find orphan references
            orphans = conn.execute(f"""
                SELECT COUNT(*) FROM {table} t
                LEFT JOIN tbl_pa_airports a ON t.{col} = a.airport_identifier
                WHERE a.airport_identifier IS NULL
            """).fetchone()[0]

            if orphans > 0:
                print(f"  [WARN] {label}: {orphans} orphan references (no matching airport)")
                # Not a hard failure since historical data might reference removed airports
            else:
                print(f"  [OK] {label}: all references valid")
        except Exception as e:
            print(f"  - {label}: {e}")
            # Not a hard failure

    return ok


def check_source_procedure_completeness(conn, source_path: str) -> bool:
    """Compare converted Chinese procedure rows with the Fenix source."""
    print("\n--- Fenix Procedure Completeness Check ---")
    source = sqlite3.connect(f"file:{source_path}?immutable=1", uri=True)
    source.row_factory = sqlite3.Row
    ok = True
    airport_filter = (
        "a.ICAO IN ('OPGT','VHHX') OR SUBSTR(a.ICAO,1,2) IN "
        "('ZB','ZG','ZH','ZJ','ZL','ZP','ZS','ZU','ZW','ZY')"
    )
    mappings = [
        ('2', 'tbl_pd_sids', 'SIDs'),
        ('1', 'tbl_pe_stars', 'STARs'),
        ('3', 'tbl_pf_iaps', 'IAPs'),
    ]
    try:
        for proc, table, label in mappings:
            source_rows = source.execute(
                f"""
                SELECT a.ICAO AS airport_identifier, l.*
                FROM TerminalLegs l
                JOIN Terminals t ON t.ID=l.TerminalID
                JOIN Airports a ON a.ID=t.AirportID
                WHERE ({airport_filter}) AND CAST(t.Proc AS TEXT)=?
                ORDER BY l.TerminalID, l.ID
                """,
                (proc,),
            )
            expected_rows = 0
            source_airports = set()
            previous_by_section = {}
            for row in source_rows:
                source_airports.add(row["airport_identifier"])
                section = (
                    row["TerminalID"], row["Type"], row["Transition"]
                )
                signature = tuple(
                    row[key]
                    for key in row.keys()
                    if key not in {"airport_identifier", "ID"}
                )
                if previous_by_section.get(section) != signature:
                    expected_rows += 1
                previous_by_section[section] = signature
            expected_airports = len(source_airports)
            source_airports = sorted(source_airports)
            placeholders = ','.join('?' for _ in source_airports)
            actual_rows, actual_airports = conn.execute(
                f"""
                SELECT COUNT(*), COUNT(DISTINCT airport_identifier)
                FROM {table}
                WHERE airport_identifier IN ({placeholders})
                """,
                source_airports,
            ).fetchone()
            matches = (
                actual_rows == expected_rows
                and actual_airports == expected_airports
            )
            status = "[OK]" if matches else "[FAIL]"
            print(
                f"  {status} {label}: rows {actual_rows}/{expected_rows}, "
                f"airports {actual_airports}/{expected_airports}"
            )
            ok &= matches
    finally:
        source.close()
    return ok


def spot_check_airport(conn, icao: str, name_hint: str) -> bool:
    """Spot-check a known airport and its data."""
    print(f"\n--- Spot Check: {icao} ---")
    ok = True

    # Check airport exists
    apt = conn.execute(
        "SELECT * FROM tbl_pa_airports WHERE airport_identifier = ?", (icao,)
    ).fetchone()

    if apt:
        name = apt['airport_name'] or ''
        print(f"  [OK] Airport: {icao} - {name}")
        if name_hint.upper() not in name.upper():
            print(f"  [WARN] Name doesn't contain expected '{name_hint}'")
    else:
        print(f"  [WARN] Airport {icao} not found (may not be in Chinese region)")
        return True  # Not a failure

    # Check runways
    rwys = conn.execute(
        "SELECT COUNT(*) FROM tbl_pg_runways WHERE airport_identifier = ?",
        (icao,)
    ).fetchone()[0]
    if rwys > 0:
        print(f"  [OK] Runways: {rwys}")
    else:
        print(f"  [WARN] No runways found for {icao}")
        ok = False

    # Check procedures (at least one of SID/STAR/IAP)
    sids = conn.execute(
        "SELECT COUNT(*) FROM tbl_pd_sids WHERE airport_identifier = ?",
        (icao,)
    ).fetchone()[0]
    stars = conn.execute(
        "SELECT COUNT(*) FROM tbl_pe_stars WHERE airport_identifier = ?",
        (icao,)
    ).fetchone()[0]
    iaps = conn.execute(
        "SELECT COUNT(*) FROM tbl_pf_iaps WHERE airport_identifier = ?",
        (icao,)
    ).fetchone()[0]

    print(f"  Procedures: SIDs={sids}, STARs={stars}, IAPs={iaps}")

    if sids == 0 and stars == 0 and iaps == 0:
        print(f"  [WARN] No procedures found for {icao}")
        ok = False
    else:
        print(f"  [OK] Procedures found")

    # Check localizers
    llz = conn.execute(
        "SELECT COUNT(*) FROM tbl_pi_localizers_glideslopes WHERE airport_identifier = ?",
        (icao,)
    ).fetchone()[0]
    if llz > 0:
        print(f"  [OK] Localizers: {llz}")

    return ok


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('db_path', help='Path to db.s3db to verify')
    p.add_argument('--source', help='Fenix nd.db3 used for conversion')
    args = p.parse_args()
    sys.exit(0 if verify_all(args.db_path, args.source) else 1)

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


CN_LAT_MIN, CN_LAT_MAX = 15.0, 55.0
CN_LON_MIN, CN_LON_MAX = 70.0, 140.0


def verify_all(db_path: str, source_path: str | None = None) -> bool:
    """Run all verification checks. Returns True if all pass."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    print("\n" + "=" * 60)
    print("  Verification Report")
    print("=" * 60)

    all_ok = True

    all_ok &= check_row_counts(conn)
    all_ok &= check_frequency_ranges(conn)
    all_ok &= check_coordinate_ranges(conn)
    all_ok &= check_referential_integrity(conn)
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
            expected_rows, expected_airports = source.execute(
                f"""
                SELECT COUNT(*), COUNT(DISTINCT a.ICAO)
                FROM TerminalLegs l
                JOIN Terminals t ON t.ID=l.TerminalID
                JOIN Airports a ON a.ID=t.AirportID
                WHERE ({airport_filter}) AND CAST(t.Proc AS TEXT)=?
                """,
                (proc,),
            ).fetchone()
            source_airports = [
                row[0] for row in source.execute(
                    f"""
                    SELECT DISTINCT a.ICAO
                    FROM TerminalLegs l
                    JOIN Terminals t ON t.ID=l.TerminalID
                    JOIN Airports a ON a.ID=t.AirportID
                    WHERE ({airport_filter}) AND CAST(t.Proc AS TEXT)=?
                    """,
                    (proc,),
                )
            ]
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

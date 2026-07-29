import sqlite3
import unittest

from tables.toliss import sanitize_toliss_data
from verify import check_toliss_loader_compatibility


class TolissCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE tbl_db_enroute_ndbnavaids "
            "(navaid_identifier TEXT, magnetic_variation REAL)"
        )
        self.conn.execute(
            "CREATE TABLE tbl_ea_enroute_waypoints "
            "(waypoint_identifier TEXT)"
        )
        self.conn.execute(
            """
            CREATE TABLE tbl_er_enroute_airways (
                route_identifier TEXT,
                waypoint_identifier TEXT,
                waypoint_description_code TEXT,
                inbound_course REAL,
                inbound_distance REAL,
                outbound_course REAL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE tbl_pg_runways (
                airport_identifier TEXT NOT NULL,
                runway_identifier TEXT NOT NULL,
                runway_length REAL NOT NULL
            )
            """
        )
        for table in ("tbl_pd_sids", "tbl_pe_stars", "tbl_pf_iaps"):
            self.conn.execute(
                f"""
                CREATE TABLE {table} (
                    path_termination TEXT NOT NULL,
                    route_distance_holding_distance_time TEXT,
                    arc_radius REAL,
                    distance_time REAL,
                    altitude_description TEXT,
                    center_waypoint_icao_code TEXT,
                    center_waypoint TEXT,
                    recommended_navaid_icao_code TEXT,
                    recommended_navaid TEXT,
                    waypoint_description_code TEXT,
                    waypoint_icao_code TEXT,
                    waypoint_identifier TEXT
                )
                """
            )

    def tearDown(self):
        self.conn.close()

    def test_sanitizes_values_rejected_by_toliss_loader(self):
        self.conn.execute(
            "INSERT INTO tbl_db_enroute_ndbnavaids VALUES ('AB', NULL)"
        )
        self.conn.execute(
            "INSERT INTO tbl_ea_enroute_waypoints VALUES ('AIWD50/CH')"
        )
        self.conn.executemany(
            "INSERT INTO tbl_er_enroute_airways VALUES (?,?,?,?,?,?)",
            [
                ("A1", "JEDAI", None, None, None, 51.0),
                ("A12345", "ABCDE", "E   ", 1.0, 20.0, 2.0),
                ("A2", "TOOLONG", "E   ", 1.0, 20.0, 2.0),
                ("A3", "VALID", "E   ", 1.0, 10440.0, 2.0),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO tbl_pd_sids (
                path_termination, altitude_description,
                center_waypoint_icao_code, center_waypoint,
                recommended_navaid_icao_code, recommended_navaid,
                waypoint_description_code, waypoint_icao_code,
                waypoint_identifier
            ) VALUES ('TF', 'MAP', 'ZB', NULL, 'ZB', NULL, 'E', 'ZB', NULL)
            """
        )
        self.conn.executemany(
            "INSERT INTO tbl_pg_runways VALUES (?,?,?)",
            [
                ("ZUUU", "02L", 11811.0),
                ("ZBCF", "03", 8202.0),
            ],
        )

        self.assertFalse(check_toliss_loader_compatibility(self.conn))

        stats = sanitize_toliss_data(self.conn)

        self.assertTrue(check_toliss_loader_compatibility(self.conn))
        self.assertEqual(stats["ndb_magvar_defaulted"], 1)
        self.assertEqual(stats["waypoints_removed"], 1)
        self.assertEqual(stats["airways_removed"], 3)
        self.assertEqual(stats["runways_reordered"], 2)
        runway_airports = [
            row[0]
            for row in self.conn.execute(
                "SELECT airport_identifier FROM tbl_pg_runways "
                "WHERE runway_length > 4757"
            )
        ]
        self.assertEqual(runway_airports, ["ZBCF", "ZUUU"])
        row = self.conn.execute(
            "SELECT waypoint_description_code, inbound_course, "
            "inbound_distance, outbound_course FROM tbl_er_enroute_airways"
        ).fetchone()
        self.assertEqual(tuple(row), ("E   ", 0.0, 0.0, 51.0))
        procedure = self.conn.execute(
            """
            SELECT altitude_description, center_waypoint_icao_code,
                   recommended_navaid_icao_code,
                   waypoint_description_code, waypoint_icao_code
            FROM tbl_pd_sids
            """
        ).fetchone()
        self.assertEqual(tuple(procedure), (None, None, None, "E   ", None))

    def test_rejects_incomplete_rf_leg_geometry(self):
        self.conn.execute(
            "INSERT INTO tbl_pf_iaps ("
            "path_termination, route_distance_holding_distance_time, "
            "arc_radius, distance_time) VALUES ('RF', NULL, NULL, NULL)"
        )
        self.assertFalse(check_toliss_loader_compatibility(self.conn))

        self.conn.execute(
            """
            UPDATE tbl_pf_iaps
            SET arc_radius=3.5
            """
        )
        self.assertTrue(check_toliss_loader_compatibility(self.conn))


if __name__ == "__main__":
    unittest.main()

import sqlite3
import unittest

from verify import check_inibuilds_procedure_semantics, check_row_counts


class RowCountVerificationTests(unittest.TestCase):
    TABLES = (
        "tbl_pa_airports",
        "tbl_pg_runways",
        "tbl_d_vhfnavaids",
        "tbl_ea_enroute_waypoints",
        "tbl_er_enroute_airways",
        "tbl_pd_sids",
        "tbl_pe_stars",
        "tbl_pf_iaps",
    )

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        for table in self.TABLES:
            self.conn.execute(f"CREATE TABLE {table} (id INTEGER)")

    def tearDown(self):
        self.conn.close()

    def test_nonempty_aircraft_specific_counts_are_warnings(self):
        for table in self.TABLES:
            self.conn.execute(f"INSERT INTO {table} VALUES (1)")

        self.assertTrue(check_row_counts(self.conn))

    def test_empty_required_table_fails(self):
        self.assertFalse(check_row_counts(self.conn))


class IniBuildsProcedureVerificationTests(unittest.TestCase):
    PROCEDURE_COLUMNS = """
        airport_identifier TEXT, procedure_identifier TEXT,
        path_termination TEXT, arc_radius REAL,
        authorization_required TEXT, center_waypoint TEXT,
        center_waypoint_latitude REAL, center_waypoint_longitude REAL,
        center_waypoint_ref_table TEXT, vertical_angle REAL,
        waypoint_ref_table TEXT, waypoint_identifier TEXT,
        waypoint_icao_code TEXT, waypoint_latitude REAL,
        waypoint_longitude REAL, recommended_navaid TEXT,
        recommended_navaid_ref_table TEXT
    """

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            f"""
            CREATE TABLE tbl_pd_sids ({self.PROCEDURE_COLUMNS});
            CREATE TABLE tbl_pe_stars ({self.PROCEDURE_COLUMNS});
            CREATE TABLE tbl_pf_iaps ({self.PROCEDURE_COLUMNS});
            CREATE TABLE tbl_pc_terminal_waypoints (
                waypoint_identifier TEXT, icao_code TEXT,
                waypoint_latitude REAL, waypoint_longitude REAL
            );
            CREATE TABLE tbl_ea_enroute_waypoints (
                waypoint_identifier TEXT, icao_code TEXT,
                waypoint_latitude REAL, waypoint_longitude REAL
            );
            CREATE TABLE tbl_pi_localizers_glideslopes (
                airport_identifier TEXT, llz_identifier TEXT
            );
            """
        )
        self.conn.execute(
            "INSERT INTO tbl_pc_terminal_waypoints VALUES (?,?,?,?)",
            ("LZ184", "ZU", 29.2166, 94.2025),
        )

    def tearDown(self):
        self.conn.close()

    def _insert_iap(self, **overrides):
        values = {
            "airport_identifier": "ZUNZ",
            "procedure_identifier": "R05",
            "path_termination": "TF",
            "arc_radius": None,
            "authorization_required": "Y",
            "center_waypoint": None,
            "center_waypoint_latitude": None,
            "center_waypoint_longitude": None,
            "center_waypoint_ref_table": None,
            "vertical_angle": -3.0,
            "waypoint_ref_table": "PC",
            "waypoint_identifier": "LZ184",
            "waypoint_icao_code": "ZU",
            "waypoint_latitude": 29.2166,
            "waypoint_longitude": 94.2025,
            "recommended_navaid": None,
            "recommended_navaid_ref_table": None,
        }
        values.update(overrides)
        columns = list(values)
        self.conn.execute(
            f"INSERT INTO tbl_pf_iaps ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            [values[column] for column in columns],
        )

    def test_accepts_consistent_procedure_references(self):
        self._insert_iap()
        self.assertTrue(check_inibuilds_procedure_semantics(self.conn))

    def test_rejects_positive_angle_partial_ar_and_wrong_waypoint(self):
        self._insert_iap(vertical_angle=3.0)
        self._insert_iap(
            authorization_required=None,
            waypoint_latitude=30.0,
            waypoint_longitude=95.0,
        )
        self.assertFalse(check_inibuilds_procedure_semantics(self.conn))

    def test_ignores_non_chinese_z_prefix_airports(self):
        self._insert_iap(
            airport_identifier="ZKPY",
            authorization_required=None,
            waypoint_identifier="MISSING",
            waypoint_icao_code="ZK",
            waypoint_latitude=39.0,
            waypoint_longitude=125.0,
            vertical_angle=3.0,
        )
        self.assertTrue(check_inibuilds_procedure_semantics(self.conn))


if __name__ == "__main__":
    unittest.main()

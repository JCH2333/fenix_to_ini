import sqlite3
import unittest

from tables.procedures import TBL_PD_COLUMNS, TBL_PF_COLUMNS, convert_procedures


def create_table(conn, name, columns):
    definitions = ", ".join(f'"{column}"' for column in columns)
    conn.execute(f'CREATE TABLE "{name}" ({definitions})')


class ProcedureConversionTests(unittest.TestCase):
    def setUp(self):
        self.src = sqlite3.connect(":memory:")
        self.src.row_factory = sqlite3.Row
        self.dst = sqlite3.connect(":memory:")
        self.dst.row_factory = sqlite3.Row

        self.src.executescript(
            """
            CREATE TABLE Terminals (
                ID INTEGER, AirportID INTEGER, Proc TEXT, ICAO TEXT,
                FullName TEXT, Name TEXT, Rwy TEXT, RwyID INTEGER, IlsID INTEGER
            );
            CREATE TABLE TerminalLegs (
                ID INTEGER, TerminalID INTEGER, Type TEXT, Transition TEXT,
                TrackCode TEXT, WptID INTEGER, WptLat REAL, WptLon REAL,
                TurnDir TEXT, NavID INTEGER, NavLat REAL, NavLon REAL,
                NavBear REAL, NavDist REAL, Course REAL, Distance REAL,
                Alt TEXT, Vnav REAL, CenterID INTEGER, CenterLat REAL,
                CenterLon REAL, WptDescCode TEXT
            );
            CREATE TABLE TerminalLegsEx (
                ID INTEGER, IsFlyOver INTEGER, SpeedLimit INTEGER,
                SpeedLimitDescription TEXT
            );
            """
        )
        create_table(self.dst, "tbl_pd_sids", TBL_PD_COLUMNS)
        create_table(self.dst, "tbl_pe_stars", TBL_PD_COLUMNS)
        # MSFS 2024 iniBuilds A340 omits the optional ctl column.
        create_table(
            self.dst,
            "tbl_pf_iaps",
            [column for column in TBL_PF_COLUMNS if column != "ctl"],
        )

        foreign = {column: None for column in TBL_PD_COLUMNS}
        foreign.update(
            airport_identifier="KJFK",
            path_termination="TF",
            procedure_identifier="FOREIGN",
            route_type="1",
            seqno=10,
        )
        stale = dict(foreign)
        stale.update(airport_identifier="ZBAA", procedure_identifier="STALE")
        self._insert("tbl_pd_sids", TBL_PD_COLUMNS, foreign)
        self._insert("tbl_pd_sids", TBL_PD_COLUMNS, stale)

    def tearDown(self):
        self.src.close()
        self.dst.close()

    def _insert(self, table, columns, values):
        placeholders = ",".join("?" for _ in columns)
        self.dst.execute(
            f'INSERT INTO "{table}" VALUES ({placeholders})',
            [values.get(column) for column in columns],
        )

    def test_preserves_fenix_route_sections_and_replaces_stale_china_rows(self):
        self.src.executemany(
            "INSERT INTO Terminals VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (10, 1, "2", "ZBAA", None, "BOTP2G", "18R", None, None),
                (20, 1, "3", "ZBAA", None, "I01-Y", "01", None, None),
                (30, 1, "3", "ZBAA", None, "R01-Y", "01", None, None),
            ],
        )
        legs = [
            (1, 10, "5", "ALL", "DF", 101, 40.0, 116.0, None, None, None, None, None, None, None, None, None, None, None, None, None, None),
            (2, 10, "5", "ALL", "CA", None, None, None, None, None, None, None, None, None, 180.0, None, "3000A", None, None, None, None, None),
            (3, 10, "5", "ALL", "TF", 102, 40.1, 116.1, None, None, None, None, None, None, None, None, None, None, None, None, None, None),
            (4, 20, "A", "AA122", "IF", 201, 39.4, 116.4, None, None, None, None, None, None, None, None, None, None, None, None, None, None),
            (5, 20, "I", "", "CF", None, 40.05, 116.61, None, None, None, None, None, None, None, None, "MAP", None, None, None, None, "G  M"),
            (6, 30, "0", "AA141", "IF", 301, 39.6, 116.7, None, None, None, None, None, None, None, None, None, None, None, None, None, None),
            (7, 30, "0", None, "TF", 302, 39.7, 116.8, None, None, None, None, None, None, None, None, None, None, None, None, None, None),
        ]
        self.src.executemany(
            "INSERT INTO TerminalLegs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            legs,
        )

        waypoint_lookup = {
            101: {"ident": "DE18R", "lat": 40.0, "lon": 116.0, "name": ""},
            102: {"ident": "BOTPU", "lat": 40.1, "lon": 116.1, "name": ""},
            201: {"ident": "AA122", "lat": 39.4, "lon": 116.4, "name": ""},
            301: {"ident": "AA141", "lat": 39.6, "lon": 116.7, "name": ""},
            302: {"ident": "FINAL", "lat": 39.7, "lon": 116.8, "name": ""},
        }

        convert_procedures(
            self.src,
            self.dst,
            {1: "ZBAA"},
            {},
            waypoint_lookup,
            {},
        )

        self.assertEqual(
            self.dst.execute(
                "SELECT COUNT(*) FROM tbl_pd_sids WHERE airport_identifier='KJFK'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.dst.execute(
                "SELECT COUNT(*) FROM tbl_pd_sids WHERE procedure_identifier='STALE'"
            ).fetchone()[0],
            0,
        )

        sid_rows = self.dst.execute(
            """
            SELECT route_type, transition_identifier, seqno,
                   path_termination, waypoint_identifier
            FROM tbl_pd_sids WHERE airport_identifier='ZBAA'
            ORDER BY seqno
            """
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in sid_rows],
            [
                ("5", "RW18R", 10, "DF", "DE18R"),
                ("5", "RW18R", 20, "CA", None),
                ("5", "RW18R", 30, "TF", "BOTPU"),
            ],
        )

        iap_rows = self.dst.execute(
            """
            SELECT procedure_identifier, route_type, transition_identifier,
                   seqno, path_termination, waypoint_identifier, waypoint_ref_table
            FROM tbl_pf_iaps WHERE airport_identifier='ZBAA'
            ORDER BY procedure_identifier, route_type, transition_identifier, seqno
            """
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in iap_rows],
            [
                ("I01-Y", "A", "AA122", 10, "IF", "AA122", "PC"),
                ("I01-Y", "I", None, 10, "CF", "RW01", "PG"),
                ("R01-Y", "A", "AA141", 10, "IF", "AA141", "PC"),
                ("R01-Y", "R", None, 10, "TF", "FINAL", "PC"),
            ],
        )

    def test_as346_iap_ctl_uses_official_default(self):
        self.dst.execute("ALTER TABLE tbl_pf_iaps ADD COLUMN ctl")
        self.src.execute(
            "INSERT INTO Terminals VALUES (?,?,?,?,?,?,?,?,?)",
            (20, 1, "3", "ZBAA", None, "I01-Y", "01", None, None),
        )
        self.src.execute(
            "INSERT INTO TerminalLegs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 20, "I", "", "CF", None, 40.05, 116.61, None,
             None, None, None, None, None, None, None, "MAP", None,
             None, None, None, "G  M"),
        )

        convert_procedures(
            self.src, self.dst, {1: "ZBAA"}, {}, {}, {}
        )

        self.assertEqual(
            tuple(self.dst.execute(
                "SELECT ctl, altitude_description "
                "FROM tbl_pf_iaps WHERE airport_identifier='ZBAA'"
            ).fetchone()),
            ("N", None),
        )

    def test_normalizes_fixed_fields_and_dependent_icao_codes(self):
        self.src.execute(
            "INSERT INTO Terminals VALUES (?,?,?,?,?,?,?,?,?)",
            (60, 1, "2", "ZBAA", None, "FIX1D", "01", None, None),
        )
        self.src.executemany(
            "INSERT INTO TerminalLegs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, 60, "5", "ALL", "IF", 101, 40.0, 116.0, None,
                 None, None, None, None, None, None, None, None, None,
                 None, None, None, "E"),
                (2, 60, "5", "ALL", "CA", None, None, None, None,
                 None, None, None, None, None, 180.0, None, None, None,
                 None, None, None, None),
            ],
        )

        convert_procedures(
            self.src,
            self.dst,
            {1: "ZBAA"},
            {},
            {101: {"ident": "FIX01", "lat": 40.0, "lon": 116.0,
                   "name": ""}},
            {},
        )

        rows = self.dst.execute(
            """
            SELECT waypoint_description_code, waypoint_identifier,
                   waypoint_icao_code, recommended_navaid,
                   recommended_navaid_icao_code, center_waypoint,
                   center_waypoint_icao_code
            FROM tbl_pd_sids
            WHERE airport_identifier='ZBAA'
            ORDER BY seqno
            """
        ).fetchall()
        self.assertEqual(rows[0]["waypoint_description_code"], "E   ")
        self.assertEqual(rows[0]["waypoint_icao_code"], "ZB")
        self.assertIsNone(rows[0]["recommended_navaid_icao_code"])
        self.assertIsNone(rows[0]["center_waypoint_icao_code"])
        self.assertIsNone(rows[1]["waypoint_identifier"])
        self.assertIsNone(rows[1]["waypoint_icao_code"])

    def test_rf_leg_derives_radius_and_arc_distance_from_center(self):
        self.src.execute(
            "INSERT INTO Terminals VALUES (?,?,?,?,?,?,?,?,?)",
            (40, 1, "3", "ZBAA", None, "R01", "01", None, None),
        )
        self.src.executemany(
            "INSERT INTO TerminalLegs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, 40, "A", "START", "IF", None, 0.0, 1.0, None,
                 None, None, None, None, None, None, None, None, None,
                 None, None, None, "E   "),
                (2, 40, "A", "START", "RF", None, 1.0, 0.0, "L",
                 None, None, None, None, None, None, None, None, None,
                 None, 0.0, 0.0, "E   "),
                (3, 40, "A", "START", "RF", None, 1.0, 0.0, "L",
                 None, None, None, None, None, None, None, None, None,
                 None, 0.0, 0.0, "E   "),
            ],
        )

        convert_procedures(
            self.src, self.dst, {1: "ZBAA"}, {}, {}, {}
        )

        row = self.dst.execute(
            """
            SELECT route_distance_holding_distance_time, arc_radius,
                   distance_time
            FROM tbl_pf_iaps
            WHERE airport_identifier='ZBAA' AND path_termination='RF'
            """
        ).fetchone()
        self.assertEqual(row["route_distance_holding_distance_time"], "D")
        self.assertAlmostEqual(row["arc_radius"], 60.04, places=2)
        self.assertAlmostEqual(row["distance_time"], 94.3, places=1)
        self.assertEqual(
            self.dst.execute(
                "SELECT COUNT(*) FROM tbl_pf_iaps "
                "WHERE airport_identifier='ZBAA' AND path_termination='RF'"
            ).fetchone()[0],
            1,
        )

    def test_rf_common_section_uses_shared_transition_endpoint(self):
        self.src.execute(
            "INSERT INTO Terminals VALUES (?,?,?,?,?,?,?,?,?)",
            (50, 1, "2", "ZBAA", None, "ARC1D", "01", None, None),
        )
        self.src.executemany(
            "INSERT INTO TerminalLegs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, 50, "4", "RW01", "IF", None, 0.0, 1.0, None,
                 None, None, None, None, None, None, None, None, None,
                 None, None, None, "E   "),
                (2, 50, "4", "RW01", "RF", None, 1.0, 0.0, "L",
                 None, None, None, None, None, None, None, None, None,
                 None, 0.0, 0.0, "E   "),
                (3, 50, "4", "RW02", "IF", None, 0.0, 1.0, None,
                 None, None, None, None, None, None, None, None, None,
                 None, None, None, "E   "),
                (4, 50, "4", "RW02", "RF", None, 1.0, 0.0, "L",
                 None, None, None, None, None, None, None, None, None,
                 None, 0.0, 0.0, "E   "),
                (5, 50, "5", "ALL", "RF", None, 0.0, -1.0, None,
                 None, None, None, None, None, None, None, None, None,
                 None, 0.0, 0.0, "E   "),
            ],
        )

        convert_procedures(
            self.src, self.dst, {1: "ZBAA"}, {}, {}, {}
        )

        row = self.dst.execute(
            """
            SELECT turn_direction, arc_radius, distance_time
            FROM tbl_pd_sids
            WHERE airport_identifier='ZBAA' AND route_type='5'
            """
        ).fetchone()
        self.assertEqual(row["turn_direction"], "L")
        self.assertAlmostEqual(row["arc_radius"], 60.04, places=2)
        self.assertAlmostEqual(row["distance_time"], 94.3, places=1)


if __name__ == "__main__":
    unittest.main()

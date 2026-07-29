import sqlite3
import unittest

from tables.airways import TBL_ER_COLUMNS, convert_airways


class AirwayConversionTests(unittest.TestCase):
    def setUp(self):
        self.src = sqlite3.connect(":memory:")
        self.src.row_factory = sqlite3.Row
        self.src.executescript(
            """
            CREATE TABLE Airways (ID INTEGER, Ident TEXT);
            CREATE TABLE AirwayLegs (
                ID INTEGER, AirwayID INTEGER, Level TEXT,
                Waypoint1ID INTEGER, Waypoint2ID INTEGER,
                IsStart INTEGER, IsEnd INTEGER
            );
            """
        )
        self.dst = sqlite3.connect(":memory:")
        self.dst.row_factory = sqlite3.Row
        columns = ", ".join(f'"{column}"' for column in TBL_ER_COLUMNS)
        self.dst.execute(f"CREATE TABLE tbl_er_enroute_airways ({columns})")

    def tearDown(self):
        self.src.close()
        self.dst.close()

    def test_missing_intermediate_waypoint_does_not_create_long_jump(self):
        self.src.execute("INSERT INTO Airways VALUES (1, 'A1')")
        self.src.executemany(
            "INSERT INTO AirwayLegs VALUES (?,?,?,?,?,?,?)",
            [
                (1, 1, "H", 1, 2, 1, 0),
                (2, 1, "H", 2, 3, 0, 1),
            ],
        )
        lookup = {
            1: {"ident": "START", "lat": 30.0, "lon": 100.0, "name": ""},
            3: {"ident": "END", "lat": 40.0, "lon": 120.0, "name": ""},
        }

        convert_airways(self.src, self.dst, lookup, {})

        rows = self.dst.execute(
            "SELECT waypoint_identifier, inbound_distance "
            "FROM tbl_er_enroute_airways ORDER BY seqno"
        ).fetchall()
        self.assertEqual([row[0] for row in rows], ["START", "END"])
        self.assertTrue(all(row[1] is None for row in rows))

    def test_skips_route_identifier_that_as346_cannot_represent(self):
        self.src.execute("INSERT INTO Airways VALUES (1, 'FANS-1')")
        self.src.execute(
            "INSERT INTO AirwayLegs VALUES (1,1,'H',1,2,1,1)"
        )
        lookup = {
            1: {"ident": "START", "lat": 30.0, "lon": 100.0, "name": ""},
            2: {"ident": "END", "lat": 31.0, "lon": 101.0, "name": ""},
        }

        convert_airways(self.src, self.dst, lookup, {})

        self.assertEqual(
            self.dst.execute("SELECT COUNT(*) FROM tbl_er_enroute_airways").fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()

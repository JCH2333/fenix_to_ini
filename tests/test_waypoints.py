import sqlite3
import unittest

from tables.waypoints import (
    TBL_EA_COLUMNS,
    TBL_PC_COLUMNS,
    convert_waypoints,
)


class FakeRegionLookup:
    def __init__(self, waypoint_regions):
        self.waypoint_regions = waypoint_regions

    def get_waypoint_icao(self, ident):
        return self.waypoint_regions.get(ident)


class WaypointConversionTests(unittest.TestCase):
    def setUp(self):
        self.src = sqlite3.connect(":memory:")
        self.src.row_factory = sqlite3.Row
        self.dst = sqlite3.connect(":memory:")
        self.dst.row_factory = sqlite3.Row
        self.src.executescript(
            """
            CREATE TABLE Airports (
                ID INTEGER, ICAO TEXT, Latitude REAL, Longtitude REAL
            );
            CREATE TABLE Terminals (ID INTEGER, AirportID INTEGER);
            CREATE TABLE TerminalLegs (TerminalID INTEGER, WptID INTEGER);
            CREATE TABLE Waypoints (
                ID INTEGER, Ident TEXT, Collocated INTEGER, Name TEXT,
                Latitude REAL, Longtitude REAL, NavaidID INTEGER
            );
            """
        )
        ea_columns = ", ".join(f'"{column}"' for column in TBL_EA_COLUMNS)
        pc_columns = ", ".join(f'"{column}"' for column in TBL_PC_COLUMNS)
        self.dst.execute(
            f"CREATE TABLE tbl_ea_enroute_waypoints ({ea_columns})"
        )
        self.dst.execute(
            f"CREATE TABLE tbl_pc_terminal_waypoints ({pc_columns})"
        )

    def tearDown(self):
        self.src.close()
        self.dst.close()

    def test_uses_naip_membership_instead_of_broad_bounding_box(self):
        self.src.execute(
            "INSERT INTO Airports VALUES (?, ?, ?, ?)",
            (1, "ZUNZ", 29.3, 94.3),
        )
        self.src.execute("INSERT INTO Terminals VALUES (?, ?)", (10, 1))
        self.src.execute("INSERT INTO TerminalLegs VALUES (?, ?)", (10, 2))
        self.src.executemany(
            "INSERT INTO Waypoints VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "CNFIX", 0, "China enroute", 40.0, 116.0, None),
                (2, "TERM1", 0, "ZUNZ terminal", 29.4, 94.4, None),
                (3, "DUGIN", 0, "Afghanistan", 35.616, 71.516, None),
            ],
        )

        waypoint_lookup, terminal_ids = convert_waypoints(
            self.src,
            self.dst,
            {1: "ZUNZ"},
            FakeRegionLookup({"CNFIX": "ZB"}),
        )

        enroute = self.dst.execute(
            "SELECT waypoint_identifier FROM tbl_ea_enroute_waypoints "
            "ORDER BY waypoint_identifier"
        ).fetchall()
        terminal = self.dst.execute(
            "SELECT waypoint_identifier FROM tbl_pc_terminal_waypoints "
            "ORDER BY waypoint_identifier"
        ).fetchall()

        self.assertEqual([row[0] for row in enroute], ["CNFIX"])
        self.assertEqual([row[0] for row in terminal], ["TERM1"])
        self.assertEqual(set(waypoint_lookup), {1, 2})
        self.assertEqual(terminal_ids, {2})


if __name__ == "__main__":
    unittest.main()

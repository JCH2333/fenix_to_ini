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
            CREATE TABLE TerminalLegs (
                TerminalID INTEGER, WptID INTEGER, CenterID INTEGER
            );
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
        self.src.executemany(
            "INSERT INTO Airports VALUES (?, ?, ?, ?)",
            [
                (2, "ZLXX", 29.2, 94.2),
                (1, "ZUNZ", 29.3, 94.3),
            ],
        )
        self.src.execute("INSERT INTO Terminals VALUES (?, ?)", (10, 1))
        self.src.executemany(
            "INSERT INTO TerminalLegs VALUES (?, ?, ?)",
            [(10, 2, None), (10, 2, 4)],
        )
        self.src.executemany(
            "INSERT INTO Waypoints VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "CNFIX", 0, "China enroute", 40.0, 116.0, None),
                (2, "TERM1", 0, "ZUNZ terminal", 29.4, 94.4, None),
                (3, "DUGIN", 0, "Afghanistan", 35.616, 71.516, None),
                (4, "CTR01", 0, "RF center", 29.45, 94.45, None),
            ],
        )
        self.dst.execute(
            "INSERT INTO tbl_ea_enroute_waypoints VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("EEU", "ASIA", "CHINA", None, "ZB", 0.2, "CNFIX",
             40.0, 116.0, "OFFICIAL EA", "R  ", " B"),
        )
        self.dst.execute(
            "INSERT INTO tbl_pc_terminal_waypoints VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("EEU", "ASIA", "CHINA", "WGE", "ZU", -0.1, "ZUNZ",
             "TERM1", 29.4, 94.4, "OFFICIAL PC", "W Z"),
        )

        waypoint_lookup, terminal_ids = convert_waypoints(
            self.src,
            self.dst,
            {2: "ZLXX", 1: "ZUNZ"},
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
        self.assertEqual([row[0] for row in terminal], ["CTR01", "TERM1"])
        self.assertEqual(set(waypoint_lookup), {1, 2, 4})
        self.assertEqual(terminal_ids, {2, 4})
        self.assertEqual(
            waypoint_lookup[1],
            {
                "ident": "CNFIX",
                "lat": 40.0,
                "lon": 116.0,
                "name": "China enroute",
                "navaid_id": None,
                "icao_code": "ZB",
                "region_code": "ZB",
                "ref_table": "EA",
            },
        )
        self.assertEqual(waypoint_lookup[2]["ref_table"], "PC")
        self.assertEqual(waypoint_lookup[4]["ref_table"], "PC")
        self.assertEqual(waypoint_lookup[2]["icao_code"], "ZU")
        self.assertEqual(waypoint_lookup[4]["icao_code"], "ZU")
        ea_row = self.dst.execute(
            "SELECT * FROM tbl_ea_enroute_waypoints "
            "WHERE waypoint_identifier='CNFIX'"
        ).fetchone()
        self.assertEqual(ea_row["continent"], "ASIA")
        self.assertEqual(ea_row["country"], "CHINA")
        self.assertEqual(ea_row["magnetic_variation"], 0.2)
        self.assertEqual(ea_row["waypoint_name"], "OFFICIAL EA")
        self.assertEqual(ea_row["waypoint_type"], "R  ")
        self.assertEqual(ea_row["waypoint_usage"], " B")
        pc_rows = {
            row["waypoint_identifier"]: row
            for row in self.dst.execute(
                "SELECT * FROM tbl_pc_terminal_waypoints"
            )
        }
        self.assertEqual(pc_rows["TERM1"]["region_code"], "ZUNZ")
        self.assertEqual(pc_rows["TERM1"]["waypoint_name"], "OFFICIAL PC")
        self.assertEqual(pc_rows["TERM1"]["waypoint_type"], "W Z")
        self.assertEqual(pc_rows["CTR01"]["region_code"], "ZUNZ")
        self.assertEqual(pc_rows["CTR01"]["waypoint_type"], "W Z")


if __name__ == "__main__":
    unittest.main()

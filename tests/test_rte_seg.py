import sqlite3
import unittest

from rte_seg import merge_rte_seg_to_airways, parse_dms
from tables.airways import TBL_ER_COLUMNS


class RteSegmentTests(unittest.TestCase):
    def test_parses_six_digit_latitude_as_dms(self):
        self.assertAlmostEqual(parse_dms("N271940"), 27.3277778, places=6)
        self.assertAlmostEqual(parse_dms("S271940.50"), -27.3279167, places=6)
        self.assertAlmostEqual(parse_dms("E1132427"), 113.4075, places=6)
        self.assertAlmostEqual(parse_dms("W0731500"), -73.25, places=6)

    def test_rejects_invalid_dms_ranges(self):
        self.assertIsNone(parse_dms("N916000"))
        self.assertIsNone(parse_dms("E1810000"))
        self.assertIsNone(parse_dms("N2719"))

    def test_deduplicates_official_route_point_with_different_seqno(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        columns = ", ".join(f'"{column}"' for column in TBL_ER_COLUMNS)
        conn.execute(f"CREATE TABLE tbl_er_enroute_airways ({columns})")
        conn.execute(
            "INSERT INTO tbl_er_enroute_airways "
            "(route_identifier, seqno, waypoint_identifier, "
            "waypoint_latitude, waypoint_longitude) VALUES (?,?,?,?,?)",
            ("W45", 5490, "NUPTI", 26.9175, 111.418333),
        )
        segment = {
            "route_ident": "W45",
            "start_ident": "NUPTI",
            "start_lat": 26.9175,
            "start_lon": 111.418333,
            "start_ref": "EA",
            "end_ident": "VESUX",
            "end_lat": 25.763611,
            "end_lon": 113.061667,
            "end_ref": "EA",
            "valid_track": 128.0,
            "mag_track": 131.0,
            "reverse_track": 308.0,
        }

        self.assertEqual(merge_rte_seg_to_airways(conn, [segment]), 0)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM tbl_er_enroute_airways").fetchone()[0],
            1,
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()

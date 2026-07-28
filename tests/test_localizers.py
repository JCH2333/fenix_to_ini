import sqlite3
import unittest

from tables.localizers import TBL_PI_COLUMNS, convert_localizers


class LocalizerConversionTests(unittest.TestCase):
    def setUp(self):
        self.src = sqlite3.connect(":memory:")
        self.src.row_factory = sqlite3.Row
        self.dst = sqlite3.connect(":memory:")
        self.dst.row_factory = sqlite3.Row
        self.src.execute(
            """
            CREATE TABLE ILSes (
                ID INTEGER, RunwayID INTEGER, Freq INTEGER, GsAngle REAL,
                Latitude REAL, Longtitude REAL, Category TEXT, Ident TEXT,
                LocCourse REAL, CrossingHeight REAL, HasDme INTEGER,
                Elevation INTEGER
            )
            """
        )
        definitions = ", ".join(f'"{column}"' for column in TBL_PI_COLUMNS)
        self.dst.execute(
            f'CREATE TABLE tbl_pi_localizers_glideslopes ({definitions})'
        )

    def tearDown(self):
        self.src.close()
        self.dst.close()

    def _insert_target(self, airport, ident, frequency):
        values = {column: None for column in TBL_PI_COLUMNS}
        values.update(
            airport_identifier=airport,
            llz_identifier=ident,
            runway_identifier="RW01",
            llz_frequency=frequency,
        )
        placeholders = ",".join("?" for _ in TBL_PI_COLUMNS)
        self.dst.execute(
            f"INSERT INTO tbl_pi_localizers_glideslopes VALUES ({placeholders})",
            [values[column] for column in TBL_PI_COLUMNS],
        )

    def test_replaces_chinese_rows_and_removes_rejected_frequencies(self):
        self._insert_target("ZBAA", "BAD", 999.0)
        self._insert_target("KJFK", "KEEP", 110.9)
        self.src.executemany(
            "INSERT INTO ILSes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, 100, 17903616, 3.0, 40.0, 116.0, "1", "GOOD", 10.0, None, 0, 100),
                (2, 100, 0, 3.0, 40.0, 116.0, "1", "REJECT", 10.0, None, 0, 100),
            ],
        )

        convert_localizers(
            self.src,
            self.dst,
            {1: "ZBAA"},
            {100: {"icao": "ZBAA", "ident": "RW01"}},
        )

        rows = self.dst.execute(
            "SELECT airport_identifier, llz_identifier, llz_frequency "
            "FROM tbl_pi_localizers_glideslopes ORDER BY airport_identifier"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [("KJFK", "KEEP", 110.9), ("ZBAA", "GOOD", 111.3)],
        )


if __name__ == "__main__":
    unittest.main()

import sqlite3
import unittest

from verify import check_row_counts


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


if __name__ == "__main__":
    unittest.main()

import sqlite3
import unittest

from db_utils import batch_merge_by_coordinates, batch_upsert


class DatabaseMergeTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_key_merge_preserves_existing_duplicate_rows(self):
        self.conn.execute("CREATE TABLE items (ident TEXT, value TEXT)")
        self.conn.executemany(
            "INSERT INTO items VALUES (?, ?)",
            [("DUP", "first"), ("DUP", "second")],
        )

        batch_upsert(
            self.conn,
            "items",
            ["ident", "value"],
            [("DUP", "updated")],
            ["ident"],
        )

        rows = self.conn.execute(
            "SELECT ident, value FROM items ORDER BY rowid"
        ).fetchall()
        self.assertEqual(rows, [("DUP", "updated"), ("DUP", "second")])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='index' AND name LIKE 'idx_upsert_%'"
            ).fetchone()[0],
            0,
        )

    def test_coordinate_merge_updates_nearby_point_and_inserts_new_point(self):
        self.conn.execute(
            "CREATE TABLE points (ident TEXT, region TEXT, lat REAL, lon REAL)"
        )
        self.conn.executemany(
            "INSERT INTO points VALUES (?, ?, ?, ?)",
            [
                ("SAME", "OLD", 40.0, 116.0),
                ("SAME", "FAR", 30.0, 100.0),
            ],
        )

        batch_merge_by_coordinates(
            self.conn,
            "points",
            ["ident", "region", "lat", "lon"],
            [
                ("SAME", "NEW", 40.00001, 116.00001),
                ("MISSING", "NEW", 31.0, 101.0),
            ],
            "ident",
            "lat",
            "lon",
        )

        rows = self.conn.execute(
            "SELECT ident, region FROM points ORDER BY rowid"
        ).fetchall()
        self.assertEqual(
            rows,
            [("SAME", "NEW"), ("SAME", "FAR"), ("MISSING", "NEW")],
        )


if __name__ == "__main__":
    unittest.main()

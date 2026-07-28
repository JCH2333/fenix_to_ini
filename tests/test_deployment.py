import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db_utils import open_target
from deployment import update_package_layout
from verify import check_runtime_compatibility


class DeploymentCompatibilityTests(unittest.TestCase):
    def test_target_database_uses_delete_journal_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "db.s3db"
            sqlite3.connect(db_path).close()

            conn = open_target(str(db_path))
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.close()

            reopened = sqlite3.connect(db_path)
            persisted_mode = reopened.execute("PRAGMA journal_mode").fetchone()[0]
            reopened.close()

        self.assertEqual(mode.lower(), "delete")
        self.assertEqual(persisted_mode.lower(), "delete")

    def test_updates_bundled_files_in_package_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "inibuilds-aircraft-a340"
            bundled = package / "Navigraph" / "BundledData"
            bundled.mkdir(parents=True)
            db_path = bundled / "ng_jeppesen_fwdfd_2303.s3db"
            cycle_path = bundled / "cycle.json"
            db_path.write_bytes(b"database-data")
            cycle_path.write_text('{"cycle":"2607"}', encoding="utf-8")
            layout_path = package / "layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "content": [
                            {
                                "path": "navigraph/bundleddata/ng_jeppesen_fwdfd_2303.s3db",
                                "size": 1,
                                "date": 1,
                            },
                            {
                                "path": "navigraph/bundleddata/cycle.json",
                                "size": 1,
                                "date": 1,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            updated = update_package_layout(str(db_path), str(cycle_path))
            content = json.loads(layout_path.read_text(encoding="utf-8"))["content"]

        self.assertEqual(Path(updated), layout_path)
        self.assertEqual(content[0]["size"], len(b"database-data"))
        self.assertEqual(content[1]["size"], len('{"cycle":"2607"}'.encode()))
        self.assertGreater(content[0]["date"], 1)

    def test_runtime_check_rejects_cycle_json_header_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "db.s3db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                CREATE TABLE tbl_hdr_header (
                    cycle, revision, effective_fromto
                )
                """
            )
            conn.execute(
                "INSERT INTO tbl_hdr_header VALUES ('2303', '001', '2303190423')"
            )
            conn.commit()
            (Path(temp_dir) / "cycle.json").write_text(
                '{"cycle":"2607","revision":"2"}', encoding="utf-8"
            )

            self.assertFalse(check_runtime_compatibility(conn, str(db_path)))
            conn.close()


if __name__ == "__main__":
    unittest.main()

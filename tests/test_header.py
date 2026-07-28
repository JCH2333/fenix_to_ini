import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from main import _copy_target_cycle_json, _write_cycle_json
from tables.header import convert_header


class HeaderConversionTests(unittest.TestCase):
    def test_isolated_output_inherits_target_cycle_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "target"
            output_dir = root / "output"
            source_dir.mkdir()
            output_dir.mkdir()
            source_db = source_dir / "nav.s3db"
            output_db = output_dir / "converted.s3db"
            source_db.touch()
            output_db.touch()
            source_cycle = source_dir / "cycle.json"
            source_cycle.write_text(
                '{"cycle":"2607","revision":"1","name":"ToLiss"}',
                encoding="utf-8",
            )
            (output_dir / "cycle.json").write_text(
                '{"name":"iniBuilds DFD v2"}', encoding="utf-8"
            )

            copied = _copy_target_cycle_json(str(source_db), str(output_db))
            payload = json.loads(Path(copied).read_text(encoding="utf-8"))

        self.assertEqual(payload["name"], "ToLiss")
        self.assertEqual(payload["cycle"], "2607")

    def test_splits_fenix_revision_from_four_digit_cycle(self):
        source = sqlite3.connect(":memory:")
        source.row_factory = sqlite3.Row
        source.execute("CREATE TABLE config (key TEXT, val TEXT)")
        source.executemany(
            "INSERT INTO config VALUES (?, ?)",
            [
                ("CycleName", "2607n2"),
                ("CycleStartDate", "09JUL26"),
                ("CycleEndDate", "05AUG26"),
            ],
        )
        target = sqlite3.connect(":memory:")
        target.row_factory = sqlite3.Row
        target.execute(
            """
            CREATE TABLE tbl_hdr_header (
                creator, cycle, data_provider, dataset_version, dataset,
                effective_fromto, parsed_at, revision
            )
            """
        )
        target.execute(
            "INSERT INTO tbl_hdr_header VALUES (?,?,?,?,?,?,?,?)",
            ("Navigraph", "2303", "JEPPESEN", "2.0.24.1017", "NG_FWDFD",
             "2303190423", "2024-11-01 05:55:07Z", "001"),
        )

        cycle_info = convert_header(source, target)
        row = target.execute(
            "SELECT cycle, effective_fromto, revision FROM tbl_hdr_header"
        ).fetchone()

        self.assertEqual(tuple(row), ("2607", "0907050826", "002"))
        self.assertEqual(cycle_info["cycle"], "2607")
        self.assertEqual(cycle_info["revision"], "2")

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "db.s3db"
            db_path.touch()
            (Path(temp_dir) / "cycle.json").write_text(
                '{"cycle":"2607","revision":"1","name":"ToLiss",'
                '"format":"dfdv2"}',
                encoding="utf-8",
            )
            cycle_path = Path(_write_cycle_json(str(db_path), cycle_info))
            payload = json.loads(cycle_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["cycle"], "2607")
        self.assertEqual(payload["revision"], "2")
        self.assertEqual(payload["name"], "ToLiss")
        self.assertEqual(payload["validityPeriod"], "2026-07-09/2026-08-05")

        source.close()
        target.close()


if __name__ == "__main__":
    unittest.main()

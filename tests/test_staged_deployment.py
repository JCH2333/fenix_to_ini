"""Regression tests for staged navigation-data deployment."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deployment import deploy_staged_database
import staging


def create_database(path: Path, value: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def read_value(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT value FROM sample").fetchone()[0]
    finally:
        connection.close()


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.staged_dir = self.root / "staged"
        self.staged_dir.mkdir()
        self.staged = self.staged_dir / "fenix_naip_dfdv2.s3db"
        create_database(self.staged, "staged")
        (self.staged_dir / "cycle.json").write_text(
            json.dumps({"cycle": "2607", "revision": "2", "name": "暂存"}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_staging_path_is_stable_and_ignored_output_location(self):
        with patch.object(staging, "STAGING_DIRECTORY", self.root / "output" / "staged"):
            self.assertEqual(
                self.root / "output" / "staged" / "fenix_naip_dfdv2.s3db",
                staging.staging_database_path(),
            )

    def test_ini_deployment_copies_database_metadata_and_updates_layout(self):
        package = self.root / "Community" / "inibuilds-aircraft-a340"
        navigation = package / "Navigraph" / "BundledData"
        navigation.mkdir(parents=True)
        target = navigation / "db.s3db"
        create_database(target, "official")
        cycle = navigation / "cycle.json"
        cycle.write_text(json.dumps({"name": "iniBuilds DFD v2"}), encoding="utf-8")
        layout = package / "layout.json"
        layout.write_text(json.dumps({"content": [
            {"path": "Navigraph/BundledData/db.s3db", "size": 1, "date": 1},
            {"path": "Navigraph/BundledData/cycle.json", "size": 1, "date": 1},
        ]}), encoding="utf-8")

        result = deploy_staged_database(
            self.staged, "ini_a340", [str(target)],
            backup_root=self.root / "backups", require_simulator_closed=False,
        )

        self.assertEqual("staged", read_value(target))
        written_cycle = json.loads(cycle.read_text(encoding="utf-8"))
        self.assertEqual("2607", written_cycle["cycle"])
        self.assertEqual("iniBuilds DFD v2", written_cycle["name"])
        self.assertTrue((result.backup_directory / "target_1" / "db.s3db").is_file())
        updated = json.loads(layout.read_text(encoding="utf-8"))
        self.assertGreater(updated["content"][0]["size"], 1)

    def test_as346_sanitizes_private_copy_only(self):
        target = self.root / "ng_jeppesen_fwdfd_2607.s3db"
        create_database(target, "official")
        target.with_name("cycle.json").write_text(
            json.dumps({"name": "ToLiss", "format": "dfdv2"}), encoding="utf-8"
        )
        with patch("deployment.sanitize_toliss_data", return_value={}) as sanitize:
            result = deploy_staged_database(
                self.staged, "as346", [str(target)],
                backup_root=self.root / "backups", require_simulator_closed=False,
            )
        self.assertTrue(sanitize.called)
        self.assertEqual("staged", read_value(target))
        cycle = json.loads(target.with_name("cycle.json").read_text(encoding="utf-8"))
        self.assertEqual("2607", cycle["cycle"])
        self.assertEqual("ToLiss", cycle["name"])
        self.assertTrue(result.sha256)

    def test_failure_restores_database_cycle_and_layout(self):
        package = self.root / "Community" / "inibuilds-aircraft-a350"
        navigation = package / "Navigraph" / "BundledData"
        navigation.mkdir(parents=True)
        target = navigation / "db.s3db"
        create_database(target, "official")
        cycle = navigation / "cycle.json"
        cycle.write_text(json.dumps({"cycle": "2606"}), encoding="utf-8")
        layout = package / "layout.json"
        original_layout = json.dumps({"content": [
            {"path": "Navigraph/BundledData/db.s3db", "size": 1, "date": 1},
        ]})
        layout.write_text(original_layout, encoding="utf-8")

        with self.assertRaises(ValueError):
            deploy_staged_database(
                self.staged, "ini_a350", [str(target)],
                backup_root=self.root / "backups", require_simulator_closed=False,
            )

        self.assertEqual("official", read_value(target))
        self.assertEqual({"cycle": "2606"}, json.loads(cycle.read_text(encoding="utf-8")))
        self.assertEqual(original_layout, layout.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

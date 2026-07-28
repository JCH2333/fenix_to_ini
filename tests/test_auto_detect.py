import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_detect import detect_as346_s3db, detect_inibuilds_s3db


class IniBuildsDetectionTests(unittest.TestCase):
    def test_prefers_community_bundled_database_over_wasm_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            appdata = root / "AppData"
            packages = root / "Packages"
            bundled = (
                packages / "Community" / "inibuilds-aircraft-a340" /
                "Navigraph" / "BundledData" / "ng_jeppesen_fwdfd_2303.s3db"
            )
            bundled.parent.mkdir(parents=True)
            bundled.touch()
            config_dir = appdata / "Microsoft Flight Simulator 2024"
            config_dir.mkdir(parents=True)
            (config_dir / "UserCfg.opt").write_text(
                f'InstalledPackagesPath "{packages}"', encoding="utf-8"
            )
            wasm = (
                config_dir / "WASM" / "MSFS2024" / "inibuilds-aircraft-a340" /
                "work" / "NavigationData" / "db.s3db"
            )
            wasm.parent.mkdir(parents=True)
            wasm.touch()

            with patch.dict(os.environ, {"APPDATA": str(appdata)}):
                results = detect_inibuilds_s3db()

        first_path = next(iter(results.values()))
        self.assertEqual(Path(first_path), bundled)
        self.assertIn("BundledData", next(iter(results)))

    def test_detects_latest_as346_downloaded_cycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            appdata = Path(temp_dir) / "AppData"
            fms_data = (
                appdata / "Microsoft Flight Simulator 2024" / "WASM" /
                "MSFS2024" / "aerosoft-aircraft-a346-pro" / "work" /
                "FMSData"
            )
            old_db = fms_data / "cycle_2605" / "ng_jeppesen_fwdfd_2605.s3db"
            new_db = fms_data / "cycle_2607" / "ng_jeppesen_fwdfd_2607.s3db"
            old_db.parent.mkdir(parents=True)
            new_db.parent.mkdir(parents=True)
            old_db.touch()
            new_db.touch()

            with patch.dict(os.environ, {"APPDATA": str(appdata)}):
                results = detect_as346_s3db()

        self.assertEqual(Path(next(iter(results.values()))), new_db)
        self.assertIn("2607", next(iter(results)))


if __name__ == "__main__":
    unittest.main()

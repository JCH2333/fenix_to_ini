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

            with patch.dict(os.environ, {
                "APPDATA": str(appdata),
                "LOCALAPPDATA": str(root / "LocalAppData"),
            }):
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

            with patch.dict(os.environ, {
                "APPDATA": str(appdata),
                "LOCALAPPDATA": str(Path(temp_dir) / "LocalAppData"),
            }):
                results = detect_as346_s3db()

        self.assertEqual(Path(next(iter(results.values()))), new_db)
        self.assertIn("2607", next(iter(results)))

    def test_detects_store_xbox_default_community_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            localappdata = root / "LocalAppData"
            bundled = (
                localappdata / "Packages" /
                "Microsoft.Limitless_8wekyb3d8bbwe" / "LocalCache" /
                "Packages" / "Community" / "inibuilds-aircraft-a340" /
                "Navigraph" / "BundledData" /
                "ng_jeppesen_fwdfd_2607.s3db"
            )
            bundled.parent.mkdir(parents=True)
            bundled.touch()

            with patch.dict(os.environ, {
                "APPDATA": str(root / "AppData"),
                "LOCALAPPDATA": str(localappdata),
            }):
                results = detect_inibuilds_s3db()

        self.assertEqual(Path(next(iter(results.values()))), bundled)
        self.assertIn("Store/Xbox", next(iter(results)))

    def test_honors_store_xbox_user_cfg_custom_package_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            localappdata = root / "LocalAppData"
            local_cache = (
                localappdata / "Packages" /
                "Microsoft.Limitless_8wekyb3d8bbwe" / "LocalCache"
            )
            packages = root / "CustomPackages"
            bundled = (
                packages / "inibuilds-aircraft-a340" / "Navigraph" /
                "BundledData" / "ng_jeppesen_fwdfd_2607.s3db"
            )
            bundled.parent.mkdir(parents=True)
            bundled.touch()
            local_cache.mkdir(parents=True)
            (local_cache / "UserCfg.opt").write_text(
                f'InstalledPackagesPath "{packages}"', encoding="utf-8"
            )

            with patch.dict(os.environ, {
                "APPDATA": str(root / "AppData"),
                "LOCALAPPDATA": str(localappdata),
            }):
                results = detect_inibuilds_s3db()

        self.assertIn(bundled, map(Path, results.values()))
        custom_label = next(
            label for label, path in results.items() if Path(path) == bundled
        )
        self.assertIn("Store/Xbox", custom_label)

    def test_detects_store_xbox_as346_cycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            localappdata = root / "LocalAppData"
            store_user_data = (
                localappdata / "Packages" /
                "Microsoft.Limitless_8wekyb3d8bbwe" / "LocalCache" /
                "Packages" / "Microsoft Flight Simulator 2024"
            )
            database = (
                store_user_data / "WASM" / "MSFS2024" /
                "aerosoft-aircraft-a346-pro" / "work" / "FMSData" /
                "cycle_2607" / "ng_jeppesen_fwdfd_2607.s3db"
            )
            database.parent.mkdir(parents=True)
            database.touch()

            with patch.dict(os.environ, {
                "APPDATA": str(root / "AppData"),
                "LOCALAPPDATA": str(localappdata),
            }):
                results = detect_as346_s3db()

        self.assertEqual(Path(next(iter(results.values()))), database)
        self.assertIn("Store/Xbox", next(iter(results)))


if __name__ == "__main__":
    unittest.main()

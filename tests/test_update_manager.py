import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.error import URLError

from update_manager import (
    GITHUB_API_URL,
    ReleaseInfo,
    UpdateError,
    apply_update_package,
    check_for_update,
    download_update,
    is_newer_version,
    validate_update_package,
)


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        if self.offset >= len(self.payload):
            return b""
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


def make_package(path: Path, version="1.6.0", gui_content=b"new gui") -> bytes:
    files = {
        "gui.py": gui_content,
        "main.py": b"new main",
        "update_manager.py": b"new updater",
        "version.py": f'__version__ = "{version}"\n'.encode(),
        "tables/example.py": b"value = 1\n",
    }
    manifest = {
        "version": version,
        "files": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in files.items()
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("update-manifest.json", json.dumps(manifest))
    return path.read_bytes()


class UpdateCheckTests(unittest.TestCase):
    def test_semantic_version_comparison(self):
        self.assertTrue(is_newer_version("v1.6.0", "1.5.0"))
        self.assertFalse(is_newer_version("1.5.0", "1.5.0"))
        self.assertFalse(is_newer_version("1.4.9", "1.5.0"))

    def test_finds_verified_release_asset(self):
        payload = {
            "tag_name": "v1.6.0",
            "name": "v1.6.0",
            "assets": [{
                "name": "fenix_to_ini-v1.6.0.zip",
                "browser_download_url": (
                    "https://github.com/JCH2333/fenix_to_ini/releases/"
                    "download/v1.6.0/fenix_to_ini-v1.6.0.zip"
                ),
                "digest": "sha256:" + "a" * 64,
                "size": 1234,
            }],
        }

        result = check_for_update(
            "1.5.0",
            opener=lambda _request, timeout: FakeResponse(
                json.dumps(payload).encode()
            ),
        )

        self.assertTrue(result.update_available)
        self.assertEqual(result.release.version, "1.6.0")
        self.assertEqual(result.release.asset_sha256, "a" * 64)

    def test_no_update_does_not_require_asset(self):
        payload = {"tag_name": "v1.5.0", "name": "v1.5.0", "assets": []}
        result = check_for_update(
            "1.5.0",
            opener=lambda _request, timeout: FakeResponse(
                json.dumps(payload).encode()
            ),
        )
        self.assertFalse(result.update_available)
        self.assertIsNone(result.error)

    def test_falls_back_to_domestic_mirror(self):
        calls = []
        payload = {"tag_name": "v1.5.0", "name": "v1.5.0", "assets": []}

        def opener(request, timeout):
            calls.append(request.full_url)
            if request.full_url == GITHUB_API_URL:
                raise URLError("blocked")
            return FakeResponse(json.dumps(payload).encode())

        result = check_for_update("1.5.0", opener=opener)
        self.assertIsNone(result.error)
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn(GITHUB_API_URL, calls[1])

    def test_network_failure_is_nonfatal(self):
        result = check_for_update(
            "1.5.0",
            opener=lambda _request, timeout: (_ for _ in ()).throw(
                URLError("offline")
            ),
        )
        self.assertFalse(result.update_available)
        self.assertIn("无法访问", result.error)


class UpdatePackageTests(unittest.TestCase):
    def test_download_falls_back_and_checks_sha256(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package_path = Path(temp_dir) / "update.zip"
            payload = make_package(package_path)
            calls = []
            asset_url = (
                "https://github.com/JCH2333/fenix_to_ini/releases/"
                "download/v1.6.0/fenix_to_ini-v1.6.0.zip"
            )
            release = ReleaseInfo(
                version="1.6.0", tag_name="v1.6.0", name="v1.6.0",
                page_url="https://github.com/JCH2333/fenix_to_ini/releases/tag/v1.6.0",
                asset_name="fenix_to_ini-v1.6.0.zip",
                asset_url=asset_url,
                asset_sha256=hashlib.sha256(payload).hexdigest(),
                asset_size=len(payload),
            )

            def opener(request, timeout):
                calls.append(request.full_url)
                if request.full_url == asset_url:
                    raise URLError("blocked")
                return FakeResponse(payload)

            downloaded = download_update(release, opener=opener)
            try:
                self.assertEqual(downloaded.read_bytes(), payload)
                self.assertGreaterEqual(len(calls), 2)
            finally:
                downloaded.unlink(missing_ok=True)

    def test_installs_with_backup_and_preserves_unmanaged_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install = root / "install"
            install.mkdir()
            (install / "gui.py").write_bytes(b"old gui")
            (install / "user.db3").write_bytes(b"database")
            package = root / "update.zip"
            make_package(package)

            backup = apply_update_package(package, install, "1.6.0")

            self.assertEqual((install / "gui.py").read_bytes(), b"new gui")
            self.assertEqual((backup / "gui.py").read_bytes(), b"old gui")
            self.assertEqual((install / "user.db3").read_bytes(), b"database")
            self.assertTrue((install / "tables" / "example.py").exists())

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "unsafe.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("../outside.py", b"bad")
                archive.writestr("update-manifest.json", json.dumps({
                    "version": "1.6.0",
                    "files": {"../outside.py": hashlib.sha256(b"bad").hexdigest()},
                }))
            with self.assertRaises(UpdateError):
                validate_update_package(package, "1.6.0")


if __name__ == "__main__":
    unittest.main()

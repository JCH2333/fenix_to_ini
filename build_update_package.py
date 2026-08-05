"""Build the verified ZIP asset used by the one-click updater."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from version import __version__


ROOT = Path(__file__).resolve().parent
EXCLUDED_PARTS = {
    ".git", "__pycache__", "backups", "diagnostics_tmp", "dist",
    "output", "tests",
}
EXTRA_FILES = {"README.txt", "requirements.txt"}


def package_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        relative = path.relative_to(ROOT)
        if path.suffix == ".py" or relative.as_posix() in EXTRA_FILES:
            files.append(relative)
    return sorted(files, key=lambda item: item.as_posix())


def build_package(output_dir: Path, version: str = __version__) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"fenix_to_ini-v{version}.zip"
    files = package_files()
    hashes = {
        path.as_posix(): hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in files
    }
    manifest = json.dumps(
        {"version": version, "files": hashes},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in files:
            info = zipfile.ZipInfo(relative.as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (ROOT / relative).read_bytes())
        info = zipfile.ZipInfo("update-manifest.json", (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, manifest)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build one-click update asset")
    parser.add_argument("--output", default=str(ROOT / "dist"))
    parser.add_argument("--version", default=__version__)
    args = parser.parse_args(argv)
    package = build_package(Path(args.output), args.version)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    print(package)
    print(f"SHA-256: {digest}")


if __name__ == "__main__":
    main()

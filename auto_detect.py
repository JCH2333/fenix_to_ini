"""
Auto-detection module for navigation data paths.

Detects Fenix nd.db3 and iniBuilds db.s3db locations for both
MSFS2020 and MSFS2024 installations.
"""

import os
import glob
import sys


def get_appdata() -> str:
    """Get AppData/Roaming path."""
    return os.environ.get('APPDATA',
        os.path.expandvars(r'%USERPROFILE%\AppData\Roaming'))


def detect_fenix_db() -> str | None:
    """
    Detect Fenix A320 nd.db3 path.
    Fenix stores navdata in a fixed location outside the sim.
    """
    paths = [
        r'C:\ProgramData\Fenix\Navdata\nd.db3',
    ]
    for p in paths:
        if os.path.exists(p):
            return os.path.normpath(p)
    return None


def detect_fenix_csv() -> str | None:
    """
    Detect RTE_SEG.csv near Fenix navdata.
    Common locations: same dir as nd.db3, or AI project dir.
    """
    fenix_db = detect_fenix_db()
    if fenix_db:
        # Check same directory
        csv_path = os.path.join(os.path.dirname(fenix_db), 'RTE_SEG.csv')
        if os.path.exists(csv_path):
            return csv_path

    # Check parent directories of the script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for _ in range(3):
        csv_path = os.path.join(script_dir, 'RTE_SEG.csv')
        if os.path.exists(csv_path):
            return csv_path
        script_dir = os.path.dirname(script_dir)

    return None


def detect_inibuilds_s3db() -> dict[str, str]:
    """
    Detect iniBuilds db.s3db for both MSFS2020 and MSFS2024.

    Returns dict mapping {sim_label: s3db_path}, e.g.:
        {'MSFS2024 - inibuilds-aircraft-a340': 'C:/.../db.s3db'}

    MSFS2024 path pattern:
        %AppData%/Microsoft Flight Simulator 2024/WASM/MSFS2024/
            inibuilds-aircraft-*/work/NavigationData/db.s3db

    MSFS2020 path pattern:
        %AppData%/Microsoft Flight Simulator/packages/
            inibuilds-aircraft-*/work/NavigationData/db.s3db
    """
    appdata = get_appdata()
    results = {}

    # --- MSFS2024 ---
    msfs24_base = os.path.join(
        appdata,
        'Microsoft Flight Simulator 2024',
        'WASM',
        'MSFS2024'
    )
    if os.path.isdir(msfs24_base):
        pattern = os.path.join(msfs24_base, 'inibuilds-aircraft-*')
        for ac_dir in glob.glob(pattern):
            if not os.path.isdir(ac_dir):
                continue
            s3db_path = os.path.join(ac_dir, 'work', 'NavigationData', 'db.s3db')
            if os.path.exists(s3db_path):
                ac_name = os.path.basename(ac_dir)
                results[f'MSFS2024 - {ac_name}'] = os.path.normpath(s3db_path)
            else:
                # Check if directory exists but no s3db yet
                nav_dir = os.path.join(ac_dir, 'work', 'NavigationData')
                if os.path.isdir(nav_dir):
                    ac_name = os.path.basename(ac_dir)
                    results[f'MSFS2024 - {ac_name} (目录存在，无s3db)'] = os.path.normpath(nav_dir)

    # --- MSFS2020 ---
    msfs20_base = os.path.join(
        appdata,
        'Microsoft Flight Simulator',
        'packages'
    )
    if os.path.isdir(msfs20_base):
        pattern = os.path.join(msfs20_base, 'inibuilds-aircraft-*')
        for ac_dir in glob.glob(pattern):
            if not os.path.isdir(ac_dir):
                continue
            s3db_path = os.path.join(ac_dir, 'work', 'NavigationData', 'db.s3db')
            if os.path.exists(s3db_path):
                ac_name = os.path.basename(ac_dir)
                results[f'MSFS2020 - {ac_name}'] = os.path.normpath(s3db_path)

    return results


def detect_all() -> dict:
    """
    Run all auto-detection and return results.

    Returns:
        {
            'fenix_db': str or None,
            'fenix_csv': str or None,
            'ini_s3db': dict,  # {sim_label: path}
        }
    """
    return {
        'fenix_db': detect_fenix_db(),
        'fenix_csv': detect_fenix_csv(),
        'ini_s3db': detect_inibuilds_s3db(),
    }


def print_detection_report(results: dict):
    """Print a formatted detection report."""
    print("=" * 60)
    print("  导航数据路径检测报告")
    print("=" * 60)

    # Fenix
    fenix = results.get('fenix_db')
    if fenix:
        print(f"  [OK] Fenix nd.db3: {fenix}")
    else:
        print(f"  [--] Fenix nd.db3: 未找到")

    # CSV
    csv_path = results.get('fenix_csv')
    if csv_path:
        print(f"  [OK] RTE_SEG.csv: {csv_path}")
    else:
        print(f"  [--] RTE_SEG.csv: 未找到")

    # iniBuilds
    ini_results = results.get('ini_s3db', {})
    if ini_results:
        print(f"\n  iniBuilds db.s3db 检测到 {len(ini_results)} 个位置:")
        for label, path in ini_results.items():
            print(f"    [{label}]")
            print(f"      {path}")
    else:
        print(f"\n  [--] iniBuilds db.s3db: 未找到任何位置")

    print("=" * 60)


# ---- CLI test entry point ----
if __name__ == '__main__':
    results = detect_all()
    print_detection_report(results)

    # Also print raw dict for debugging
    if '--verbose' in sys.argv:
        print("\nRaw results:")
        import json
        print(json.dumps(results, indent=2, ensure_ascii=False, default=str))

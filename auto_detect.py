"""
Auto-detection module for navigation data paths.

Detects Fenix nd.db3 and iniBuilds db.s3db locations for both
MSFS2020 and MSFS2024 installations.
"""

import os
import glob
import sys
import sqlite3
import re


# 判定"完整 NAIP 中国程序数据"的最小机场数阈值。
# 普通 Navigraph 订阅版 Fenix 数据仅约 92 个中国机场且几乎没有 SID/STAR/IAP，
# 而含 NAIP 数据的完整版约有 280 个中国机场含程序。
NAIP_COMPLETENESS_THRESHOLD = 100


def get_appdata() -> str:
    """Get AppData/Roaming path."""
    return os.environ.get('APPDATA',
        os.path.expandvars(r'%USERPROFILE%\AppData\Roaming'))


def detect_fenix_db() -> str | None:
    """
    Detect Fenix A320 nd.db3 path.
    Priority:
    1. Script's parent directory (may have NAIP-enhanced version)
    2. C:/ProgramData/Fenix/Navdata/nd.db3 (stock Navigraph)
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Check parent directories of the script (up to 3 levels)
    search_dir = script_dir
    for _ in range(3):
        parent = os.path.dirname(search_dir)
        path = os.path.join(parent, 'nd.db3')
        if os.path.exists(path):
            return os.path.normpath(path)
        search_dir = parent

    # Check ProgramData
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

    # Prefer the Community package's bundled database. iniBuilds copies this
    # file into WASM work/NavigationData during startup, so converting only the
    # work copy is temporary and will be overwritten by the aircraft.
    user_cfg = os.path.join(
        appdata, 'Microsoft Flight Simulator 2024', 'UserCfg.opt'
    )
    package_root = None
    if os.path.isfile(user_cfg):
        with open(user_cfg, 'r', encoding='utf-8', errors='ignore') as handle:
            match = re.search(
                r'InstalledPackagesPath\s+"([^"]+)"', handle.read(), re.IGNORECASE
            )
        if match:
            package_root = os.path.normpath(match.group(1))

    if package_root:
        community_dirs = [package_root]
        community_dirs.extend(
            os.path.join(package_root, name)
            for name in ('Community', 'Community2024')
        )
        for community_dir in community_dirs:
            if not os.path.isdir(community_dir):
                continue
            pattern = os.path.join(community_dir, 'inibuilds-aircraft-*')
            for ac_dir in sorted(glob.glob(pattern)):
                bundled_pattern = os.path.join(
                    ac_dir, 'Navigraph', 'BundledData', '*.s3db'
                )
                bundled_files = sorted(glob.glob(bundled_pattern))
                if bundled_files:
                    ac_name = os.path.basename(ac_dir)
                    label = f'MSFS2024 - {ac_name} (BundledData)'
                    results[label] = os.path.normpath(bundled_files[0])

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


def detect_as346_s3db() -> dict[str, str]:
    """Detect Aerosoft AS346 downloaded and fallback DFDv2 databases."""
    appdata = get_appdata()
    results = {}
    work_dir = os.path.join(
        appdata,
        'Microsoft Flight Simulator 2024',
        'WASM',
        'MSFS2024',
        'aerosoft-aircraft-a346-pro',
        'work',
    )

    cycle_pattern = os.path.join(work_dir, 'FMSData', 'cycle_*', '*.s3db')
    for path in sorted(glob.glob(cycle_pattern), reverse=True):
        cycle_dir = os.path.basename(os.path.dirname(path))
        results[f'MSFS2024 - Aerosoft AS346 ({cycle_dir})'] = os.path.normpath(path)

    for path in sorted(glob.glob(os.path.join(work_dir, '*.s3db'))):
        results['MSFS2024 - Aerosoft AS346 (fallback)'] = os.path.normpath(path)

    return results


def check_naip_completeness(fenix_db_path: str) -> dict:
    """
    检查 Fenix nd.db3 是否包含完整的 NAIP 中国程序数据。

    普通 Navigraph 订阅版本仅有约 92 个中国机场，且几乎没有 SID/STAR/IAP
    进离场程序；只有集成了 NAIP 数据的版本才有约 280 个中国机场含完整程序。

    Args:
        fenix_db_path: Fenix nd.db3 文件路径

    Returns:
        {
            'is_complete': bool,        # 是否判定为完整版
            'cn_airports_with_procs': int,  # 含程序的中国机场数
            'error': str | None,        # 检测出错时的错误信息
        }
    """
    result = {
        'is_complete': False,
        'cn_airports_with_procs': 0,
        'error': None,
    }
    try:
        uri = f"file:{fenix_db_path}?immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        try:
            count = conn.execute(
                "SELECT COUNT(DISTINCT AirportID) FROM Terminals "
                "WHERE ICAO LIKE 'Z%'"
            ).fetchone()[0]
            result['cn_airports_with_procs'] = count
            result['is_complete'] = count >= NAIP_COMPLETENESS_THRESHOLD
        finally:
            conn.close()
    except Exception as e:
        result['error'] = str(e)

    return result


def detect_all() -> dict:
    """
    Run all auto-detection and return results.

    Returns:
        {
            'fenix_db': str or None,
            'fenix_csv': str or None,
            'ini_s3db': dict,  # {sim_label: path}
            'naip_completeness': dict or None,  # 完整性校验结果（仅当检测到 fenix_db 时）
        }
    """
    fenix_db = detect_fenix_db()
    naip_completeness = None
    if fenix_db:
        naip_completeness = check_naip_completeness(fenix_db)

    return {
        'fenix_db': fenix_db,
        'fenix_csv': detect_fenix_csv(),
        'ini_s3db': detect_inibuilds_s3db(),
        'as346_s3db': detect_as346_s3db(),
        'naip_completeness': naip_completeness,
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

        naip = results.get('naip_completeness')
        if naip:
            if naip.get('error'):
                print(f"  [!!] 无法校验 NAIP 完整性: {naip['error']}")
            elif naip['is_complete']:
                print(f"  [OK] NAIP 完整性校验通过: {naip['cn_airports_with_procs']} 个中国机场含进离场程序")
            else:
                print(f"  [警告] 检测到的 Fenix 数据可能不含完整 NAIP 中国程序数据！")
                print(f"         仅 {naip['cn_airports_with_procs']} 个中国机场含进离场程序"
                      f"（完整版通常 >= {NAIP_COMPLETENESS_THRESHOLD} 个）")
                print(f"         这可能是普通 Navigraph 订阅版本，转换后中国机场程序会很少")
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

"""
WMM 磁变量计算模块。

使用 pygeomag 库（世界磁场模型 World Magnetic Model 的 Python 实现）
计算磁偏角，用于修正跑道和 ILS 的真方位/磁方位互相转换。

pygeomag 参考: https://github.com/boxpet/pygeomag
"""

from datetime import date
from typing import Optional

# 尝试导入 pygeomag，如果不可用则回退到占位逻辑
try:
    from pygeomag import GeoMag
    _GEOMAG_AVAILABLE = True
    _geo_mag_instance: Optional["GeoMag"] = None
except ImportError:
    _GEOMAG_AVAILABLE = False
    _geo_mag_instance = None


def _get_geomag_instance():
    """获取（懒加载并复用）GeoMag 计算实例，避免重复初始化模型系数。"""
    global _geo_mag_instance
    if _geo_mag_instance is None:
        _geo_mag_instance = GeoMag()
    return _geo_mag_instance


def _current_decimal_year() -> float:
    """返回当前日期对应的十进制年份（如 2026.57），用于 WMM 时间外推。"""
    today = date.today()
    day_of_year = today.timetuple().tm_yday
    days_in_year = 366 if (today.year % 4 == 0 and (today.year % 100 != 0 or today.year % 400 == 0)) else 365
    return today.year + (day_of_year - 1) / days_in_year


def get_magnetic_declination(lat: float, lon: float, elevation_m: float = 0.0) -> Optional[float]:
    """
    计算给定坐标的磁偏角。

    Args:
        lat: 纬度（度，北纬为正）
        lon: 经度（度，东经为正）
        elevation_m: 海拔高度（米），默认为 0

    Returns:
        磁偏角（度），东偏为正，西偏为负；
        如果计算失败或库不可用则返回 None
    """
    if not _GEOMAG_AVAILABLE:
        return None

    try:
        geo_mag = _get_geomag_instance()
        # pygeomag 的 alt 参数单位是千米（沿用 NOAA 原始 C 实现的约定）
        alt_km = elevation_m / 1000.0
        result = geo_mag.calculate(
            glat=lat, glon=lon, alt=alt_km, time=_current_decimal_year()
        )
        declination = result.d

        # 四舍五入到 0.1 度精度
        return round(declination, 1)

    except Exception as e:
        # 计算失败（例如坐标超出有效范围）
        print(f"  警告：无法计算磁偏角 (lat={lat:.4f}, lon={lon:.4f}): {e}")
        return None


def apply_magnetic_variation(true_bearing: float, mag_var: Optional[float]) -> float:
    """
    将真方位转换为磁方位。

    Args:
        true_bearing: 真方位（度，0-360）
        mag_var: 磁偏角（度，东偏为正）；如果为 None 则直接返回真方位

    Returns:
        磁方位（度，0-360）

    计算公式：磁方位 = 真方位 - 磁偏角
    （东偏为正，所以东偏时磁北在真北东侧，磁方位数值小于真方位）
    """
    if mag_var is None:
        return true_bearing

    mag_bearing = true_bearing - mag_var

    # 归一化到 0-360 范围
    while mag_bearing < 0:
        mag_bearing += 360.0
    while mag_bearing >= 360:
        mag_bearing -= 360.0

    return mag_bearing


def is_geomag_available() -> bool:
    """检查 pygeomag 库是否可用。"""
    return _GEOMAG_AVAILABLE


def print_geomag_status():
    """打印磁变量计算模块状态（用于诊断）。"""
    if _GEOMAG_AVAILABLE:
        print("  [OK] pygeomag 库可用，将使用 WMM 计算精确磁偏角")
    else:
        print("  [--] pygeomag 库不可用，磁方位将等于真方位（不推荐）")
        print("       请运行: pip install pygeomag")

"""
2607 CSV FIR 区域码交叉参考模块。

从 2607 NAIP CSV 文件中读取真实的 FIR（飞行情报区）信息，
建立 ident → ICAO 前缀的映射表，用于修正 Fenix 数据中缺失的区域码。
"""

import csv
import os
from typing import Dict, Optional


# FIR 中文名称到 ICAO 前缀的映射表
FIR_TO_ICAO = {
    '北京情报区': 'ZB',
    '上海情报区': 'ZS',
    '广州情报区': 'ZG',
    '昆明情报区': 'ZP',
    '武汉情报区': 'ZH',
    '沈阳情报区': 'ZY',
    '兰州情报区': 'ZL',
    '乌鲁木齐情报区': 'ZW',
    '三亚情报区': 'ZJ',
    # 备用/边界交叠情况处理
    '上海情报区，广州情报区': 'ZS',
    '广州情报区，上海情报区': 'ZG',
    '武汉情报区，上海情报区': 'ZH',
    '沈阳情报区，上海情报区': 'ZY',
    '昆明情报区，广州情报区': 'ZP',
}


class RegionLookup:
    """区域码查找器，基于 2607 CSV FIR 字段交叉参考。"""

    def __init__(self, csv_dir: Optional[str] = None):
        """
        初始化区域码查找器。

        Args:
            csv_dir: 2607 CSV 数据目录路径，默认自动探测
        """
        self.navaid_map: Dict[str, str] = {}  # ident → icao_code
        self.waypoint_map: Dict[str, str] = {}
        self.airport_map: Dict[str, str] = {}
        self._loaded = False

        if csv_dir is None:
            csv_dir = self._auto_detect_csv_dir()

        self.csv_dir = csv_dir

        if csv_dir and os.path.isdir(csv_dir):
            self._load_from_csv(csv_dir)

    def _auto_detect_csv_dir(self) -> Optional[str]:
        """自动探测 2607 CSV 数据目录。"""
        # 尝试常见位置
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(script_dir, '2607'),
            r'F:\我的世界动画\AI项目\导航数据\2607',
            os.path.join(script_dir, '..', '2607'),
        ]

        for path in candidates:
            if os.path.isdir(path) and os.path.isfile(os.path.join(path, 'AD_HP.csv')):
                return path

        return None

    def _load_from_csv(self, csv_dir: str):
        """从 2607 CSV 文件加载区域码映射。"""
        try:
            # 加载机场数据（AD_HP.csv）
            airport_file = os.path.join(csv_dir, 'AD_HP.csv')
            if os.path.isfile(airport_file):
                with open(airport_file, encoding='gbk', errors='replace') as f:
                    reader = csv.reader(f)
                    header = next(reader)
                    # 列索引：1=CODE_FIR, 4=CODE_ID(ICAO)
                    for row in reader:
                        if len(row) > 4:
                            fir_name = row[1].strip()
                            icao = row[4].strip()
                            if icao and len(icao) == 4 and icao[:2] in ('ZB', 'ZG', 'ZH', 'ZJ', 'ZL', 'ZP', 'ZS', 'ZU', 'ZW', 'ZY'):
                                self.airport_map[icao] = icao[:2]  # 机场直接用 ICAO 前缀

            # 加载 VOR/DME/TACAN 数据（VOR.csv）
            vor_file = os.path.join(csv_dir, 'VOR.csv')
            if os.path.isfile(vor_file):
                with open(vor_file, encoding='gbk', errors='replace') as f:
                    reader = csv.reader(f)
                    header = next(reader)
                    # 列索引：1=CODE_FIR, 3=CODE_ID(ident)
                    for row in reader:
                        if len(row) > 3:
                            fir_name = row[1].strip()
                            ident = row[3].strip()
                            if ident and fir_name in FIR_TO_ICAO:
                                self.navaid_map[ident] = FIR_TO_ICAO[fir_name]

            # 加载 NDB 数据（NDB.csv）
            ndb_file = os.path.join(csv_dir, 'NDB.csv')
            if os.path.isfile(ndb_file):
                with open(ndb_file, encoding='gbk', errors='replace') as f:
                    reader = csv.reader(f)
                    header = next(reader)
                    # 列索引：1=CODE_FIR, 3=CODE_ID(ident)
                    for row in reader:
                        if len(row) > 3:
                            fir_name = row[1].strip()
                            ident = row[3].strip()
                            if ident and fir_name in FIR_TO_ICAO:
                                self.navaid_map[ident] = FIR_TO_ICAO[fir_name]

            # 加载航路点数据（DESIGNATED_POINT.csv）
            waypoint_file = os.path.join(csv_dir, 'DESIGNATED_POINT.csv')
            if os.path.isfile(waypoint_file):
                with open(waypoint_file, encoding='gbk', errors='replace') as f:
                    reader = csv.reader(f)
                    header = next(reader)
                    # 列索引：1=CODE_FIR, 3=CODE_ID(ident)
                    for row in reader:
                        if len(row) > 3:
                            fir_name = row[1].strip()
                            ident = row[3].strip()
                            if ident and fir_name in FIR_TO_ICAO:
                                self.waypoint_map[ident] = FIR_TO_ICAO[fir_name]

            self._loaded = True
            total = len(self.navaid_map) + len(self.waypoint_map) + len(self.airport_map)
            print(f"  已从 2607 CSV 加载 {total} 条区域码映射（导航台: {len(self.navaid_map)}, 航路点: {len(self.waypoint_map)}, 机场: {len(self.airport_map)}）")

        except Exception as e:
            print(f"  警告：加载 2607 CSV 区域码数据时出错: {e}")
            print(f"  将回退到基于最近机场的区域码推断")

    def get_navaid_icao(self, ident: str) -> Optional[str]:
        """获取导航台的 ICAO 区域码。"""
        return self.navaid_map.get(ident)

    def get_waypoint_icao(self, ident: str) -> Optional[str]:
        """获取航路点的 ICAO 区域码。"""
        return self.waypoint_map.get(ident)

    def get_airport_icao(self, ident: str) -> Optional[str]:
        """获取机场的 ICAO 区域码（通常直接从 ICAO 码推断）。"""
        if len(ident) == 4 and ident[:2] in ('ZB', 'ZG', 'ZH', 'ZJ', 'ZL', 'ZP', 'ZS', 'ZU', 'ZW', 'ZY'):
            return ident[:2]
        return self.airport_map.get(ident)

    def is_loaded(self) -> bool:
        """检查是否成功加载了区域码数据。"""
        return self._loaded

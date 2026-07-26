===============================================================================
  Fenix -> iniBuilds 导航数据转换工具
  中国区域数据补充 (NAIP)
  https://github.com/JCH2333/fenix_to_ini
===============================================================================

  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  !!  重要提示: 本工具必须使用含有 NAIP 中国数据的 Fenix 机模导航数据      !!
  !!                                                                         !!
  !!  普通 Navigraph 订阅的 Fenix 导航数据仅有约 92 个中国机场，不含完整    !!
  !!  的 NAIP 进离场程序。转换后不会有明显增加。                            !!
  !!                                                                         !!
  !!  只有包含 NAIP 数据的 Fenix nd.db3 (约 281 个中国机场，含完整 SID/     !!
  !!  STAR/IAP 程序) 才能正常使用本工具。                                   !!
  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!


一、简介
--------

本工具将 Fenix A320 的导航数据 (nd.db3, 含 NAIP 中国数据) 转换为 iniBuilds
DFDv2 格式 (db.s3db)，补充到 MSFS2020/2024 的 iniBuilds 机模中。

支持机型: iniBuilds A300, A320neo, A330, A340, A350

转换范围: 中国空域 (ZB, ZG, ZH, ZJ, ZL, ZP, ZS, ZU, ZW, ZY) +
          特殊机场 OPGT (吉尔吉特), VHHX (启德)

原理: 将 Fenix nd.db3 中有而 iniBuilds db.s3db 中没有的中国机场、
      跑道、导航台、航路、进离场程序数据补充到 iniBuilds 数据库中。

转换后的导航数据周期与源 Fenix 数据保持一致。


二、系统要求
-------------

- Windows 10/11
- Python 3.10 及以上 (需 tkinter, 通常随 Python 一起安装)
- 含 NAIP 数据的 Fenix A320 导航数据 (nd.db3)
- iniBuilds 机模已安装 (MSFS2020 或 MSFS2024)


三、快速开始 (GUI 模式)
-------------------------

1. 双击 gui.py 或在命令行运行:
   python gui.py

2. 点击 "自动检测路径" 按钮，工具会自动查找:
   - Fenix nd.db3 (优先脚本所在目录，其次 C:\ProgramData\Fenix\Navdata\)
   - iniBuilds db.s3db (MSFS2024 WASM 或 MSFS2020 packages 目录)

3. 确认弹窗中的路径是否正确，点击 "是" 自动填入

4. 选择转换选项:
   - 转换进离场程序: 包含 SID/STAR/IAP (推荐勾选)
   - 处理 RTE_SEG.csv: 合并中国 NAIP 航路 (如有 CSV 文件推荐勾选)
   - 自动备份原文件: 转换前备份原始 s3db (强烈建议勾选)

5. 点击 "开始转换"

6. 等待进度条完成，查看日志确认转换结果

7. 启动 MSFS 测试中国机场的进离场程序


四、命令行模式
---------------

python main.py [选项]

选项:
  --src PATH           Fenix nd.db3 路径
  --dst PATH           iniBuilds db.s3db 路径
  --csv PATH           RTE_SEG.csv 路径 (可选)
  --overwrite          覆盖目标文件 (自动备份)
  --auto-detect        自动检测路径
  --dry-run            仅分析不写入
  --skip-procedures    跳过程序转换 (快速模式)
  --skip-rte           跳过 RTE_SEG.csv
  --no-backup          不创建备份

示例:
  # 自动检测并转换
  python main.py --auto-detect --overwrite

  # 手动指定路径
  python main.py --src ../nd.db3 --dst ../db.s3db --csv ../RTE_SEG.csv --overwrite

  # 仅分析不修改
  python main.py --dry-run

  # 转换后验证
  python verify.py <path_to_db.s3db>


五、文件说明
-------------

main.py          - 命令行入口 / 核心转换引擎
gui.py           - 图形界面 (tkinter)
auto_detect.py   - 自动检测导航数据路径
freq.py          - 频率编解码
mappings.py      - 常量映射
db_utils.py      - SQLite 工具
merge.py         - 数据统计
rte_seg.py       - NAIP 航路 CSV 解析
verify.py        - 转换后验证
tables/          - 各阶段表转换模块


六、自动检测路径
-----------------

MSFS2024:
  %AppData%\Microsoft Flight Simulator 2024\WASM\MSFS2024\
    inibuilds-aircraft-*\work\NavigationData\db.s3db

MSFS2020:
  %AppData%\Microsoft Flight Simulator\packages\
    inibuilds-aircraft-*\work\NavigationData\db.s3db

Fenix (共用):
  C:\ProgramData\Fenix\Navdata\nd.db3
  (或脚本所在目录的父级目录中的 nd.db3)


七、转换阶段说明
-----------------

Phase 0: 元数据更新 (周期、日期、cycle.json)
Phase 1: 机场 (新增约 190 个中国机场)
Phase 2: 跑道
Phase 3: 导航台 (VHF VOR/DME, NDB)
Phase 4: 航路点 (约 3 万个中国航路点)
Phase 5: 航路 (约 8000 条中国航路段)
Phase 6: ILS 盲降
Phase 7: 进离场程序 (约 14 万条 SID/STAR/IAP)
Phase 8: 等待程序, GLS, 指点标, 网格高度, 通讯频率
Phase 9: 兼容表创建


八、注意事项
-------------

1. 转换前请确保 MSFS 未运行，否则 s3db 文件可能被锁定
2. 首次使用建议先运行 --dry-run 查看预期结果
3. 转换后可在 MSFS 中测试 ZBAA, ZSPD 等机场的 SID/STAR
4. 每次 AIRAC 更新后需重新运行转换
5. 如有问题请查看 GitHub Issues


九、参考项目
-------------

- iFly-NDB: https://github.com/Yuzuriha03/iFly-NDB
  (Fenix -> iFly 737 MAX 转换器，本项目参考了其频率解码和程序转换逻辑)

- Navigraph DFD Format: 本项目输出的 iniBuilds db.s3db 遵循 DFDv2 格式

===============================================================================
  License: GPL-3.0
  Author:  JCH2333
  GitHub:  https://github.com/JCH2333/fenix_to_ini
===============================================================================

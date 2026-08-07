===============================================================================
  Fenix -> iniBuilds 导航数据转换工具
  中国区域数据补充 (NAIP)
  版本: v1.7.0
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

当前 GUI 使用两步工作流：
1. 选择 Fenix nd.db3 与任意一份官方 DFDv2 数据库作为转换模板，点击“生成本地暂存”。转换结果固定保存在 output/staged/db.s3db，使用 iniBuilds 标准文件名，不会立即覆盖游戏文件。
2. 勾选已检测到的 iniBuilds A340、iniBuilds A350、Aerosoft AS346 或 C919，确认游戏已完全退出后，点击“部署到所选机模”。同一份暂存结果可反复部署到多个机模，无需重复转换。

部署前工具会创建带时间戳的数据库、cycle.json 与 layout.json 备份。iniBuilds 和 C919 使用标准 DFDv2 暂存库；AS346 会仅在部署副本上执行运行时兼容性处理。请在实机验证通过前将结果视为测试版。

本工具将 Fenix A320 的导航数据 (nd.db3, 含 NAIP 中国数据) 转换为 iniBuilds
DFDv2 格式 (db.s3db)，补充到 MSFS2020/2024 的 iniBuilds 机模中。

支持机型: iniBuilds A300, A320neo, A330, A340, A350，以及 Aerosoft AS346

转换范围: 中国空域 (ZB, ZG, ZH, ZJ, ZL, ZP, ZS, ZU, ZW, ZY) +
          特殊机场 OPGT (吉尔吉特), VHHX (启德)

原理: 将 Fenix nd.db3 中有而 iniBuilds db.s3db 中没有的中国机场、
      跑道、导航台、航路、进离场程序数据补充到 iniBuilds 数据库中。

转换后的导航数据周期与源 Fenix 数据保持一致。

自动更新:
- 程序启动后会在后台检查 GitHub 新版本，不影响路径检测和导航数据转换。
- 也可以点击“检查更新”立即检查；发现新版本后可一键下载、校验和安装。
- GitHub 无法访问时会自动尝试国内镜像，无需手动下载安装包。
- 安装前自动备份当前程序，失败时自动恢复，完成后自动重启并显示结果。

v1.7.0 更新简介:
- GUI 改为“先生成本地暂存、再多选部署”的两步流程。Fenix 数据只转换一次，暂存结果可重复覆盖 iniBuilds A340、A350、Aerosoft AS346 和 C919。
- 部署前检查 Microsoft Flight Simulator 2024 是否已完全退出；每个目标会备份数据库、cycle.json 和 layout.json，部署失败时自动恢复。
- iniBuilds 与 C919 直接部署标准 DFDv2 暂存库；AS346 只在私有副本中执行兼容性清洗，避免影响其他机模的数据。
- 自动检测新增 C919 的 Community/WASM 数据位置，并保留 Microsoft Store/Xbox 路径支持。

v1.6.0 更新简介:
- 新增启动后台检查和“检查更新”按钮。
- 新版本可一键下载、SHA-256 校验、安装并自动重启，无需手动下载安装包。
- GitHub 访问失败时自动依次尝试国内镜像。
- 安装前自动备份程序文件，安装异常时自动回滚，不触碰用户数据库和转换输出。

v1.5.0 更新简介:
- 以可正常使用的 iniBuilds 2604 中国 NAIP 数据为基准，重建 A340 程序字段语义。
- 修复 SID/STAR/IAP 航点坐标、区域码和 EA/PC 引用不一致问题。
- 补全 ILS 最终进近、FAF、MAP、复飞尾段以及 PI 航向台引用。
- 恢复 RNP AR 授权、RNP 0.3、RF 弧心/半径、下降角和跑道 MAP 航点。
- 增加 21 个跨区域机场的 ARINC ICAO 区域码映射，并排除 ZK/ZM 数据误判。
- 修复 RNP AR 跑道航点重复转换时坐标漂移，转换结果实现逻辑幂等。
- Header 元数据改为确定性生成，同一输入重复转换不再产生时间字段差异。
- iniBuilds A340 实机验证通过：机场输入、进离场、完整进近及 RNP AR 均可正常使用。

v1.4.0 更新简介:
- 完成 Aerosoft AS346/ToLiss DFDv2 支持，新增目标格式识别、专用清洗和验证。
- 修复 AS346 跑道物理顺序导致新增 NAIP 机场无法检索的问题。
- 修复程序固定字段、NULL 联动及 MAP 高度描述错误导致的 WASM 0xc0000005 崩溃。
- 补全 RF 航段半径、弧长、转向和公共程序段起点，去除重复零长度航段。
- 修复 RTE_SEG 定长 DMS 纬度解析，以及缺失中间航点造成的假长航段。
- 航路按航路名、航点和坐标跨数据源去重，保留有效记录且最终清洗不再删除数据。
- AS346 实机验证通过：ZUNZ/ZUUU 可设置航线并选择 SID、STAR 和进近，WASM 不再崩溃。
- 自动检测新增 Microsoft Store/Xbox 版 MSFS 2024 路径支持。

v1.3.0 更新简介:
- 修复仅覆盖 WASM 工作副本、启动后被 iniBuilds 原版数据自动还原的问题。
- 自动检测优先选择 Community 机模包中的 Navigraph/BundledData 数据库。
- 转换 Community 数据库后自动同步 layout.json 的文件大小和时间戳。
- 按 DFDv2 规范将 2607n2 写为周期 2607、修订号 2，并保持 SQLite DELETE 日志模式。
- 新增 Aerosoft AS346 实验性转换入口，自动选择 WASM/FMSData 中最新下载周期。

v1.2.1 更新简介:
- 兼容 MSFS 2024 iniBuilds A340 不含 ctl 可选列的 IAP 表结构。
- 验证器兼容不同机模版本的基础数据库规模，仍严格检查空表与源程序完整性。

v1.2.0 更新简介:
- 修复 SID/STAR/IAP 的程序类型与路径终止码字段颠倒问题。
- 按 Fenix 源库完整重建中国程序，保留进近过渡、最终进近和复飞段。
- 使用坐标匹配更新导航台和航路点，不再删除合法同名记录或创建唯一索引。
- 修复 --output 未生效、--dry-run 会修改目标库、进度总数错误等问题。
- 验证器支持与 Fenix 源库逐段对照，并在失败时返回错误状态。


二、系统要求
-------------

- Windows 10/11
- Python 3.10 及以上 (需 tkinter, 通常随 Python 一起安装)
- pygeomag 库 (用于计算真实磁偏角/磁方位，安装: pip install -r requirements.txt
  或 pip install pygeomag；未安装时会自动回退为"磁方位≈真方位"的旧行为并打印警告)
- 含 NAIP 数据的 Fenix A320 导航数据 (nd.db3)
- iniBuilds 机模已安装 (MSFS2020 或 MSFS2024)
- (可选) 本地 2607 NAIP CSV 数据目录，用于交叉参考更准确的机场/导航台/
  航路点区域码 (icao_code)。默认自动探测脚本同级或上级目录中的 2607 文件夹


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
  --output PATH        以 --dst 为模板生成新的输出文件
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

  # 保留模板库并生成新的输出文件
  python main.py --src ../nd.db3 --dst ../db.s3db --output ./output/db.s3db

  # 仅分析不修改
  python main.py --dry-run

  # 转换后验证
  python verify.py <path_to_db.s3db> --source <path_to_nd.db3>


五、文件说明
-------------

main.py          - 命令行入口 / 核心转换引擎
staging.py       - 本地暂存库的固定路径和一次性转换入口
deployment.py    - 多机模部署、备份、回滚、周期元数据和布局文件更新
gui.py           - 图形界面 (tkinter)
auto_detect.py   - 自动检测导航数据路径 + NAIP 完整性校验
freq.py          - 频率编解码 + 合法频段校验
mappings.py      - 常量映射
db_utils.py      - SQLite 工具 (非破坏性更新与坐标匹配)
merge.py         - 数据统计
region_lookup.py - 2607 CSV FIR 区域码交叉参考
geomag.py        - WMM 磁偏角计算 (基于 pygeomag)
rte_seg.py       - NAIP 航路 CSV 解析
verify.py        - 转换后验证
version.py       - 程序版本号
update_manager.py - 自动检查、下载、校验、备份、安装和重启
build_update_package.py - 构建 GitHub Release 一键更新包
tables/          - 各阶段表转换模块


六、自动检测路径
-----------------

MSFS2024:
  %AppData%\Microsoft Flight Simulator 2024\WASM\MSFS2024\
    inibuilds-aircraft-*\work\NavigationData\db.s3db
  %AppData%\Microsoft Flight Simulator 2024\WASM\MSFS2024\
    aerosoft-aircraft-a346-pro\work\FMSData\cycle_*\ng_jeppesen_fwdfd_*.s3db

MSFS2024 Microsoft Store/Xbox:
  %LocalAppData%\Packages\Microsoft.Limitless_*\LocalCache\Packages\Community\
    inibuilds-aircraft-*\Navigraph\BundledData\*.s3db
  同时检测 LocalCache、LocalCache\Packages 和 LocalState 下的 UserCfg.opt、
  Microsoft Flight Simulator 2024\WASM\MSFS2024 及自定义 InstalledPackagesPath。

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
5. 本工具会自动检测已装 Fenix nd.db3 是否为含完整 NAIP 数据的版本
   (通过统计含进离场程序的中国机场数量)，如检测到普通 Navigraph 版本
   会给出中文警告提示
6. 机场/跑道/导航台/航路点/ILS 等主表现已支持覆盖更新 (UPSERT)：
   重复运行转换时，已存在的记录会用最新 Fenix 数据刷新，而不是仅新增
7. 未安装 pygeomag 时磁方位会退化为"约等于真方位"的旧行为，建议安装
   pygeomag 以获得准确的磁方位数据
8. 如有问题请查看 GitHub Issues


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

#!/usr/bin/env python3
"""
GUI for Fenix -> iniBuilds Navigation Data Converter.

Uses tkinter (stdlib) for file selection, progress display, and result reporting.
"""

import sys
import os
import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_detect import detect_all
from deployment import DEPLOYMENT_PROFILES, deploy_staged_database
from main import set_log_callback
from staging import create_staged_navigation_data, staging_database_path
from update_manager import (
    UpdateError,
    check_for_update,
    download_update,
    launch_update_installer,
)
from version import __version__


class RedirectText:
    """Redirect print/stdout to a tkinter Text widget."""

    def __init__(self, text_widget: tk.Text):
        self.text_widget = text_widget
        self._original_stdout = sys.stdout

    def write(self, msg: str):
        self.text_widget.insert(tk.END, msg)
        self.text_widget.see(tk.END)
        self.text_widget.update_idletasks()

    def flush(self):
        pass

    def __enter__(self):
        sys.stdout = self
        return self

    def __exit__(self, *args):
        sys.stdout = self._original_stdout


class ConversionGUI:
    """Main GUI application window."""

    def __init__(self, update_result: dict | None = None):
        self.root = tk.Tk()
        self.root.title(f"Fenix -> DFDv2 导航数据转换工具 v{__version__}")
        self.root.geometry("900x700")
        self.root.minsize(780, 600)

        self.conversion_thread: threading.Thread | None = None
        self.deployment_thread: threading.Thread | None = None
        self.running = False
        self.update_busy = False
        self.redirect: RedirectText | None = None
        self.detected_targets: dict[str, list[str]] = {}
        self.staged_database: Path | None = None

        self._build_ui()

        # Keep startup dialogs sequential: update result, path detection, then update check.
        self.root.after(300, lambda: self._run_startup_tasks(update_result))

    def _build_ui(self):
        """Build the GUI layout."""
        # Title
        title_frame = ttk.Frame(self.root, padding=10)
        title_frame.pack(fill=tk.X)
        ttk.Label(title_frame, text="Fenix -> DFDv2 导航数据转换工具",
                  font=('Microsoft YaHei', 14, 'bold')).pack()
        ttk.Label(title_frame, text="AIRAC 2607 | 中国区域数据补充 | MSFS2020/2024 支持",
                  font=('Microsoft YaHei', 9)).pack()

        # Separator
        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10)

        # File paths frame
        paths_frame = ttk.LabelFrame(self.root, text=" 文件路径 ", padding=10)
        paths_frame.pack(fill=tk.X, padx=10, pady=5)

        # Row 0: Auto-detect button
        btn_frame = ttk.Frame(paths_frame)
        btn_frame.grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 5))
        self.detect_btn = ttk.Button(btn_frame, text="自动检测路径", command=self.auto_detect)
        self.detect_btn.pack(side=tk.LEFT, padx=5)
        self.detect_label = ttk.Label(btn_frame, text="", foreground="gray")
        self.detect_label.pack(side=tk.LEFT, padx=10)

        # Row 1: Fenix nd.db3
        ttk.Label(paths_frame, text="Fenix nd.db3:", width=18, anchor=tk.E).grid(row=1, column=0, sticky=tk.W, pady=2)
        self.src_var = tk.StringVar()
        self.src_entry = ttk.Entry(paths_frame, textvariable=self.src_var, width=70)
        self.src_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Button(paths_frame, text="浏览...", command=self.browse_src).grid(row=1, column=2, pady=2)

        # Row 2: generic DFDv2 template used only to build the local staging copy
        ttk.Label(paths_frame, text="暂存转换模板:", width=18, anchor=tk.E).grid(row=2, column=0, sticky=tk.W, pady=2)
        self.template_var = tk.StringVar()
        self.template_entry = ttk.Entry(paths_frame, textvariable=self.template_var, width=70)
        self.template_entry.grid(row=2, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Button(paths_frame, text="浏览...", command=self.browse_template).grid(row=2, column=2, pady=2)

        # Row 3: RTE_SEG.csv
        ttk.Label(paths_frame, text="RTE_SEG.csv (可选):", width=18, anchor=tk.E).grid(row=3, column=0, sticky=tk.W, pady=2)
        self.csv_var = tk.StringVar()
        self.csv_entry = ttk.Entry(paths_frame, textvariable=self.csv_var, width=70)
        self.csv_entry.grid(row=3, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Button(paths_frame, text="浏览...", command=self.browse_csv).grid(row=3, column=2, pady=2)

        paths_frame.columnconfigure(1, weight=1)

        # Options frame
        opt_frame = ttk.LabelFrame(self.root, text=" 转换选项 ", padding=10)
        opt_frame.pack(fill=tk.X, padx=10, pady=5)

        self.procedures_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="转换进离场程序 (SID/STAR/IAP)", variable=self.procedures_var).pack(side=tk.LEFT, padx=10)

        self.rte_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="处理 RTE_SEG.csv", variable=self.rte_var).pack(side=tk.LEFT, padx=10)

        ttk.Label(opt_frame, text="部署时自动备份导航库、周期文件和布局文件", foreground="gray").pack(side=tk.LEFT, padx=10)

        deploy_frame = ttk.LabelFrame(self.root, text=" 部署目标（生成本地暂存后可多选） ", padding=10)
        deploy_frame.pack(fill=tk.X, padx=10, pady=5)
        self.target_vars: dict[str, tk.BooleanVar] = {}
        self.target_checks: dict[str, ttk.Checkbutton] = {}
        self.target_labels: dict[str, ttk.Label] = {}
        for column, (key, profile) in enumerate(DEPLOYMENT_PROFILES.items()):
            variable = tk.BooleanVar(value=False)
            self.target_vars[key] = variable
            check = ttk.Checkbutton(
                deploy_frame, text=profile.label, variable=variable,
                command=self._refresh_deploy_button,
                state=tk.DISABLED,
            )
            check.grid(row=0, column=column * 2, sticky=tk.W, padx=(8, 2))
            self.target_checks[key] = check
            label = ttk.Label(deploy_frame, text="未检测到", foreground="gray", width=13)
            label.grid(row=0, column=column * 2 + 1, sticky=tk.W, padx=(0, 8))
            self.target_labels[key] = label

        # Progress bar
        progress_frame = ttk.Frame(self.root, padding=(10, 5))
        progress_frame.pack(fill=tk.X)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=(0, 10))
        self.progress_label = ttk.Label(progress_frame, text="就绪", width=40, anchor=tk.W)
        self.progress_label.pack(side=tk.RIGHT)

        # Log output
        log_frame = ttk.LabelFrame(self.root, text=" 运行日志 ", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, font=('Consolas', 9),
                                bg='#1e1e1e', fg='#d4d4d4',
                                state=tk.DISABLED)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Buttons
        btn_frame = ttk.Frame(self.root, padding=10)
        btn_frame.pack(fill=tk.X)

        self.start_btn = ttk.Button(btn_frame, text="生成本地暂存", command=self.start_conversion)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.deploy_btn = ttk.Button(btn_frame, text="部署到所选机模", command=self.deploy_selected, state=tk.DISABLED)
        self.deploy_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self.stop_conversion, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self.verify_btn = ttk.Button(btn_frame, text="验证数据库", command=self.verify_database)
        self.verify_btn.pack(side=tk.LEFT, padx=5)

        self.update_btn = ttk.Button(btn_frame, text="检查更新", command=self.check_updates)
        self.update_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(btn_frame, text="退出", command=self.root.quit).pack(side=tk.RIGHT, padx=5)

        # Status bar
        self.status_var = tk.StringVar(value='就绪 - 先生成本地暂存，再部署到所选机模')
        status_bar = ttk.Label(self.root, textvariable=self.status_var,
                               relief=tk.SUNKEN, anchor=tk.W, padding=5)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _run_startup_tasks(self, update_result: dict | None):
        if update_result:
            self._show_update_result(update_result)
        self.auto_detect()
        self.root.after(800, lambda: self.check_updates(manual=False))

    # ---- File Browsers ----
    def browse_src(self):
        path = filedialog.askopenfilename(
            title="选择 Fenix nd.db3",
            filetypes=[("SQLite Database", "*.db3"), ("All Files", "*.*")]
        )
        if path:
            self.src_var.set(path)

    def browse_template(self):
        path = filedialog.askopenfilename(
            title="选择标准 DFDv2 转换模板",
            filetypes=[("SQLite Database", "*.s3db"), ("All Files", "*.*")]
        )
        if path:
            self.template_var.set(path)

    def browse_csv(self):
        path = filedialog.askopenfilename(
            title="选择 RTE_SEG.csv (可选)",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        if path:
            self.csv_var.set(path)

    # ---- Auto Detection ----
    def auto_detect(self):
        """Auto-detect navigation data paths and prompt user."""
        self.log("检测导航数据路径...\n")

        try:
            results = detect_all()
        except Exception as e:
            self.log(f"检测失败: {e}\n")
            return

        fenix = results.get('fenix_db')
        csv_path = results.get('fenix_csv')
        deployment_targets = results.get('deployment_targets', {})
        naip = results.get('naip_completeness')

        # Build detection summary
        lines = []
        lines.append("检测结果:")
        if fenix:
            lines.append(f"  [OK] Fenix nd.db3: {fenix}")
            if naip:
                if naip.get('error'):
                    lines.append(f"  [!!] 无法校验 NAIP 完整性: {naip['error']}")
                elif naip['is_complete']:
                    lines.append(f"  [OK] NAIP 完整性校验通过: {naip['cn_airports_with_procs']} 个中国机场含进离场程序")
                else:
                    lines.append(f"  [警告] 检测到的 Fenix 数据可能不含完整 NAIP 中国程序数据！")
                    lines.append(f"         仅 {naip['cn_airports_with_procs']} 个中国机场含进离场程序，转换后中国机场程序会很少")
        else:
            lines.append(f"  [--] Fenix nd.db3: 未找到")

        if csv_path:
            lines.append(f"  [OK] RTE_SEG.csv: {csv_path}")
        else:
            lines.append(f"  [--] RTE_SEG.csv: 未找到")

        self.detected_targets = {
            key: list(paths) for key, paths in deployment_targets.items()
        }
        template_path = None
        for key in ("ini_a340", "ini_a350", "c919", "as346"):
            paths = self.detected_targets.get(key, [])
            profile = DEPLOYMENT_PROFILES[key]
            if paths:
                self.target_checks[key].config(state=tk.NORMAL)
                self.target_labels[key].config(
                    text=f"已检测 {len(paths)} 处", foreground="green"
                )
                lines.append(f"  [OK] {profile.label}: {len(paths)} 个加载位置")
                if template_path is None:
                    template_path = paths[0]
            else:
                self.target_vars[key].set(False)
                self.target_checks[key].config(state=tk.DISABLED)
                self.target_labels[key].config(text="未检测到", foreground="gray")
                lines.append(f"  [--] {profile.label}: 未检测到")

        self._refresh_deploy_button()

        msg = '\n'.join(lines)
        summary = f"Fenix: {fenix or '未找到'}\nCSV: {csv_path or '未找到'}\n转换模板: {template_path or '未找到'}"
        if naip and not naip.get('error') and not naip['is_complete']:
            summary += (f"\n\n警告：检测到的 Fenix 数据仅 {naip['cn_airports_with_procs']} 个中国机场含进离场程序，"
                        f"可能不是含 NAIP 数据的完整版，转换后中国机场程序会很少。")

        self.log(msg + '\n')

        # Ask user
        if fenix:
            answer = messagebox.askyesno(
                "自动检测完成",
                f"{summary}\n\n是否自动填入以上路径？"
            )
            if answer:
                self.src_var.set(fenix)
                if template_path:
                    self.template_var.set(template_path)
                if csv_path:
                    self.csv_var.set(csv_path)
                self.detect_label.config(text="已填入转换来源和模板", foreground="green")
                self.status_var.set("路径已自动填入，可生成本地暂存")
            else:
                self.detect_label.config(text="检测完成，请手动输入", foreground="orange")
                self.status_var.set("请手动输入或浏览文件路径")
        else:
            self.detect_label.config(text="未检测到路径", foreground="red")
            self.status_var.set("未检测到导航数据，请手动输入路径")

    # ---- Conversion ----
    def start_conversion(self):
        """Start the conversion in a background thread."""
        src = self.src_var.get().strip()
        template = self.template_var.get().strip()
        csv_path = self.csv_var.get().strip() or None

        # Validate
        if not src:
            messagebox.showerror("错误", "请选择 Fenix nd.db3 文件路径")
            return
        if not template:
            messagebox.showerror("错误", "请选择用于生成暂存数据的标准 DFDv2 模板")
            return
        if not os.path.exists(src):
            messagebox.showerror("错误", f"Fenix nd.db3 不存在:\n{src}")
            return
        if not os.path.exists(template):
            messagebox.showerror("错误", f"转换模板数据库不存在:\n{template}")
            return

        skip_proc = not self.procedures_var.get()
        skip_rte = not self.rte_var.get()
        if not messagebox.askyesno(
            "确认生成暂存",
            f"源文件: {src}\n转换模板: {template}\n\n"
            "将生成本地暂存导航数据，不会覆盖任何机模文件。\n"
            f"{'转换进离场程序' if not skip_proc else '跳过进离场程序'}\n"
            f"{'处理RTE_SEG.csv' if not skip_rte else '跳过RTE_SEG.csv'}\n\n"
            "确认开始生成？"
        ):
            return

        self._set_running(True)
        self.progress_var.set(0)
        self.progress_label.config(text="准备中...")
        self.clear_log()

        # Set up log redirection
        self.redirect = RedirectText(self.log_text)
        self.redirect.__enter__()

        # Set log callback for the conversion pipeline
        set_log_callback(self._log_to_gui)

        # Run conversion in background thread
        self.conversion_thread = threading.Thread(
            target=self._run_conversion_thread,
            args=(src, template, csv_path, skip_proc, skip_rte),
            daemon=True
        )
        self.conversion_thread.start()

        # Poll for completion
        self.root.after(200, self._check_thread)

    def _run_conversion_thread(self, src, template, csv_path, skip_proc, skip_rte):
        """Background thread for conversion."""
        try:
            result = create_staged_navigation_data(
                src_path=src,
                template_path=template,
                csv_path=csv_path,
                skip_procedures=skip_proc,
                skip_rte=skip_rte,
                progress_callback=self._on_progress,
            )
            self._conversion_result = ('ok', result)
        except Exception as e:
            import traceback
            self._conversion_result = ('error', str(e) + '\n' + traceback.format_exc())

    def _check_thread(self):
        """Check if the conversion thread has finished."""
        if self.conversion_thread and self.conversion_thread.is_alive():
            self.root.after(200, self._check_thread)
        else:
            self._on_conversion_done()

    def _on_conversion_done(self):
        """Handle conversion completion."""
        self._set_running(False)

        # Restore stdout
        if self.redirect:
            self.redirect.__exit__()
            self.redirect = None

        # Restore log callback
        set_log_callback(print)

        result = getattr(self, '_conversion_result', ('error', 'Unknown'))
        if result[0] == 'ok':
            self.staged_database = Path(result[1]).resolve()
            self.progress_var.set(100)
            self.progress_label.config(text="转换完成!")
            self.status_var.set(f"转换成功! 输出: {result[1]}")
            self._refresh_deploy_button()
            messagebox.showinfo(
                "完成",
                "导航数据已生成到本地暂存区。\n\n"
                f"暂存文件: {self.staged_database}\n\n"
                "请勾选需要覆盖的机模，再点击“部署到所选机模”。",
            )
        else:
            self.progress_label.config(text="转换失败")
            self.status_var.set("转换失败，请查看日志")
            messagebox.showerror("错误", f"转换过程中出现错误:\n\n{result[1][:500]}")

    def stop_conversion(self):
        """Stop a running conversion (best effort)."""
        self.running = False
        self.status_var.set("正在停止...")
        self._set_running(False)

    # ---- Deployment ----
    def deploy_selected(self):
        """Deploy the existing staging output to all selected aircraft."""
        staged = self.staged_database or staging_database_path()
        selected = [key for key, variable in self.target_vars.items() if variable.get()]
        if not staged.is_file():
            messagebox.showerror("错误", "请先生成本地暂存导航数据。")
            return
        if not selected:
            messagebox.showerror("错误", "请至少选择一个已检测到的机模。")
            return

        details = []
        for key in selected:
            paths = self.detected_targets.get(key, [])
            if not paths:
                messagebox.showerror("错误", f"{DEPLOYMENT_PROFILES[key].label} 没有可用的导航数据路径。")
                return
            details.append(f"- {DEPLOYMENT_PROFILES[key].label}: {len(paths)} 个位置")

        if not messagebox.askyesno(
            "确认覆盖导航数据",
            "将覆盖以下机模的导航数据：\n\n" + "\n".join(details)
            + "\n\n请确认 Microsoft Flight Simulator 2024 已完全关闭。"
              "\n部署前会自动备份数据库、周期文件和布局文件。"
              "\nAS346 会额外应用已验证的兼容性适配。\n\n是否继续？",
        ):
            return

        self._set_running(True)
        self.progress_var.set(0)
        self.progress_label.config(text="准备部署...")
        self.status_var.set("正在部署导航数据...")
        self._deployment_result = None
        self.deployment_thread = threading.Thread(
            target=self._run_deployment_thread, args=(staged, selected), daemon=True
        )
        self.deployment_thread.start()
        self.root.after(200, self._check_deployment_thread)

    def _run_deployment_thread(self, staged: Path, selected: list[str]):
        try:
            results = []
            total = len(selected)
            for index, key in enumerate(selected, start=1):
                profile = DEPLOYMENT_PROFILES[key]
                self.root.after(0, lambda index=index, total=total, profile=profile:
                                self._set_deployment_progress(index - 1, total, f"正在部署到 {profile.label}..."))
                results.append(deploy_staged_database(staged, key, self.detected_targets[key]))
                self.root.after(0, lambda index=index, total=total, profile=profile:
                                self._set_deployment_progress(index, total, f"{profile.label} 部署完成"))
            self._deployment_result = ("ok", results)
        except Exception as exc:
            import traceback
            self._deployment_result = ("error", str(exc) + "\n" + traceback.format_exc())

    def _set_deployment_progress(self, current: int, total: int, label: str):
        self.progress_var.set(current * 100 / max(total, 1))
        self.progress_label.config(text=label)
        self.status_var.set(label)

    def _check_deployment_thread(self):
        if self.deployment_thread and self.deployment_thread.is_alive():
            self.root.after(200, self._check_deployment_thread)
            return
        self._on_deployment_done()

    def _on_deployment_done(self):
        self._set_running(False)
        result = self._deployment_result or ("error", "部署线程未返回结果")
        if result[0] == "ok":
            self.progress_var.set(100)
            self.progress_label.config(text="部署完成")
            self.status_var.set("已完成所选机模的导航数据部署")
            summary = [
                f"{item.profile.label}: {len(item.database_paths)} 个位置\n备份: {item.backup_directory}"
                for item in result[1]
            ]
            messagebox.showinfo("部署完成", "\n\n".join(summary))
        else:
            self.progress_label.config(text="部署失败，已尝试恢复备份")
            self.status_var.set("部署失败，请查看日志和备份目录")
            self.log(result[1])
            messagebox.showerror("部署失败", result[1][:700])
        self._refresh_deploy_button()

    # ---- Updates ----
    def check_updates(self, manual: bool = True):
        """Check for a release without blocking the GUI."""
        if self.running or self.update_busy:
            if manual and self.running:
                messagebox.showinfo("检查更新", "请等待当前转换或验证完成后再检查更新。")
            return

        self.update_busy = True
        self.update_btn.config(state=tk.DISABLED)
        if manual:
            self.status_var.set("正在检查更新...")
            self.progress_label.config(text="正在检查更新...")

        def worker():
            result = check_for_update()
            self.root.after(0, lambda: self._on_update_checked(result, manual))

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_checked(self, result, manual: bool):
        self.update_busy = False
        self.update_btn.config(state=tk.NORMAL if not self.running else tk.DISABLED)

        if result.error:
            self.log(f"更新检查失败: {result.error}")
            if manual:
                self.status_var.set("检查更新失败")
                self.progress_label.config(text="检查更新失败")
                messagebox.showerror(
                    "检查更新",
                    "GitHub 和国内镜像均不可用，请稍后重试。",
                )
            return

        if not result.update_available:
            if manual:
                self.status_var.set(f"当前已是最新版 v{__version__}")
                self.progress_label.config(text="当前已是最新版")
                messagebox.showinfo("检查更新", f"当前已是最新版 v{__version__}。")
            return

        release = result.release
        if not release:
            return
        if self.running:
            self.log(f"发现新版本 v{release.version}，请在当前操作完成后点击检查更新。")
            return

        confirmed = messagebox.askyesno(
            "发现新版本",
            f"发现新版本 v{release.version}（当前 v{__version__}）。\n\n"
            "是否立即下载安装？\n\n"
            "程序会自动备份当前版本，安装完成后自动重启；安装失败会自动恢复。",
        )
        if confirmed:
            self._download_and_install_update(release)

    def _download_and_install_update(self, release):
        self.update_busy = True
        self._set_update_controls(False)
        self.progress_var.set(0)
        self.progress_label.config(text=f"正在下载 v{release.version}...")
        self.status_var.set("正在下载并校验更新包...")

        def progress(received: int, total: int):
            percent = min(100.0, received * 100.0 / max(total, 1))
            self.root.after(0, lambda: self.progress_var.set(percent))
            self.root.after(
                0,
                lambda: self.progress_label.config(
                    text=f"下载更新 {received / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB"
                ),
            )

        def worker():
            try:
                package_path = download_update(release, progress_callback=progress)
                self.root.after(
                    0,
                    lambda: self._start_update_installer(package_path, release),
                )
            except (UpdateError, OSError) as exc:
                error = str(exc)
                self.root.after(0, lambda: self._on_update_download_failed(error))

        threading.Thread(target=worker, daemon=True).start()

    def _start_update_installer(self, package_path: Path, release):
        try:
            self.progress_var.set(100)
            self.progress_label.config(text="下载校验完成，正在安装...")
            self.status_var.set("程序即将关闭，更新完成后会自动重启...")
            launch_update_installer(
                package_path,
                Path(__file__).resolve().parent,
                release,
                os.getpid(),
            )
        except (OSError, UpdateError) as exc:
            package_path.unlink(missing_ok=True)
            self._on_update_download_failed(str(exc))
            return
        self.root.after(100, self.root.destroy)

    def _on_update_download_failed(self, error: str):
        self.update_busy = False
        self._set_update_controls(True)
        self.progress_label.config(text="自动更新失败")
        self.status_var.set("自动更新失败")
        self.log(f"自动更新失败: {error}")
        messagebox.showerror(
            "自动更新失败",
            f"更新包下载或校验失败，当前程序未被修改。\n\n{error}",
        )

    def _set_update_controls(self, enabled: bool):
        state = tk.NORMAL if enabled and not self.running else tk.DISABLED
        self.start_btn.config(state=state)
        self.detect_btn.config(state=state)
        self.verify_btn.config(state=state)
        self.update_btn.config(state=state)
        for key, check in self.target_checks.items():
            check.config(state=state if self.detected_targets.get(key) else tk.DISABLED)
        self._refresh_deploy_button()

    def _show_update_result(self, result: dict):
        success = bool(result.get("success"))
        message = str(result.get("message") or "更新结果未知")
        if success:
            self.status_var.set(f"已成功更新到 v{result.get('version', __version__)}")
            self.progress_label.config(text="自动更新完成")
            messagebox.showinfo("自动更新完成", message)
        else:
            self.status_var.set("自动更新失败，已保留原版本")
            self.progress_label.config(text="自动更新失败")
            messagebox.showerror("自动更新失败", message)

    # ---- Verification ----
    def verify_database(self):
        """Run verification on the selected database."""
        staged = self.staged_database or staging_database_path()
        path = str(staged) if staged.is_file() else self.template_var.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror("错误", "请先选择或自动检测目标 DFDv2 数据库")
            return

        self.clear_log()
        self._set_running(True)
        self.status_var.set("正在验证...")

        from verify import verify_all

        self.redirect = RedirectText(self.log_text)
        self.redirect.__enter__()

        def run_verify():
            try:
                ok = verify_all(path)
                self._verify_result = ok
            except Exception as e:
                self._verify_result = str(e)

        threading.Thread(target=run_verify, daemon=True).start()
        self.root.after(500, lambda: self._check_verify(path))

    def _check_verify(self, path):
        if getattr(self, '_verify_result', None) is None and threading.active_count() > 2:
            self.root.after(500, lambda: self._check_verify(path))
        else:
            if self.redirect:
                self.redirect.__exit__()
                self.redirect = None
            self._set_running(False)
            result = getattr(self, '_verify_result', None)
            if result is True:
                self.status_var.set("验证通过")
            elif result is False:
                self.status_var.set("验证发现问题，请查看日志")
            else:
                self.status_var.set(f"验证出错: {result}")
            self._verify_result = None

    # ---- Helpers ----
    def _set_running(self, running: bool):
        self.running = running
        state_start = tk.DISABLED if running else tk.NORMAL
        state_stop = tk.NORMAL if running else tk.DISABLED
        self.start_btn.config(state=state_start)
        self.stop_btn.config(state=state_stop)
        self.detect_btn.config(state=state_start)
        self.verify_btn.config(state=state_start)
        self.update_btn.config(state=state_start)
        for key, check in self.target_checks.items():
            check.config(
                state=tk.DISABLED if running or not self.detected_targets.get(key) else tk.NORMAL
            )
        self._refresh_deploy_button()

    def _refresh_deploy_button(self):
        staged = self.staged_database or staging_database_path()
        has_target = any(
            self.target_vars[key].get() and self.detected_targets.get(key)
            for key in self.target_vars
        )
        state = tk.NORMAL if not self.running and staged.is_file() and has_target else tk.DISABLED
        self.deploy_btn.config(state=state)

    def _on_progress(self, phase: int, total: int, label: str):
        """Called from conversion pipeline for progress updates."""
        pct = (phase / total) * 100
        self.root.after(0, lambda: self.progress_var.set(pct))
        self.root.after(0, lambda: self.progress_label.config(text=f"[{phase}/{total}] {label}"))
        self.root.after(0, lambda: self.status_var.set(f"转换中... {label}"))

    def _log_to_gui(self, msg: str):
        """Log callback for the conversion pipeline."""
        self.root.after(0, lambda: self._append_log(msg))

    def _append_log(self, msg: str):
        """Append text to log widget (thread-safe via after())."""
        self.log_text.config(state=tk.NORMAL)
        if msg:
            self.log_text.insert(tk.END, msg)
        self.log_text.insert(tk.END, '\n')
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def log(self, msg: str):
        """Log a message directly (called from main thread)."""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + '\n')
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete('1.0', tk.END)
        self.log_text.config(state=tk.DISABLED)

    def run(self):
        self.root.mainloop()


def _read_update_result(argv: list[str]) -> dict | None:
    """Read and remove the temporary result passed by the updater."""
    if "--update-result" not in argv:
        return None
    index = argv.index("--update-result")
    if index + 1 >= len(argv):
        return {"success": False, "message": "更新结果文件参数无效"}
    path = Path(argv[index + 1])
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"success": False, "message": f"无法读取更新结果: {exc}"}
    finally:
        path.unlink(missing_ok=True)


if __name__ == '__main__':
    app = ConversionGUI(_read_update_result(sys.argv[1:]))
    app.run()

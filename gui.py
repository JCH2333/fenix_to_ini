#!/usr/bin/env python3
"""
GUI for Fenix -> iniBuilds Navigation Data Converter.

Uses tkinter (stdlib) for file selection, progress display, and result reporting.
"""

import sys
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_detect import detect_all
from main import run_conversion, set_log_callback


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

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Fenix -> DFDv2 导航数据转换工具 v1.3.0")
        self.root.geometry("800x650")
        self.root.minsize(700, 550)

        self.conversion_thread: threading.Thread | None = None
        self.running = False
        self.redirect: RedirectText | None = None
        self.detected_targets: dict[str, str] = {}

        self._build_ui()

        # Auto-detect on startup
        self.root.after(500, self.auto_detect)

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

        # Row 1: Aircraft target
        ttk.Label(paths_frame, text="目标机模:", width=18, anchor=tk.E).grid(row=1, column=0, sticky=tk.W, pady=2)
        self.aircraft_var = tk.StringVar(value="iniBuilds A340")
        self.aircraft_combo = ttk.Combobox(
            paths_frame,
            textvariable=self.aircraft_var,
            values=("iniBuilds A340", "Aerosoft AS346"),
            state="readonly",
            width=67,
        )
        self.aircraft_combo.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=2)
        self.aircraft_combo.bind("<<ComboboxSelected>>", self._on_aircraft_changed)

        # Row 2: Fenix nd.db3
        ttk.Label(paths_frame, text="Fenix nd.db3:", width=18, anchor=tk.E).grid(row=2, column=0, sticky=tk.W, pady=2)
        self.src_var = tk.StringVar()
        self.src_entry = ttk.Entry(paths_frame, textvariable=self.src_var, width=70)
        self.src_entry.grid(row=2, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Button(paths_frame, text="浏览...", command=self.browse_src).grid(row=2, column=2, pady=2)

        # Row 3: target DFDv2 database
        ttk.Label(paths_frame, text="目标 DFDv2 数据库:", width=18, anchor=tk.E).grid(row=3, column=0, sticky=tk.W, pady=2)
        self.dst_var = tk.StringVar()
        self.dst_entry = ttk.Entry(paths_frame, textvariable=self.dst_var, width=70)
        self.dst_entry.grid(row=3, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Button(paths_frame, text="浏览...", command=self.browse_dst).grid(row=3, column=2, pady=2)

        # Row 4: RTE_SEG.csv
        ttk.Label(paths_frame, text="RTE_SEG.csv (可选):", width=18, anchor=tk.E).grid(row=4, column=0, sticky=tk.W, pady=2)
        self.csv_var = tk.StringVar()
        self.csv_entry = ttk.Entry(paths_frame, textvariable=self.csv_var, width=70)
        self.csv_entry.grid(row=4, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Button(paths_frame, text="浏览...", command=self.browse_csv).grid(row=4, column=2, pady=2)

        paths_frame.columnconfigure(1, weight=1)

        # Options frame
        opt_frame = ttk.LabelFrame(self.root, text=" 转换选项 ", padding=10)
        opt_frame.pack(fill=tk.X, padx=10, pady=5)

        self.procedures_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="转换进离场程序 (SID/STAR/IAP)", variable=self.procedures_var).pack(side=tk.LEFT, padx=10)

        self.rte_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="处理 RTE_SEG.csv", variable=self.rte_var).pack(side=tk.LEFT, padx=10)

        self.backup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="自动备份原文件", variable=self.backup_var).pack(side=tk.LEFT, padx=10)

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

        self.start_btn = ttk.Button(btn_frame, text="开始转换", command=self.start_conversion)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self.stop_conversion, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(btn_frame, text="验证数据库", command=self.verify_database).pack(side=tk.LEFT, padx=5)

        ttk.Button(btn_frame, text="退出", command=self.root.quit).pack(side=tk.RIGHT, padx=5)

        # Status bar
        self.status_var = tk.StringVar(value='就绪 - 点击 [自动检测路径] 或手动输入文件路径')
        status_bar = ttk.Label(self.root, textvariable=self.status_var,
                               relief=tk.SUNKEN, anchor=tk.W, padding=5)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    # ---- File Browsers ----
    def browse_src(self):
        path = filedialog.askopenfilename(
            title="选择 Fenix nd.db3",
            filetypes=[("SQLite Database", "*.db3"), ("All Files", "*.*")]
        )
        if path:
            self.src_var.set(path)

    def browse_dst(self):
        path = filedialog.askopenfilename(
            title="选择目标 DFDv2 数据库",
            filetypes=[("SQLite Database", "*.s3db"), ("All Files", "*.*")]
        )
        if path:
            self.dst_var.set(path)

    def browse_csv(self):
        path = filedialog.askopenfilename(
            title="选择 RTE_SEG.csv (可选)",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        if path:
            self.csv_var.set(path)

    # ---- Auto Detection ----
    def _on_aircraft_changed(self, _event=None):
        path = self.detected_targets.get(self.aircraft_var.get())
        if path:
            self.dst_var.set(path)

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
        ini_results = results.get('ini_s3db', {})
        as346_results = results.get('as346_s3db', {})
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

        if ini_results:
            lines.append(f"  iniBuilds: 检测到 {len(ini_results)} 个位置")
            for label, path in ini_results.items():
                lines.append(f"    [{label}]")
                lines.append(f"    {path}")
        else:
            lines.append(f"  [--] iniBuilds: 未找到")

        if as346_results:
            lines.append(f"  Aerosoft AS346: 检测到 {len(as346_results)} 个位置")
            for label, path in as346_results.items():
                lines.append(f"    [{label}]")
                lines.append(f"    {path}")

        msg = '\n'.join(lines)

        # Auto-select best match. Exclude "目录存在，无s3db" placeholder
        # entries (directory found but no db.s3db written yet) — see main.py
        # for the same fix rationale.
        msfs24_keys = [k for k in ini_results if '2024' in k and '无s3db' not in k]
        msfs20_keys = [k for k in ini_results if '2020' in k]
        selected_ini = None
        selected_label = None
        if msfs24_keys:
            selected_label = msfs24_keys[0]
            selected_ini = ini_results[selected_label]
        elif msfs20_keys:
            selected_label = msfs20_keys[0]
            selected_ini = ini_results[selected_label]

        self.detected_targets = {}
        for label, path in ini_results.items():
            if 'a340' in label.lower() and 'BundledData' in label:
                self.detected_targets.setdefault("iniBuilds A340", path)
        for label, path in as346_results.items():
            if 'fallback' not in label.lower():
                self.detected_targets.setdefault("Aerosoft AS346", path)

        available_targets = list(self.detected_targets)
        if available_targets:
            self.aircraft_combo.configure(values=available_targets)
            if self.aircraft_var.get() not in self.detected_targets:
                self.aircraft_var.set(available_targets[0])
            selected_label = self.aircraft_var.get()
            selected_ini = self.detected_targets[selected_label]

        # Build summary for dialog
        summary = f"Fenix: {fenix or '未找到'}\nCSV: {csv_path or '未找到'}\niniBuilds: {selected_label or '未找到'}"
        if naip and not naip.get('error') and not naip['is_complete']:
            summary += (f"\n\n警告：检测到的 Fenix 数据仅 {naip['cn_airports_with_procs']} 个中国机场含进离场程序，"
                        f"可能不是含 NAIP 数据的完整版，转换后中国机场程序会很少。")

        self.log(msg + '\n')

        # Ask user
        if fenix and selected_ini:
            answer = messagebox.askyesno(
                "自动检测完成",
                f"{summary}\n\n是否自动填入以上路径？"
            )
            if answer:
                self.src_var.set(fenix)
                self.dst_var.set(selected_ini)
                if csv_path:
                    self.csv_var.set(csv_path)
                self.detect_label.config(text=f"已填入: {selected_label}", foreground="green")
                self.status_var.set("路径已自动填入，准备就绪")
            else:
                self.detect_label.config(text="检测完成，请手动输入", foreground="orange")
                self.status_var.set("请手动输入或浏览文件路径")
        elif fenix and not selected_ini:
            self.src_var.set(fenix)
            if csv_path:
                self.csv_var.set(csv_path)
            self.detect_label.config(text="已填入 Fenix 路径，请手动选择 iniBuilds 目标", foreground="orange")
            self.status_var.set("请选择 iniBuilds db.s3db 路径")
            messagebox.showinfo("部分检测", f"Fenix nd.db3 已自动填入。\n\niniBuilds s3db 未自动检测到，请手动选择。\n\n检测到的 iniBuilds 位置:\n{msg}")
        else:
            self.detect_label.config(text="未检测到路径", foreground="red")
            self.status_var.set("未检测到导航数据，请手动输入路径")

    # ---- Conversion ----
    def start_conversion(self):
        """Start the conversion in a background thread."""
        src = self.src_var.get().strip()
        dst = self.dst_var.get().strip()
        csv_path = self.csv_var.get().strip() or None

        # Validate
        if not src:
            messagebox.showerror("错误", "请选择 Fenix nd.db3 文件路径")
            return
        if not dst:
            messagebox.showerror("错误", "请选择目标 DFDv2 数据库文件路径")
            return
        if not os.path.exists(src):
            messagebox.showerror("错误", f"Fenix nd.db3 不存在:\n{src}")
            return
        if not os.path.exists(dst):
            messagebox.showerror("错误", f"目标 DFDv2 数据库不存在:\n{dst}")
            return

        skip_proc = not self.procedures_var.get()
        skip_rte = not self.rte_var.get()
        no_backup = not self.backup_var.get()

        # Confirm overwrite
        if not messagebox.askyesno(
            "确认转换",
            f"源文件: {src}\n目标文件: {dst}\n\n"
            f"{'将备份原文件' if not no_backup else '不备份原文件'}\n"
            f"{'转换进离场程序' if not skip_proc else '跳过进离场程序'}\n"
            f"{'处理RTE_SEG.csv' if not skip_rte else '跳过RTE_SEG.csv'}\n\n"
            f"确认开始转换？"
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
            args=(src, dst, csv_path, skip_proc, skip_rte, no_backup),
            daemon=True
        )
        self.conversion_thread.start()

        # Poll for completion
        self.root.after(200, self._check_thread)

    def _run_conversion_thread(self, src, dst, csv_path, skip_proc, skip_rte, no_backup):
        """Background thread for conversion."""
        try:
            result = run_conversion(
                src_path=src,
                dst_path=dst,
                csv_path=csv_path,
                skip_procedures=skip_proc,
                skip_rte=skip_rte,
                no_backup=no_backup,
                overwrite_mode=True,
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
            self.progress_var.set(100)
            self.progress_label.config(text="转换完成!")
            self.status_var.set(f"转换成功! 输出: {result[1]}")
            messagebox.showinfo("完成", f"导航数据转换完成!\n\n输出文件: {result[1]}")
        else:
            self.progress_label.config(text="转换失败")
            self.status_var.set("转换失败，请查看日志")
            messagebox.showerror("错误", f"转换过程中出现错误:\n\n{result[1][:500]}")

    def stop_conversion(self):
        """Stop a running conversion (best effort)."""
        self.running = False
        self.status_var.set("正在停止...")
        self._set_running(False)

    # ---- Verification ----
    def verify_database(self):
        """Run verification on the selected database."""
        path = self.dst_var.get().strip()
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


if __name__ == '__main__':
    app = ConversionGUI()
    app.run()

from dataclasses import asdict
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from filenest.executor import Executor
from filenest.models import Rule, default_rules
from filenest.paths import inside
from filenest.planner import build_plan
from filenest.scanner import scan
from filenest.storage import Store


STATUS_TEXT = {
    "ready": "待处理", "unmatched": "未匹配", "skipped": "已跳过",
    "invalid": "无效", "pending": "等待", "running": "执行中",
    "success": "成功", "failed": "失败", "cancelled": "已取消",
    "uncertain": "需核对",
}
ACTION_TEXT = {"move": "移动", "copy": "复制", "rename": "重命名"}


def human_size(value):
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def parse_mb(text, optional=False):
    text = text.strip()
    if optional and not text:
        return None
    value = float(text or "0")
    if value < 0:
        raise ValueError("大小不能为负数")
    return int(value * 1024 * 1024)


class RuleDialog(tk.Toplevel):
    def __init__(self, parent, rule=None):
        super().__init__(parent)
        self.title("编辑规则" if rule else "添加规则")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        current = rule or Rule(name="新规则")
        self.original_rule = current
        self.values = {
            "name": tk.StringVar(value=current.name),
            "folder": tk.StringVar(value=current.folder),
            "extensions": tk.StringVar(value=current.extensions),
            "keyword": tk.StringVar(value=current.keyword),
            "min_size": tk.StringVar(value=f"{current.min_size / 1024 / 1024:g}"),
            "max_size": tk.StringVar(value="" if current.max_size is None else f"{current.max_size / 1024 / 1024:g}"),
            "date_from": tk.StringVar(value=current.date_from),
            "date_to": tk.StringVar(value=current.date_to),
            "action": tk.StringVar(value=current.action),
            "conflict": tk.StringVar(value=current.conflict),
            "prefix": tk.StringVar(value=current.prefix),
            "suffix": tk.StringVar(value=current.suffix),
            "replace_from": tk.StringVar(value=current.replace_from),
            "replace_to": tk.StringVar(value=current.replace_to),
            "by_month": tk.BooleanVar(value=current.by_month),
            "add_date": tk.BooleanVar(value=current.add_date),
            "sequence": tk.BooleanVar(value=current.sequence),
            "enabled": tk.BooleanVar(value=current.enabled),
        }
        body = ttk.Frame(self, padding=14)
        body.grid(sticky="nsew")
        ttk.Label(body, text="规则名称").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.values["name"], width=38).grid(row=0, column=1, sticky="ew")
        ttk.Checkbutton(body, text="启用", variable=self.values["enabled"]).grid(row=0, column=2, padx=(10, 0))
        tabs = ttk.Notebook(body)
        tabs.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=10)
        conditions = ttk.Frame(tabs, padding=12)
        action = ttk.Frame(tabs, padding=12)
        tabs.add(conditions, text="匹配条件")
        tabs.add(action, text="处理动作")
        condition_fields = [
            ("扩展名（空格分隔）", "extensions"), ("名称关键词", "keyword"),
            ("最小大小（MB）", "min_size"), ("最大大小（MB，留空不限）", "max_size"),
            ("修改日期从（YYYY-MM-DD）", "date_from"), ("修改日期至（YYYY-MM-DD）", "date_to"),
        ]
        for row, (label, name) in enumerate(condition_fields):
            ttk.Label(conditions, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(conditions, textvariable=self.values[name], width=34).grid(row=row, column=1, sticky="ew", padx=(10, 0))
        action_fields = [
            ("动作", "action", ("move", "copy", "rename")),
            ("目标子目录", "folder", None),
            ("冲突", "conflict", ("skip", "number")),
            ("名称前缀", "prefix", None), ("名称后缀", "suffix", None),
            ("替换文本", "replace_from", None), ("替换为", "replace_to", None),
        ]
        for row, (label, name, options) in enumerate(action_fields):
            ttk.Label(action, text=label).grid(row=row, column=0, sticky="w", pady=4)
            widget = (ttk.Combobox(action, textvariable=self.values[name], values=options,
                                   state="readonly", width=31) if options else
                      ttk.Entry(action, textvariable=self.values[name], width=34))
            widget.grid(row=row, column=1, sticky="ew", padx=(10, 0))
        checks = ttk.Frame(action)
        checks.grid(row=len(action_fields), column=0, columnspan=2, sticky="w", pady=(8, 0))
        for column, (text, name) in enumerate((("按年/月建目录", "by_month"),
                                                ("添加修改日期", "add_date"),
                                                ("添加三位序号", "sequence"))):
            ttk.Checkbutton(checks, text=text, variable=self.values[name]).grid(row=0, column=column, padx=(0, 12))
        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=5)
        ttk.Button(buttons, text="保存", command=self.save).pack(side="left")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self.save())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.wait_visibility()
        self.focus_set()

    def save(self):
        try:
            data = {name: value.get() for name, value in self.values.items()}
            data["min_size"] = parse_mb(data["min_size"])
            data["max_size"] = parse_mb(data["max_size"], optional=True)
            self.result = Rule(**{**asdict(self.original_rule), **data})
            from filenest.rules import validate
            validate(self.result)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("规则无效", str(exc), parent=self)
            return
        self.destroy()


class FileNestApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FileNest 文件整理")
        self.geometry("1360x860")
        self.minsize(1200, 760)
        self.configure(background="#9dcfe3")
        self.data_dir = Path(__file__).resolve().parent.parent / ".filenest"
        self.store = Store(self.data_dir)
        self.store.recover()
        self.executor = Executor(self.store)
        try:
            settings, self.rules = self.store.load_settings()
        except (OSError, ValueError, TypeError) as exc:
            settings, self.rules = {}, default_rules()
            messagebox.showwarning("设置未载入", f"设置文件无效，将恢复默认规则：{exc}", parent=self)
        self.sources = list(settings.get("sources", []))
        self.excludes = list(settings.get("excludes", []))
        self.files, self.plan = [], []
        self.cancel_event = threading.Event()
        self.events = queue.Queue()
        self.busy = False
        self.target_var = tk.StringVar(value=settings.get("target", ""))
        self.recursive_var = tk.BooleanVar(value=settings.get("recursive", True))
        self.notify_var = tk.BooleanVar(value=settings.get("notify", True))
        self.conflict_var = tk.StringVar(value=("自动编号" if settings.get("conflict") == "number"
                                                else "跳过同名文件"))
        self.search_var = tk.StringVar()
        self.filter_mode = "all"
        self.status_var = tk.StringVar(value="请选择来源目录并扫描")
        self.summary_var = tk.StringVar(value="尚未扫描")
        self.stat_vars = {name: tk.StringVar(value="0") for name in
                          ("total", "ready", "conflict", "unmatched")}
        self.selection_var = tk.StringVar(value="已选择 0 个文件")
        self.count_var = tk.StringVar(value="共 0 项")
        self._build_style()
        self._build_ui()
        self._render_sources()
        self._render_excludes()
        self._render_rules()
        self.refresh_history()
        self.after(80, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _build_style(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f4f9fc")
        style.configure("Main.TFrame", background="#dff2f8")
        style.configure("Page.TFrame", background="#eef8fb")
        style.configure("Sidebar.TFrame", background="#eef6f4")
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure("TLabel", background="#f4f9fc", foreground="#263b55",
                        font=("Microsoft YaHei UI", 10))
        style.configure("Page.TLabel", background="#eef8fb")
        style.configure("Card.TLabel", background="#ffffff")
        style.configure("Title.TLabel", background="#eef8fb", foreground="#24354d",
                        font=("Microsoft YaHei UI", 21, "bold"))
        style.configure("Section.TLabel", background="#ffffff", foreground="#253b55",
                        font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Muted.TLabel", foreground="#71839a", background="#eef8fb",
                        font=("Microsoft YaHei UI", 9))
        style.configure("CardMuted.TLabel", foreground="#7b899c", background="#ffffff",
                        font=("Microsoft YaHei UI", 9))
        style.configure("TButton", padding=(12, 8), borderwidth=0,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Primary.TButton", background="#2a9ede", foreground="white",
                        font=("Microsoft YaHei UI", 10, "bold"), padding=(18, 10))
        style.map("Primary.TButton", background=[("active", "#248bc7"), ("disabled", "#a9c8d8")])
        style.configure("Filter.TButton", background="#ffffff", foreground="#71839a", padding=(9, 5))
        style.configure("Active.Filter.TButton", background="#e1efff", foreground="#2868ad", padding=(9, 5))
        style.configure("Nav.TButton", background="#eef6f4", foreground="#65758c",
                        anchor="w", padding=(18, 13), font=("Microsoft YaHei UI", 10))
        style.configure("Active.Nav.TButton", background="#bde5f3", foreground="#234d68",
                        anchor="w", padding=(18, 13), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Nav.TButton", background=[("active", "#d9edf3")])
        style.map("Active.Nav.TButton", background=[("active", "#acdcec")])
        style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff",
                        foreground="#52677f", rowheight=32, borderwidth=0,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", background="#f3f7fb", foreground="#74859b",
                        relief="flat", padding=(8, 8), font=("Microsoft YaHei UI", 9))
        style.map("Treeview", background=[("selected", "#dbeeff")],
                  foreground=[("selected", "#254e78")])
        style.configure("TEntry", fieldbackground="#fbfdff", padding=8)
        style.configure("TCombobox", fieldbackground="#fbfdff", padding=6)

    def _build_ui(self):
        shell = tk.Frame(self, background="#eef8fb", highlightthickness=1,
                         highlightbackground="#ffffff")
        shell.pack(fill="both", expand=True, padx=22, pady=22)
        shell.grid_columnconfigure(1, weight=1)
        shell.grid_rowconfigure(0, weight=1)

        sidebar = ttk.Frame(shell, style="Sidebar.TFrame", width=225, padding=(18, 26))
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        brand = ttk.Frame(sidebar, style="Sidebar.TFrame")
        brand.pack(fill="x", padx=4)
        tk.Label(brand, text="▱", background="#f4a249", foreground="white",
                 font=("Segoe UI Symbol", 18, "bold"), width=2, pady=3).pack(side="left")
        tk.Label(brand, text="FileNest", background="#eef6f4", foreground="#24354d",
                 font=("Segoe UI", 20, "bold")).pack(side="left", padx=(9, 0))
        tk.Label(brand, text=".", background="#eef6f4", foreground="#f28b35",
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        tk.Label(sidebar, text="让每一份文件，都有归处。", background="#eef6f4",
                 foreground="#7b899c", font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=5, pady=(8, 34))
        tk.Label(sidebar, text="WORKSPACE / 工作空间", background="#eef6f4",
                 foreground="#8b99ae", font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(0, 10))
        self.nav_buttons = {}
        nav_items = (("organize", "ϟ   快速整理"), ("rules", "☷   规则管理"),
                     ("history", "◷   历史与撤销"), ("settings", "⚙   偏好设置"))
        for key, text in nav_items:
            button = ttk.Button(sidebar, text=text, style="Nav.TButton",
                                command=lambda name=key: self.show_page(name))
            button.pack(fill="x", pady=4)
            self.nav_buttons[key] = button
        ttk.Frame(sidebar, style="Sidebar.TFrame").pack(fill="both", expand=True)
        tip = tk.Frame(sidebar, background="#ffffff", highlightthickness=1,
                       highlightbackground="#d9eaf0", padx=13, pady=12)
        tip.pack(fill="x", pady=(12, 18))
        tk.Label(tip, text="有序，从一个文件夹开始", background="#ffffff",
                 foreground="#344b64", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        tk.Label(tip, text="先预览，再整理。\n每一步操作都有迹可循。", justify="left",
                 background="#ffffff", foreground="#7a899e",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(5, 0))
        tk.Label(sidebar, text="●  本地工作空间", background="#eef6f4", foreground="#d6813b",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=8)

        main = ttk.Frame(shell, style="Main.TFrame")
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        topbar = tk.Frame(main, background="#f7fbfd", height=58,
                          highlightthickness=1, highlightbackground="#ffffff")
        topbar.grid(row=0, column=0, sticky="ew")
        topbar.grid_propagate(False)
        self.breadcrumb_var = tk.StringVar(value="快速整理")
        tk.Label(topbar, text="工作空间   /", background="#f7fbfd", foreground="#8392a8",
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(28, 12), pady=19)
        tk.Label(topbar, textvariable=self.breadcrumb_var, background="#f7fbfd", foreground="#435670",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left", pady=19)
        tk.Label(topbar, text="本地处理 · 无需联网", background="#fff1e5", foreground="#b66c2f",
                 font=("Microsoft YaHei UI", 8), padx=10, pady=4).pack(side="right", padx=28, pady=16)

        self.page_host = ttk.Frame(main, style="Page.TFrame", padding=(28, 22))
        self.page_host.grid(row=1, column=0, sticky="nsew")
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.grid_columnconfigure(0, weight=1)
        self.pages = {}
        for name in ("organize", "rules", "history", "settings"):
            page = ttk.Frame(self.page_host, style="Page.TFrame")
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page
        self.organize_tab = self.pages["organize"]
        self.rules_tab = self.pages["rules"]
        self.history_tab = self.pages["history"]
        self.settings_tab = self.pages["settings"]
        self._build_organize()
        self._build_rules()
        self._build_history()
        self._build_settings()
        self.show_page("organize")

    def show_page(self, name):
        titles = {"organize": "快速整理", "rules": "规则管理",
                  "history": "历史与撤销", "settings": "偏好设置"}
        self.pages[name].tkraise()
        self.breadcrumb_var.set(titles[name])
        for key, button in self.nav_buttons.items():
            button.configure(style="Active.Nav.TButton" if key == name else "Nav.TButton")

    def _page_heading(self, parent, title, subtitle, badge=""):
        row = ttk.Frame(parent, style="Page.TFrame")
        row.pack(fill="x", pady=(0, 16))
        text = ttk.Frame(row, style="Page.TFrame")
        text.pack(side="left")
        ttk.Label(text, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(text, text=subtitle, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        if badge:
            tk.Label(row, text=badge, background="#fff1e5", foreground="#b66c2f",
                     font=("Microsoft YaHei UI", 8), padx=10, pady=4).pack(side="right", anchor="n", pady=5)

    def _card(self, parent, padding=16):
        return tk.Frame(parent, background="#ffffff", highlightthickness=1,
                        highlightbackground="#dce8ee", padx=padding, pady=padding)

    def _build_organize(self):
        self._page_heading(self.organize_tab, "把散落的文件，放回合适的位置。",
                           "选择目录、检查整理策略，然后在预览中确认每一项变更。")
        body_shell = ttk.Frame(self.organize_tab, style="Page.TFrame")
        body_shell.pack(fill="both", expand=True)
        body_shell.grid_rowconfigure(0, weight=1)
        body_shell.grid_columnconfigure(0, weight=1)
        self.organize_canvas = tk.Canvas(body_shell, background="#eef8fb", highlightthickness=0)
        body_scroll = ttk.Scrollbar(body_shell, orient="vertical", command=self.organize_canvas.yview)
        self.organize_canvas.configure(yscrollcommand=body_scroll.set)
        self.organize_canvas.grid(row=0, column=0, sticky="nsew")
        body_scroll.grid(row=0, column=1, sticky="ns")
        organize_content = tk.Frame(self.organize_canvas, background="#eef8fb")
        organize_window = self.organize_canvas.create_window((0, 0), window=organize_content, anchor="nw")
        organize_content.bind("<Configure>", lambda _e: self.organize_canvas.configure(
            scrollregion=self.organize_canvas.bbox("all")))
        self.organize_canvas.bind("<Configure>",
                                  lambda e: self.organize_canvas.itemconfigure(organize_window, width=e.width))
        self.organize_canvas.bind("<MouseWheel>",
                                  lambda e: self.organize_canvas.yview_scroll(-int(e.delta / 120), "units"))

        config = self._card(organize_content, 18)
        config.pack(fill="x", pady=(0, 14))
        top = tk.Frame(config, background="#ffffff")
        top.pack(fill="x", pady=(0, 13))
        tk.Label(top, text="选择待整理目录", background="#ffffff", foreground="#253b55",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        ttk.Button(top, text="管理排除目录", command=lambda: self.show_page("settings")).pack(side="right")
        fields = tk.Frame(config, background="#ffffff")
        fields.pack(fill="x")
        fields.grid_columnconfigure(0, weight=5)
        fields.grid_columnconfigure(1, weight=5)
        fields.grid_columnconfigure(2, weight=3)
        source = tk.Frame(fields, background="#ffffff")
        source.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        tk.Label(source, text="待整理目录", background="#ffffff", foreground="#6e7e94",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(0, 5))
        self.source_list = tk.Listbox(source, height=2, exportselection=False, relief="solid",
                                      borderwidth=1, highlightthickness=0, foreground="#455b78",
                                      background="#fbfdff", selectbackground="#dbeeff",
                                      font=("Microsoft YaHei UI", 9))
        self.source_list.pack(fill="x")
        source_buttons = tk.Frame(source, background="#ffffff")
        source_buttons.pack(anchor="w", pady=(6, 0))
        ttk.Button(source_buttons, text="＋ 添加目录", command=self.add_source).pack(side="left")
        ttk.Button(source_buttons, text="移除", command=self.remove_source).pack(side="left", padx=5)

        target = tk.Frame(fields, background="#ffffff")
        target.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        tk.Label(target, text="目标目录", background="#ffffff", foreground="#6e7e94",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(0, 5))
        target_entry = ttk.Entry(target, textvariable=self.target_var)
        target_entry.pack(fill="x")
        target_entry.bind("<KeyRelease>", lambda _e: self.invalidate())
        ttk.Button(target, text="选择目标目录…", command=self.choose_target).pack(anchor="w", pady=(6, 0))
        rule_info = tk.Frame(fields, background="#ffffff")
        rule_info.grid(row=0, column=2, sticky="nsew")
        tk.Label(rule_info, text="使用规则", background="#ffffff", foreground="#6e7e94",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(0, 5))
        self.rule_count_label = tk.Label(rule_info, text="", background="#fbfdff", foreground="#455b78",
                                         anchor="w", padx=9, pady=8, relief="solid", borderwidth=1,
                                         font=("Microsoft YaHei UI", 9))
        self.rule_count_label.pack(fill="x")
        ttk.Button(rule_info, text="查看规则", command=lambda: self.show_page("rules")).pack(anchor="w", pady=(6, 0))
        config_bottom = tk.Frame(config, background="#ffffff")
        config_bottom.pack(fill="x", pady=(13, 0))
        ttk.Checkbutton(config_bottom, text="包含子目录", variable=self.recursive_var,
                        command=self.invalidate).pack(side="left")
        tk.Label(config_bottom, text="· 默认排除临时文件和内部数据", background="#ffffff",
                 foreground="#a2aec0", font=("Microsoft YaHei UI", 8)).pack(side="left", padx=8)
        self.scan_button = ttk.Button(config_bottom, text="↻  重新扫描", command=self.start_scan)
        self.scan_button.pack(side="right")

        strategy = self._card(organize_content, 16)
        strategy.pack(fill="x", pady=(0, 14))
        strategy_head = tk.Frame(strategy, background="#ffffff")
        strategy_head.pack(fill="x", pady=(0, 11))
        tk.Label(strategy_head, text="整理策略", background="#ffffff", foreground="#253b55",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        tk.Label(strategy_head, text="点击启用或停用 · 更改后刷新预览", background="#ffffff",
                 foreground="#8795a6", font=("Microsoft YaHei UI", 8)).pack(side="right")
        strategy_grid = tk.Frame(strategy, background="#ffffff")
        strategy_grid.pack(fill="x")
        for column in range(4):
            strategy_grid.grid_columnconfigure(column, weight=1, uniform="strategy")
        strategy_data = (
            ("images", "▧  图片与素材\n.jpg   .png", "#2f9bd7"),
            ("documents", "▤  办公文档\n.pdf   .docx", "#f39a3e"),
            ("media", "⬡  视频与压缩包\n.mp4   .zip", "#2599a8"),
            ("date", "▦  按年份月份\n全部分类", "#738fca"),
        )
        self.strategy_buttons = {}
        for column, (key, text, color) in enumerate(strategy_data):
            button = tk.Button(strategy_grid, text=text, command=lambda value=key: self.toggle_strategy(value),
                               justify="left", anchor="w", background=color, foreground="white",
                               activebackground=color, activeforeground="white", relief="flat",
                               cursor="hand2", padx=13, pady=10, font=("Microsoft YaHei UI", 9, "bold"))
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 5, 0))
            self.strategy_buttons[key] = button

        stats = tk.Frame(organize_content, background="#ffffff", highlightthickness=1,
                         highlightbackground="#dce8ee", pady=10)
        stats.pack(fill="x", pady=(0, 14))
        stat_data = (("total", "扫描文件", "#36536d"), ("ready", "可以整理", "#2879bc"),
                     ("conflict", "需关注", "#db812f"), ("unmatched", "未匹配规则", "#71839a"))
        for column, (key, title, color) in enumerate(stat_data):
            stats.grid_columnconfigure(column, weight=1, uniform="stats")
            item = tk.Frame(stats, background="#ffffff")
            item.grid(row=0, column=column, sticky="ew", padx=18)
            tk.Label(item, text=title, background="#ffffff", foreground="#7b8aa1",
                     font=("Microsoft YaHei UI", 8)).pack(side="left")
            tk.Label(item, textvariable=self.stat_vars[key], background="#ffffff", foreground=color,
                     font=("Segoe UI", 16, "bold")).pack(side="right")

        preview = self._card(organize_content, 0)
        preview.pack(fill="both", expand=True)
        preview_head = tk.Frame(preview, background="#ffffff", padx=16, pady=12)
        preview_head.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(preview_head, text="变更预览", background="#ffffff", foreground="#253b55",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        self.filter_buttons = {}
        for key, text in (("all", "全部文件"), ("ready", "待整理"), ("attention", "需关注")):
            button = ttk.Button(preview_head, text=text, style="Filter.TButton",
                                command=lambda value=key: self.set_filter(value))
            button.pack(side="left", padx=(12 if key == "all" else 3, 0))
            self.filter_buttons[key] = button
        search = ttk.Entry(preview_head, textvariable=self.search_var, width=22)
        search.pack(side="right")
        self.search_var.trace_add("write", lambda *_args: self._render_plan())
        columns = ("name", "size", "action", "destination", "status")
        self.plan_tree = ttk.Treeview(preview, columns=columns, show="headings", selectmode="extended")
        headings = {"name": "文件名称", "size": "大小", "action": "处理方式",
                    "destination": "目标位置", "status": "状态"}
        widths = {"name": 210, "size": 80, "action": 85, "destination": 430, "status": 90}
        for name in columns:
            self.plan_tree.heading(name, text=headings[name])
            self.plan_tree.column(name, width=widths[name], minwidth=55, stretch=name == "destination")
        scrollbar = ttk.Scrollbar(preview, orient="vertical", command=self.plan_tree.yview)
        horizontal = ttk.Scrollbar(preview, orient="horizontal", command=self.plan_tree.xview)
        self.plan_tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.plan_tree.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        horizontal.grid(row=2, column=0, sticky="ew")
        self.plan_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_selection_text())
        self.plan_tree.bind("<Double-1>", self.show_plan_detail)
        footer = tk.Frame(preview, background="#ffffff", padx=16, pady=8)
        footer.grid(row=3, column=0, columnspan=2, sticky="ew")
        tk.Label(footer, textvariable=self.selection_var, background="#ffffff", foreground="#8797ae",
                 font=("Microsoft YaHei UI", 8)).pack(side="left")
        tk.Label(footer, textvariable=self.count_var, background="#ffffff", foreground="#8797ae",
                 font=("Microsoft YaHei UI", 8)).pack(side="right")
        preview.rowconfigure(1, weight=1)
        preview.columnconfigure(0, weight=1)
        bottom = ttk.Frame(self.organize_tab, style="Page.TFrame")
        bottom.pack(fill="x", pady=(12, 0))
        ttk.Label(bottom, text="♧  文件安全优先 · 默认不覆盖 · 操作可追溯",
                  style="Muted.TLabel").pack(side="left")
        self.undo_latest_button = ttk.Button(bottom, text="撤销上一次", command=self.start_undo_latest)
        self.undo_latest_button.pack(side="right", padx=(6, 0))
        self.cancel_button = ttk.Button(bottom, text="取消任务", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="right", padx=(6, 0))
        self.preview_button = ttk.Button(bottom, text="刷新预览", command=self.start_preview)
        self.preview_button.pack(side="right", padx=(6, 0))
        self.execute_button = ttk.Button(bottom, text="立即开始整理  →", style="Primary.TButton",
                                         command=self.start_execute)
        self.execute_button.pack(side="right")
        ttk.Label(self.organize_tab, textvariable=self.status_var, style="Muted.TLabel").pack(fill="x", pady=(8, 0))
        self.filter_buttons["all"].configure(style="Active.Filter.TButton")

    def _build_rules(self):
        self._page_heading(self.rules_tab, "让规则，替你记住。",
                           "按顺序匹配文件，为每一种资料安排合适的位置。", "规则可随时调整")
        bar = ttk.Frame(self.rules_tab, style="Page.TFrame")
        bar.pack(fill="x", pady=(0, 10))
        ttk.Label(bar, text="文件命中第一条启用规则后停止；未匹配文件保持原位。",
                  style="Muted.TLabel").pack(side="left")
        ttk.Button(bar, text="＋ 添加规则", style="Primary.TButton", command=self.add_rule).pack(side="right")
        canvas_card = self._card(self.rules_tab, 10)
        canvas_card.pack(fill="both", expand=True)
        self.rule_canvas = tk.Canvas(canvas_card, background="#ffffff", highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_card, orient="vertical", command=self.rule_canvas.yview)
        self.rule_canvas.configure(yscrollcommand=scrollbar.set)
        self.rule_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.rule_cards_frame = tk.Frame(self.rule_canvas, background="#ffffff")
        self.rule_window = self.rule_canvas.create_window((0, 0), window=self.rule_cards_frame, anchor="nw")
        self.rule_cards_frame.bind("<Configure>",
                                   lambda _e: self.rule_canvas.configure(scrollregion=self.rule_canvas.bbox("all")))
        self.rule_canvas.bind("<Configure>",
                              lambda e: self.rule_canvas.itemconfigure(self.rule_window, width=e.width))

    def _build_history(self):
        self._page_heading(self.history_tab, "每一次整理，都有记录。",
                           "查看逐文件结果；安全条件满足时，可将移动和重命名恢复到原位置。", "本地历史")
        card = self._card(self.history_tab, 14)
        card.pack(fill="both", expand=True)
        card.grid_rowconfigure(1, weight=2)
        card.grid_rowconfigure(3, weight=3)
        card.grid_columnconfigure(0, weight=1)
        bar = tk.Frame(card, background="#ffffff")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(bar, text="执行批次", background="#ffffff", foreground="#253b55",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        ttk.Button(bar, text="刷新", command=self.refresh_history).pack(side="right")
        ttk.Button(bar, text="撤销所选批次", command=self.start_undo).pack(side="right", padx=5)
        self.batch_tree = ttk.Treeview(card, columns=("time", "total", "success", "failed", "undone"),
                                       show="headings", height=4)
        for name, label, width in (("time", "时间", 180), ("total", "总数", 70),
                                   ("success", "成功", 70), ("failed", "失败", 70), ("undone", "已撤销", 80)):
            self.batch_tree.heading(name, text=label)
            self.batch_tree.column(name, width=width)
        self.batch_tree.grid(row=1, column=0, sticky="nsew")
        self.batch_tree.bind("<<TreeviewSelect>>", lambda _e: self.show_operations())
        tk.Label(card, text="逐文件记录", background="#ffffff", foreground="#253b55",
                 font=("Microsoft YaHei UI", 10, "bold")).grid(row=2, column=0, sticky="w", pady=(14, 7))
        columns = ("source", "destination", "action", "status", "undo", "reason")
        self.operation_tree = ttk.Treeview(card, columns=columns, show="headings", height=7)
        for name, label, width in (("source", "来源", 230), ("destination", "目标", 230),
                                   ("action", "动作", 65), ("status", "结果", 75),
                                   ("undo", "撤销", 75), ("reason", "说明", 270)):
            self.operation_tree.heading(name, text=label)
            self.operation_tree.column(name, width=width, stretch=name in {"source", "destination", "reason"})
        self.operation_tree.grid(row=3, column=0, sticky="nsew")

    def _build_settings(self):
        self._page_heading(self.settings_tab, "按你的习惯，整理。",
                           "常用偏好集中在这里，修改后会自动保存。")
        card = self._card(self.settings_tab, 20)
        card.pack(fill="both", expand=True)
        conflict = tk.Frame(card, background="#ffffff", pady=12)
        conflict.pack(fill="x")
        conflict_text = tk.Frame(conflict, background="#ffffff")
        conflict_text.pack(side="left")
        tk.Label(conflict_text, text="遇到同名文件", background="#ffffff", foreground="#253b55",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        tk.Label(conflict_text, text="应用到全部规则，修改后需要重新生成预览。", background="#ffffff",
                 foreground="#7b899c", font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(4, 0))
        conflict_box = ttk.Combobox(conflict, textvariable=self.conflict_var, state="readonly", width=18,
                                    values=("跳过同名文件", "自动编号"))
        conflict_box.pack(side="right")
        conflict_box.bind("<<ComboboxSelected>>", lambda _e: self.apply_conflict_preference())
        ttk.Separator(card).pack(fill="x", pady=5)

        notice = tk.Frame(card, background="#ffffff", pady=12)
        notice.pack(fill="x")
        notice_text = tk.Frame(notice, background="#ffffff")
        notice_text.pack(side="left")
        tk.Label(notice_text, text="整理完成通知", background="#ffffff", foreground="#253b55",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        tk.Label(notice_text, text="任务结束后显示完成提示。", background="#ffffff",
                 foreground="#7b899c", font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(notice, variable=self.notify_var, command=self.save_preferences).pack(side="right")
        ttk.Separator(card).pack(fill="x", pady=5)

        excluded = tk.Frame(card, background="#ffffff", pady=12)
        excluded.pack(fill="x")
        excluded_top = tk.Frame(excluded, background="#ffffff")
        excluded_top.pack(fill="x")
        tk.Label(excluded_top, text="排除目录", background="#ffffff", foreground="#253b55",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        ttk.Button(excluded_top, text="添加", command=self.add_exclude).pack(side="right")
        ttk.Button(excluded_top, text="移除", command=self.remove_exclude).pack(side="right", padx=5)
        tk.Label(excluded, text="扫描时跳过这些目录；目标目录和内部数据会始终排除。", background="#ffffff",
                 foreground="#7b899c", font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(4, 7))
        self.exclude_list = tk.Listbox(excluded, height=5, exportselection=False, relief="solid",
                                       borderwidth=1, highlightthickness=0, foreground="#455b78",
                                       background="#fbfdff", selectbackground="#dbeeff",
                                       font=("Microsoft YaHei UI", 9))
        self.exclude_list.pack(fill="x")
        ttk.Separator(card).pack(fill="x", pady=5)
        about = tk.Frame(card, background="#ffffff", pady=12)
        about.pack(fill="x")
        about_text = tk.Frame(about, background="#ffffff")
        about_text.pack(side="left")
        tk.Label(about_text, text="关于 FileNest", background="#ffffff", foreground="#253b55",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        tk.Label(about_text, text="本地处理文件，执行前预览，移动与重命名可安全撤销。",
                 background="#ffffff", foreground="#7b899c",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(4, 0))
        tk.Label(about, text="v0.1 基础版", background="#edf3ff", foreground="#5981c9",
                 font=("Microsoft YaHei UI", 8), padx=9, pady=4).pack(side="right")

    def _save_settings(self):
        self.store.save_settings({"sources": self.sources, "excludes": self.excludes,
                                  "target": self.target_var.get().strip(),
                                  "recursive": self.recursive_var.get(),
                                  "notify": self.notify_var.get(),
                                  "conflict": "number" if self.conflict_var.get() == "自动编号" else "skip"},
                                 self.rules)

    def save_preferences(self):
        try:
            self._save_settings()
        except OSError as exc:
            self.status_var.set(f"保存设置失败：{exc}")

    def apply_conflict_preference(self):
        value = "number" if self.conflict_var.get() == "自动编号" else "skip"
        for rule in self.rules:
            rule.conflict = value
        self._render_rules()
        self.invalidate()

    def invalidate(self):
        if self.plan:
            self.plan = []
            self._render_plan()
            self.status_var.set("设置已改变，请重新扫描并生成预览")
        try:
            self._save_settings()
        except OSError as exc:
            self.status_var.set(f"保存设置失败：{exc}")

    def _render_sources(self):
        self.source_list.delete(0, "end")
        for value in self.sources:
            self.source_list.insert("end", value)

    def _render_excludes(self):
        self.exclude_list.delete(0, "end")
        for value in self.excludes:
            self.exclude_list.insert("end", value)

    def add_source(self):
        path = filedialog.askdirectory(parent=self, title="选择待整理目录", mustexist=True)
        if path and path not in self.sources:
            self.sources.append(path)
            self._render_sources()
            self.invalidate()

    def remove_source(self):
        selected = self.source_list.curselection()
        if selected:
            self.sources.pop(selected[0])
            self._render_sources()
            self.invalidate()

    def add_exclude(self):
        path = filedialog.askdirectory(parent=self, title="选择排除目录", mustexist=True)
        if path and path not in self.excludes:
            self.excludes.append(path)
            self._render_excludes()
            self.invalidate()

    def remove_exclude(self):
        selected = self.exclude_list.curselection()
        if selected:
            self.excludes.pop(selected[0])
            self._render_excludes()
            self.invalidate()

    def choose_target(self):
        path = filedialog.askdirectory(parent=self, title="选择整理目标目录", mustexist=True)
        if path:
            self.target_var.set(path)
            self.invalidate()

    def _start_worker(self, label, task, done):
        if self.busy:
            return
        self.busy = True
        self.cancel_event = threading.Event()
        self.status_var.set(label)
        self._set_controls()

        def run():
            try:
                result = task()
                self.events.put(("done", done, result))
            except Exception as exc:
                self.events.put(("error", exc))

        threading.Thread(target=run, daemon=True).start()

    def _progress(self, text):
        self.events.put(("progress", text))

    def _poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    self.status_var.set(event[1])
                elif event[0] == "error":
                    self.busy = False
                    self._set_controls()
                    self.status_var.set(f"操作失败：{event[1]}")
                    messagebox.showerror("操作失败", str(event[1]), parent=self)
                elif event[0] == "done":
                    self.busy = False
                    self._set_controls()
                    event[1](event[2])
        except queue.Empty:
            pass
        self.after(80, self._poll_events)

    def _set_controls(self):
        state = "disabled" if self.busy else "normal"
        for widget in (self.scan_button, self.preview_button, self.execute_button):
            widget.configure(state=state)
        for widget in self.strategy_buttons.values():
            widget.configure(state=state)
        self.cancel_button.configure(state="normal" if self.busy else "disabled")
        has_history = bool(self.batch_tree.get_children())
        self.undo_latest_button.configure(state="disabled" if self.busy or not has_history else "normal")

    def cancel(self):
        self.cancel_event.set()
        self.status_var.set("正在停止后续操作…")

    def start_scan(self):
        if not self.sources:
            messagebox.showinfo("缺少目录", "请先添加至少一个来源目录。", parent=self)
            return
        excludes = [*self.excludes]
        target = self.target_var.get().strip()
        if target and any(inside(source, target) for source in self.sources):
            messagebox.showerror("目录关系无效", "整理目标不能等于来源目录，也不能包含来源目录。\n"
                                 "目标可以放在来源目录内部，扫描时会自动排除它。", parent=self)
            return
        if target:
            excludes.append(target)
        excludes.append(str(self.data_dir))
        self._start_worker("正在扫描…", lambda: scan(self.sources, self.recursive_var.get(), excludes,
                                                     self.cancel_event, self._progress), self._scan_done)

    def _scan_done(self, result):
        self.files, errors = result
        self.plan = []
        self._render_plan()
        categories = {}
        for file in self.files:
            categories[file.category] = categories.get(file.category, 0) + 1
        detail = "，".join(f"{k} {v}" for k, v in categories.items()) or "无文件"
        self.summary_var.set(f"共 {len(self.files)} 个文件｜{detail}")
        if self.cancel_event.is_set():
            self.status_var.set(f"扫描已停止，已找到 {len(self.files)} 个文件")
        else:
            self.status_var.set(f"扫描完成；{len(errors)} 项无法读取" if errors else "扫描完成，请生成预览")
        if errors:
            messagebox.showwarning("部分项目未扫描", "\n".join(errors[:20]) +
                                   (f"\n…另有 {len(errors)-20} 项" if len(errors) > 20 else ""), parent=self)

    def start_preview(self):
        target = self.target_var.get().strip()
        if not self.files:
            messagebox.showinfo("尚未扫描", "请先扫描来源目录。", parent=self)
            return
        if not target:
            messagebox.showinfo("缺少目标", "请选择整理目标目录。", parent=self)
            return
        if not self.rules:
            messagebox.showinfo("缺少规则", "请在规则管理中添加规则。", parent=self)
            return
        self._start_worker("正在生成预览…", lambda: build_plan(self.files, self.rules, target,
                           internal=(self.data_dir,), cancel=self.cancel_event,
                           progress=self._progress), self._preview_done)

    def _preview_done(self, plan):
        self.plan = plan
        self._render_plan(select_ready=True)
        ready = sum(item.status == "ready" for item in plan)
        if self.cancel_event.is_set():
            self.status_var.set(f"预览已停止：当前有 {ready} 个文件可处理。如需完整结果，请重新预览。")
        else:
            self.status_var.set(f"预览完成：{ready} 个文件可处理。请核对目标路径后选择并执行。")

    def set_filter(self, value):
        self.filter_mode = value
        for key, button in self.filter_buttons.items():
            button.configure(style="Active.Filter.TButton" if key == value else "Filter.TButton")
        self._render_plan(select_ready=value == "ready")

    def _attention(self, item):
        return item.status in {"skipped", "invalid"} or "冲突" in item.reason

    def _render_plan(self, select_ready=False):
        self.plan_tree.delete(*self.plan_tree.get_children())
        ready_ids = []
        file_by_path = {f.path: f for f in self.files}
        query = self.search_var.get().strip().casefold()
        visible = []
        for index, item in enumerate(self.plan):
            if self.filter_mode == "ready" and item.status != "ready":
                continue
            if self.filter_mode == "attention" and not self._attention(item):
                continue
            if query and query not in Path(item.source).name.casefold():
                continue
            visible.append(item)
            file = file_by_path.get(item.source)
            iid = str(index)
            self.plan_tree.insert("", "end", iid=iid, values=(Path(item.source).name,
                human_size(file.size) if file else "", ACTION_TEXT.get(item.action, "—"),
                item.destination or "—", STATUS_TEXT.get(item.status, item.status)),
                tags=(item.status,))
            if item.status == "ready":
                ready_ids.append(iid)
        self.plan_tree.tag_configure("ready", foreground="#326aa8")
        self.plan_tree.tag_configure("skipped", foreground="#bd742f")
        self.plan_tree.tag_configure("invalid", foreground="#b45454")
        self.plan_tree.tag_configure("unmatched", foreground="#8291a3")
        if select_ready and ready_ids:
            self.plan_tree.selection_set(ready_ids)
        self.count_var.set(f"共 {len(self.plan)} 项 · 当前显示 {len(visible)} 项")
        self._update_selection_text()
        self._update_stats()

    def _update_selection_text(self):
        selected = sum(self.plan[int(i)].status == "ready" for i in self.plan_tree.selection()
                       if i.isdigit() and int(i) < len(self.plan))
        self.selection_var.set(f"已选择 {selected} 个可整理文件")

    def _update_stats(self):
        self.stat_vars["total"].set(str(len(self.files)))
        self.stat_vars["ready"].set(str(sum(item.status == "ready" for item in self.plan)))
        self.stat_vars["conflict"].set(str(sum(self._attention(item) for item in self.plan)))
        self.stat_vars["unmatched"].set(str(sum(item.status == "unmatched" for item in self.plan)))

    def show_plan_detail(self, event=None):
        iid = self.plan_tree.identify_row(event.y) if event else self.plan_tree.focus()
        if not iid or not iid.isdigit() or int(iid) >= len(self.plan):
            return
        item = self.plan[int(iid)]
        messagebox.showinfo(Path(item.source).name,
                            f"来源：{item.source}\n\n目标：{item.destination or '保持原位'}\n\n"
                            f"处理：{ACTION_TEXT.get(item.action, '跳过')}\n状态："
                            f"{STATUS_TEXT.get(item.status, item.status)}\n\n说明：{item.reason}", parent=self)

    def start_execute(self):
        selected = [self.plan[int(i)] for i in self.plan_tree.selection() if self.plan[int(i)].status == "ready"]
        if not selected:
            messagebox.showinfo("没有选择", "请在预览列表中选择至少一个待处理文件。", parent=self)
            return
        if not messagebox.askyesno("确认执行", f"将按预览处理 {len(selected)} 个文件。是否继续？",
                                   icon="warning", parent=self):
            return
        self._start_worker("正在执行…", lambda: self.executor.execute(selected, self.cancel_event,
                           self._progress), self._execute_done)

    def _execute_done(self, batch):
        self.status_var.set("执行结束。请在历史记录中查看逐文件结果。")
        self.plan = []
        self._render_plan()
        self.refresh_history()
        self.show_page("history")
        if self.batch_tree.exists(batch):
            self.batch_tree.selection_set(batch)
            self.batch_tree.focus(batch)
            self.show_operations()
        if self.notify_var.get():
            rows = self.store.operations(batch)
            success = sum(row["status"] == "success" for row in rows)
            issues = sum(row["status"] in {"failed", "uncertain"} for row in rows)
            messagebox.showinfo("整理完成", f"成功处理 {success} 个文件。"
                                + (f"\n有 {issues} 项需要查看。" if issues else ""), parent=self)

    def _rule_conditions(self, rule):
        parts = []
        if rule.extensions:
            parts.append("类型：" + rule.extensions)
        if rule.keyword:
            parts.append("名称含：" + rule.keyword)
        if rule.min_size:
            parts.append(f"≥ {human_size(rule.min_size)}")
        if rule.max_size is not None:
            parts.append(f"≤ {human_size(rule.max_size)}")
        if rule.date_from or rule.date_to:
            parts.append(f"日期：{rule.date_from or '不限'} 至 {rule.date_to or '不限'}")
        return "；".join(parts) or "所有文件"

    def _render_rules(self):
        for widget in self.rule_cards_frame.winfo_children():
            widget.destroy()
        self.rule_enabled_vars = []
        palette = ("#edf4ff", "#fff2e6", "#e8f7f7", "#f0f2fb", "#f8eff8", "#eef6ee")
        for index, rule in enumerate(self.rules):
            row, column = divmod(index, 3)
            self.rule_cards_frame.grid_columnconfigure(column, weight=1, uniform="rules")
            card = tk.Frame(self.rule_cards_frame, background="#ffffff", highlightthickness=1,
                            highlightbackground="#dce8ee", padx=15, pady=14)
            card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)
            top = tk.Frame(card, background="#ffffff")
            top.pack(fill="x")
            tk.Label(top, text=f"RULE {index + 1:02d}", background="#ffffff", foreground="#91a0b3",
                     font=("Segoe UI", 8)).pack(side="left")
            enabled = tk.BooleanVar(value=rule.enabled)
            self.rule_enabled_vars.append(enabled)
            ttk.Checkbutton(top, text="启用", variable=enabled,
                            command=lambda i=index, value=enabled: self.toggle_rule(i, value)).pack(side="right")
            tk.Label(card, text=rule.name, background="#ffffff", foreground="#2d435d",
                     font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(11, 6))
            tk.Label(card, text=self._rule_conditions(rule), background="#ffffff", foreground="#77889d",
                     justify="left", anchor="nw", wraplength=275,
                     font=("Microsoft YaHei UI", 8)).pack(fill="x")
            tag = f"{ACTION_TEXT.get(rule.action, rule.action)}  ·  {rule.folder or '原目录'}  ·  " \
                  f"{'自动编号' if rule.conflict == 'number' else '跳过冲突'}"
            tk.Label(card, text=tag, background=palette[index % len(palette)], foreground="#567395",
                     font=("Microsoft YaHei UI", 8), padx=7, pady=4).pack(anchor="w", pady=(12, 10))
            actions = tk.Frame(card, background="#ffffff")
            actions.pack(fill="x")
            ttk.Button(actions, text="编辑", command=lambda i=index: self.edit_rule(i)).pack(side="left")
            ttk.Button(actions, text="↑", width=3, command=lambda i=index: self.move_rule(i, -1)).pack(side="left", padx=(5, 0))
            ttk.Button(actions, text="↓", width=3, command=lambda i=index: self.move_rule(i, 1)).pack(side="left", padx=(3, 0))
            ttk.Button(actions, text="删除", command=lambda i=index: self.delete_rule(i)).pack(side="right")
        if not self.rules:
            tk.Label(self.rule_cards_frame, text="还没有规则。添加一条规则后即可生成整理预览。",
                     background="#ffffff", foreground="#7b899c",
                     font=("Microsoft YaHei UI", 10), pady=50).pack(fill="x")
        self.rule_count_label.configure(text=f"{sum(rule.enabled for rule in self.rules)} 条启用 / {len(self.rules)} 条规则")
        self._render_strategies()

    def _strategy_rule_indices(self, kind):
        groups = {
            "images": {"图片"}, "documents": {"文档"},
            "media": {"视频", "音频", "压缩包"},
        }
        names = groups.get(kind, set())
        return [index for index, rule in enumerate(self.rules)
                if rule.folder in names or any(name in rule.name for name in names)]

    def _render_strategies(self):
        colors = {"images": "#2f9bd7", "documents": "#f39a3e",
                  "media": "#2599a8", "date": "#738fca"}
        for kind, button in self.strategy_buttons.items():
            if kind == "date":
                relevant = [rule for rule in self.rules if rule.enabled]
                active = bool(relevant) and all(rule.by_month for rule in relevant)
            else:
                indices = self._strategy_rule_indices(kind)
                active = bool(indices) and any(self.rules[index].enabled for index in indices)
            color = colors[kind] if active else "#e6e9ed"
            foreground = "white" if active else "#8996a6"
            button.configure(background=color, activebackground=color,
                             foreground=foreground, activeforeground=foreground)

    def toggle_strategy(self, kind):
        if kind == "date":
            relevant = [rule for rule in self.rules if rule.enabled]
            enabled = not (relevant and all(rule.by_month for rule in relevant))
            for rule in relevant:
                rule.by_month = enabled
        else:
            indices = self._strategy_rule_indices(kind)
            enabled = not (indices and any(self.rules[index].enabled for index in indices))
            for index in indices:
                self.rules[index].enabled = enabled
        self._render_rules()
        self.invalidate()

    def toggle_rule(self, index, value):
        if 0 <= index < len(self.rules):
            self.rules[index].enabled = value.get()
            self._render_strategies()
            self.rule_count_label.configure(text=f"{sum(rule.enabled for rule in self.rules)} 条启用 / {len(self.rules)} 条规则")
            self.invalidate()

    def add_rule(self):
        dialog = RuleDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            self.rules.append(dialog.result)
            self._render_rules()
            self.invalidate()

    def edit_rule(self, index=None):
        if index is None or not 0 <= index < len(self.rules):
            return
        dialog = RuleDialog(self, self.rules[index])
        self.wait_window(dialog)
        if dialog.result:
            self.rules[index] = dialog.result
            self._render_rules()
            self.invalidate()

    def delete_rule(self, index=None):
        if index is not None and 0 <= index < len(self.rules) and messagebox.askyesno(
                "删除规则", f"删除规则「{self.rules[index].name}」？", parent=self):
            self.rules.pop(index)
            self._render_rules()
            self.invalidate()

    def move_rule(self, index, offset):
        target = index + offset
        if index is not None and 0 <= target < len(self.rules):
            self.rules[index], self.rules[target] = self.rules[target], self.rules[index]
            self._render_rules()
            self.invalidate()

    def refresh_history(self):
        self.batch_tree.delete(*self.batch_tree.get_children())
        batches = self.store.batches()
        for batch in batches:
            self.batch_tree.insert("", "end", iid=batch["id"], values=(batch["created"], batch["total"],
                batch["success"] or 0, batch["failed"] or 0, batch["undone"] or 0))
        self.operation_tree.delete(*self.operation_tree.get_children())
        self.undo_latest_button.configure(state="normal" if batches and not self.busy else "disabled")

    def start_undo_latest(self):
        items = self.batch_tree.get_children()
        if not items:
            messagebox.showinfo("没有记录", "当前没有可撤销的整理记录。", parent=self)
            return
        self.batch_tree.selection_set(items[0])
        self.start_undo()

    def show_operations(self):
        selected = self.batch_tree.selection()
        if not selected:
            return
        self.operation_tree.delete(*self.operation_tree.get_children())
        for row in self.store.operations(selected[0]):
            reason = row["undo_reason"] or row["reason"]
            self.operation_tree.insert("", "end", values=(row["source"], row["destination"],
                ACTION_TEXT.get(row["action"], row["action"]), STATUS_TEXT.get(row["status"], row["status"]),
                STATUS_TEXT.get(row["undo_status"], row["undo_status"] or "—"), reason))

    def start_undo(self):
        selected = self.batch_tree.selection()
        if not selected:
            messagebox.showinfo("未选择记录", "请选择一个执行批次。", parent=self)
            return
        batch = selected[0]
        rows = self.store.operations(batch)
        count = sum(r["status"] == "success" and r["action"] != "copy" and
                    r["undo_status"] not in {"success", "running", "uncertain"} for r in rows)
        if not count:
            messagebox.showinfo("无法撤销", "此批次没有可撤销的移动或重命名操作。复制不会自动撤销。", parent=self)
            return
        if not messagebox.askyesno("确认撤销", f"将尝试恢复 {count} 个文件；遇到冲突会跳过。是否继续？",
                                   icon="warning", parent=self):
            return
        self._start_worker("正在撤销…", lambda: self.executor.undo(batch, self.cancel_event,
                           self._progress), lambda _result: self._undo_done(batch))

    def _undo_done(self, batch):
        self.status_var.set("撤销结束，请查看逐文件结果。")
        self.refresh_history()
        if self.batch_tree.exists(batch):
            self.batch_tree.selection_set(batch)
            self.show_operations()

    def close(self):
        if self.busy:
            if not messagebox.askyesno("任务正在运行", "关闭窗口会请求停止后续操作。是否关闭？", parent=self):
                return
            self.cancel_event.set()
        try:
            self._save_settings()
        except OSError as exc:
            if not messagebox.askyesno("设置保存失败", f"{exc}\n仍要关闭吗？", parent=self):
                return
        self.destroy()


def run():
    app = FileNestApp()
    app.mainloop()

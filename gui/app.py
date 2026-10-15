"""主視窗（Tkinter + ttk）。

版面：上方檔案清單／中左設定分頁（輸出、切分、封面）／中右章節預覽樹／下方進度與日誌。
流程：先「分析章節」看預覽、必要時手動調整，再「轉換輸出」。
所有耗時工作都丟給 Worker 在背景執行緒跑，Tk 物件只在主執行緒碰。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont
from urllib.parse import quote

from PIL import Image, ImageTk

from core import editing
from core.api import (COVER_MODES, FORMAT_LABELS, CoverStyle, FontNotFoundError, SplitOptions,
                      TxtOptions, is_epub)
from core.converter import MODES, Converter
from core.cover import generate_cover_image, palette_names, script_for_language, thumbnail
from core.splitter import validate_options
from core.txt_writer import ENCODINGS, safe_filename
from . import settings
from .dialogs import SplitDialog
from .jobs import JobConfig, batch_job, export_job, load_book
from .widgets import ScrollFrame
from .worker import Worker

try:                                    # 拖放為選配：沒裝就只能用按鈕選檔
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:                       # pragma: no cover
    HAS_DND = False

APP_TITLE = "簡繁轉換與章節整理工具"
SUPPORTED = (".txt", ".text", ".epub")
THUMB = (200, 300)
PALETTE_AUTO = "自動（依書名）"
SPLIT_MODES = {"auto": "自動偵測章節", "fixed": "固定字數切分", "none": "不切分（整本一章）"}


def _rev(d: dict) -> dict:
    return {v: k for k, v in d.items()}


def open_folder(path: str) -> None:
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(folder)                              # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception:
        pass


class App:
    def __init__(self):
        self.root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
        self.root.title(APP_TITLE)
        self.settings = settings.load()
        sh = self.root.winfo_screenheight()
        self.root.geometry(self.settings.get("geometry") or f"1200x{min(800, max(600, sh - 90))}")
        self.root.minsize(1000, 680)

        # ---- 狀態 ----
        self.files: list[str] = []
        self.current_idx = -1
        self.src_cache: dict = {}
        self.raw_book = None                  # 原文字形；章節可被使用者編輯
        self.undo_stack: list = []
        self.script_hint = ""
        self.cover_image_path = ""
        self.cover_font_path = self.settings.get("font_path", "")
        self._cover_photo = None
        self._cover_after = None
        self._convs: dict[str, Converter] = {}
        self._info_text = ""

        self.worker = Worker(self.root, self._on_progress, self._log, self._on_done,
                             self._on_error, self._on_cancelled)

        self._init_style()
        self._make_vars()
        self._build()
        self._wire_traces()
        self._on_format()
        self._log("就緒。請選取或拖放 TXT / EPUB 檔案。" if HAS_DND else
                  "就緒。請按「選取檔案」。（安裝 tkinterdnd2 即可支援拖放）")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Control-z>", lambda e: self.undo())
        threading.Thread(target=self._warm_up, daemon=True).start()
        self._schedule_cover_preview()

    # ------------------------------------------------------------ 樣式 ----
    def _init_style(self):
        fams = set(tkfont.families(self.root))
        for fam in ("Microsoft JhengHei UI", "Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC",
                    "Noto Sans TC", "WenQuanYi Micro Hei"):
            if fam in fams:
                for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
                    tkfont.nametofont(name).configure(family=fam)
                break
        tkfont.nametofont("TkDefaultFont").configure(size=10)
        tkfont.nametofont("TkTextFont").configure(size=10)
        st = ttk.Style(self.root)
        if sys.platform.startswith("linux") and "clam" in st.theme_names():
            st.theme_use("clam")
        st.configure("Treeview", rowheight=26)
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        st.configure("Primary.TButton", font=bold, padding=(14, 6))
        st.configure("Warn.TLabel", foreground="#b45309")

    def _warm_up(self):
        """背景預先載入 OpenCC 字典，第一次轉換時才不會卡住畫面。"""
        try:
            Converter(self._mode_key())
        except Exception:
            pass

    # ------------------------------------------------------------ 變數 ----
    def _make_vars(self):
        S = self.settings.get
        self.v_title = tk.StringVar()
        self.v_author = tk.StringVar()
        mode = S("mode", "s2twp")
        self.v_mode = tk.StringVar(value=MODES.get(mode, MODES["s2twp"]))
        self.v_fmt = tk.StringVar(value=S("fmt", "epub") if S("fmt", "epub") in FORMAT_LABELS else "epub")
        self.v_enc = tk.StringVar(value=ENCODINGS.get(S("enc", "utf-8"), "UTF-8"))
        self.v_indent = tk.BooleanVar(value=S("indent", True))
        self.v_blank = tk.BooleanVar(value=S("blank", True))
        self.v_crlf = tk.BooleanVar(value=S("crlf", False))
        self.v_vertical = tk.BooleanVar(value=S("vertical", False))

        self.v_split_mode = tk.StringVar(value=S("split_mode", "auto"))
        self.v_default_rules = tk.BooleanVar(value=S("default_rules", True))
        self.v_numeric = tk.BooleanVar(value=S("numeric", False))
        self.v_prefix_num = tk.BooleanVar(value=S("prefix_num", False))
        self.v_fixed = tk.StringVar(value=str(S("fixed_size", 5000)))
        self.v_fallback = tk.BooleanVar(value=S("fallback", True))
        self.v_resplit = tk.BooleanVar(value=S("resplit", False))

        self.v_cover_mode = tk.StringVar(value=COVER_MODES.get(S("cover_mode", "fallback"), COVER_MODES["fallback"]))
        pal = S("palette", "auto")
        self.v_palette = tk.StringVar(value=pal if pal in palette_names() else PALETTE_AUTO)
        self.v_show_author = tk.BooleanVar(value=S("show_author", True))
        self.v_frame = tk.BooleanVar(value=S("frame", True))

        self.v_status = tk.StringVar(value="")
        self.v_info = tk.StringVar(value="尚未選取檔案")
        self.v_summary = tk.StringVar(value="")

    def _wire_traces(self):
        self.v_mode.trace_add("write", lambda *_: self._on_mode_change())
        self.v_fmt.trace_add("write", lambda *_: self._on_format())
        for v in (self.v_split_mode, self.v_default_rules, self.v_numeric, self.v_prefix_num,
                  self.v_fixed, self.v_fallback, self.v_resplit):
            v.trace_add("write", lambda *_: self._mark_dirty())
        for v in (self.v_cover_mode, self.v_palette, self.v_show_author, self.v_frame,
                  self.v_title, self.v_author):
            v.trace_add("write", lambda *_: self._schedule_cover_preview())

    # ------------------------------------------------------------ 版面 ----
    def _build(self):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        self._build_top(root)
        pw = ttk.PanedWindow(root, orient="horizontal")
        pw.grid(row=1, column=0, sticky="nsew", padx=8)
        left = ttk.Frame(pw)
        right = ttk.Frame(pw)
        pw.add(left, weight=0)
        pw.add(right, weight=1)
        self._build_left(left)
        self._build_tree(right)
        self._build_bottom(root)
        if HAS_DND:
            root.drop_target_register(DND_FILES)
            root.dnd_bind("<<Drop>>", lambda e: self.add_files(list(root.tk.splitlist(e.data))))

    def _build_top(self, parent):
        f = ttk.LabelFrame(parent, text="檔案（TXT / EPUB，可多選或拖放）", padding=6)
        f.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        f.columnconfigure(0, weight=1)
        self.lb_files = tk.Listbox(f, height=3, activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(f, command=self.lb_files.yview)
        self.lb_files.configure(yscrollcommand=sb.set)
        self.lb_files.grid(row=0, column=0, sticky="ew")
        sb.grid(row=0, column=1, sticky="ns")
        self.lb_files.bind("<<ListboxSelect>>", self._on_file_select)
        btns = ttk.Frame(f)
        btns.grid(row=0, column=2, rowspan=2, padx=(8, 0), sticky="n")
        ttk.Button(btns, text="選取檔案…", command=self.pick_files).pack(fill="x")
        ttk.Button(btns, text="移除選取", command=self.remove_file).pack(fill="x", pady=2)
        ttk.Button(btns, text="清空", command=self.clear_files).pack(fill="x")
        ttk.Label(f, textvariable=self.v_info, foreground="#4b5563").grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _build_left(self, parent):
        nb = ttk.Notebook(parent, width=390)
        nb.pack(fill="both", expand=True, padx=(0, 6), pady=2)
        self.nb = nb
        self._build_tab_output(nb)
        self._build_tab_split(nb)
        self._build_tab_cover(nb)

    def _build_tab_output(self, nb):
        sf = ScrollFrame(nb)
        nb.add(sf, text="輸出")
        f = sf.inner
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="書名").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_title).grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(f, text="作者").grid(row=1, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_author).grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Label(f, text="輸出文字").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(f, textvariable=self.v_mode, values=list(MODES.values()), state="readonly"
                     ).grid(row=2, column=1, sticky="ew", pady=(10, 0))

        fm = ttk.LabelFrame(f, text="輸出格式", padding=6)
        fm.grid(row=3, column=0, columnspan=2, sticky="ew", pady=10)
        for key, label in FORMAT_LABELS.items():
            ttk.Radiobutton(fm, text=label, value=key, variable=self.v_fmt).pack(anchor="w")

        self.lf_txt = ttk.LabelFrame(f, text="TXT 選項", padding=6)
        self.lf_txt.grid(row=4, column=0, columnspan=2, sticky="ew")
        self.lf_txt.columnconfigure(1, weight=1)
        ttk.Label(self.lf_txt, text="編碼").grid(row=0, column=0, sticky="w")
        ttk.Combobox(self.lf_txt, textvariable=self.v_enc, values=list(ENCODINGS.values()),
                     state="readonly").grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Checkbutton(self.lf_txt, text="段首縮排兩格（全形空白）", variable=self.v_indent
                        ).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(self.lf_txt, text="段落之間空一行", variable=self.v_blank
                        ).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(self.lf_txt, text="Windows 換行（CRLF）", variable=self.v_crlf
                        ).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(self.lf_txt, text="目標編碼沒有的字會變成「?」（如簡體字存成 Big5）。",
                  foreground="#6b7280", wraplength=330).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.lf_epub = ttk.LabelFrame(f, text="EPUB 選項", padding=6)
        self.lf_epub.grid(row=4, column=0, columnspan=2, sticky="ew")
        ttk.Checkbutton(self.lf_epub, text="直排（由右至左翻頁）", variable=self.v_vertical).pack(anchor="w")
        ttk.Label(self.lf_epub, text="封面請到「封面」分頁設定。", foreground="#6b7280").pack(anchor="w")

    def _build_tab_split(self, nb):
        sf = ScrollFrame(nb)
        nb.add(sf, text="切分")
        f = sf.inner
        f.columnconfigure(0, weight=1)
        fm = ttk.LabelFrame(f, text="章節切分方式", padding=6)
        fm.grid(row=0, column=0, sticky="ew")
        for key, label in SPLIT_MODES.items():
            ttk.Radiobutton(fm, text=label, value=key, variable=self.v_split_mode).pack(anchor="w")

        fr = ttk.LabelFrame(f, text="自動偵測規則", padding=6)
        fr.grid(row=1, column=0, sticky="ew", pady=6)
        ttk.Checkbutton(fr, text="預設規則（第X章/回/節/卷、Chapter N、序章、番外、後記…）",
                        variable=self.v_default_rules).pack(anchor="w")
        ttk.Checkbutton(fr, text="純數字標題（「1. 標題」「001 標題」，易誤判）",
                        variable=self.v_numeric).pack(anchor="w")

        fp = ttk.LabelFrame(f, text="自訂句首（一行一個，行首相符就當章節；簡繁通用）", padding=6)
        fp.grid(row=2, column=0, sticky="ew")
        self.t_prefix = tk.Text(fp, height=4, width=36)
        self.t_prefix.pack(fill="x")
        self.t_prefix.insert("1.0", self.settings.get("prefixes", ""))
        self.t_prefix.bind("<KeyRelease>", lambda e: self._mark_dirty())
        ttk.Checkbutton(fp, text="句首後面要接數字（如「卷3」）", variable=self.v_prefix_num).pack(anchor="w")

        fx = ttk.LabelFrame(f, text="自訂 regex（進階，一行一個）", padding=6)
        fx.grid(row=3, column=0, sticky="ew", pady=6)
        self.t_regex = tk.Text(fx, height=2, width=36)
        self.t_regex.pack(fill="x")
        self.t_regex.insert("1.0", self.settings.get("regexes", ""))
        self.t_regex.bind("<KeyRelease>", lambda e: self._mark_dirty())

        ff = ttk.LabelFrame(f, text="固定字數", padding=6)
        ff.grid(row=4, column=0, sticky="ew")
        row = ttk.Frame(ff)
        row.pack(fill="x")
        ttk.Label(row, text="每段約").pack(side="left")
        ttk.Spinbox(row, from_=500, to=200000, increment=500, width=8, textvariable=self.v_fixed).pack(side="left", padx=4)
        ttk.Label(row, text="字").pack(side="left")
        ttk.Checkbutton(ff, text="自動偵測找不到章節時，改用固定字數", variable=self.v_fallback).pack(anchor="w")

        fe = ttk.LabelFrame(f, text="EPUB 輸入", padding=6)
        fe.grid(row=5, column=0, sticky="ew", pady=6)
        ttk.Checkbutton(fe, text="重新切分（預設沿用原書章節）", variable=self.v_resplit).pack(anchor="w")
        ttk.Label(fe, text="重新切分會捨棄原書章節內的圖片與版面。", foreground="#6b7280").pack(anchor="w")
        ttk.Label(f, text="修改切分設定後，請按下方「分析章節」重新預覽。", style="Warn.TLabel",
                  wraplength=340).grid(row=6, column=0, sticky="w")

    def _build_tab_cover(self, nb):
        sf = ScrollFrame(nb)
        nb.add(sf, text="封面")
        f = sf.inner
        f.columnconfigure(0, weight=1)
        ttk.Combobox(f, textvariable=self.v_cover_mode, values=list(COVER_MODES.values()),
                     state="readonly").grid(row=0, column=0, columnspan=2, sticky="ew")
        box = tk.Frame(f, width=THUMB[0] + 4, height=THUMB[1] + 4, bg="#d1d5db")
        box.grid(row=1, column=0, columnspan=2, pady=8)
        box.pack_propagate(False)
        self.lbl_cover = tk.Label(box, bg="#e5e7eb", wraplength=THUMB[0] - 12, justify="center", fg="#4b5563")
        self.lbl_cover.pack(fill="both", expand=True, padx=2, pady=2)
        self.v_cover_info = tk.StringVar()
        ttk.Label(f, textvariable=self.v_cover_info, foreground="#6b7280").grid(row=2, column=0, columnspan=2)

        ttk.Button(f, text="選擇圖片…", command=self.pick_cover_image).grid(row=3, column=0, sticky="ew", pady=(8, 2), padx=(0, 3))
        ttk.Button(f, text="用書名開瀏覽器搜圖", command=self.search_cover_web).grid(row=3, column=1, sticky="ew", pady=(8, 2), padx=(3, 0))
        self.v_cover_warn = tk.StringVar()
        ttk.Label(f, textvariable=self.v_cover_warn, style="Warn.TLabel", wraplength=340).grid(row=4, column=0, columnspan=2, sticky="w")

        fa = ttk.LabelFrame(f, text="自動產生封面", padding=6)
        fa.grid(row=5, column=0, columnspan=2, sticky="ew", pady=8)
        fa.columnconfigure(1, weight=1)
        ttk.Label(fa, text="配色").grid(row=0, column=0, sticky="w")
        ttk.Combobox(fa, textvariable=self.v_palette, values=[PALETTE_AUTO] + palette_names(),
                     state="readonly").grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Checkbutton(fa, text="顯示作者", variable=self.v_show_author).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(fa, text="外框", variable=self.v_frame).grid(row=1, column=1, sticky="w")
        ttk.Button(fa, text="選擇字型…", command=self.pick_font).grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.v_font = tk.StringVar()
        ttk.Label(fa, textvariable=self.v_font, foreground="#6b7280", wraplength=230).grid(row=2, column=1, sticky="w", padx=6, pady=(4, 0))
        self._refresh_font_label()

    def _build_tree(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        bar = ttk.Frame(parent)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=2)
        for text, cmd in (("合併", self.cmd_merge), ("拆分…", self.cmd_split), ("改名", self.cmd_rename),
                          ("刪除", self.cmd_delete), ("切換輸出", self.cmd_toggle),
                          ("全選", lambda: self.cmd_all(False)), ("全不選", lambda: self.cmd_all(True)),
                          ("復原", self.undo)):
            ttk.Button(bar, text=text, width=7, command=cmd).pack(side="left", padx=(0, 3))
        cols = ("use", "title", "chars")
        self.tree = ttk.Treeview(parent, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("use", text="輸出")
        self.tree.heading("title", text="章節標題")
        self.tree.heading("chars", text="字數")
        self.tree.column("use", width=50, anchor="center", stretch=False)
        self.tree.column("title", width=380, anchor="w")
        self.tree.column("chars", width=80, anchor="e", stretch=False)
        self.tree.tag_configure("skip", foreground="#9ca3af")
        sb = ttk.Scrollbar(parent, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double)
        self.tree.bind("<Delete>", lambda e: self.cmd_delete())
        ttk.Label(parent, textvariable=self.v_summary, foreground="#4b5563").grid(row=2, column=0, columnspan=2, sticky="w", pady=2)

    def _build_bottom(self, parent):
        f = ttk.Frame(parent, padding=(8, 4, 8, 8))
        f.grid(row=2, column=0, sticky="ew")
        f.columnconfigure(0, weight=1)
        row = ttk.Frame(f)
        row.grid(row=0, column=0, sticky="ew")
        self.btn_analyze = ttk.Button(row, text="分析章節", command=lambda: self.analyze(False))
        self.btn_export = ttk.Button(row, text="轉換輸出", style="Primary.TButton", command=self.export)
        self.btn_batch = ttk.Button(row, text="批次轉換全部檔案", command=self.batch)
        self.btn_cancel = ttk.Button(row, text="取消", command=self.worker.cancel, state="disabled")
        self.btn_analyze.pack(side="left")
        self.btn_export.pack(side="left", padx=6)
        self.btn_batch.pack(side="left")
        self.btn_cancel.pack(side="right")
        self._busy_widgets = [self.btn_analyze, self.btn_export, self.btn_batch]
        self.pbar = ttk.Progressbar(f, maximum=100)
        self.pbar.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(f, textvariable=self.v_status, foreground="#4b5563").grid(row=2, column=0, sticky="w")
        lf = ttk.Frame(f)
        lf.grid(row=3, column=0, sticky="ew")
        lf.columnconfigure(0, weight=1)
        self.txt_log = tk.Text(lf, height=5, state="disabled", wrap="word", bg="#f9fafb")
        sb = ttk.Scrollbar(lf, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.grid(row=0, column=0, sticky="ew")
        sb.grid(row=0, column=1, sticky="ns")

    # ------------------------------------------------------------ 小工具 ----
    def _log(self, msg: str):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", time.strftime("%H:%M:%S ") + msg + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _mode_key(self) -> str:
        return _rev(MODES).get(self.v_mode.get(), "s2twp")

    def _conv(self) -> Converter:
        k = self._mode_key()
        if k not in self._convs:
            self.root.configure(cursor="watch")
            self.root.update_idletasks()
            try:
                self._convs[k] = Converter(k)
            finally:
                self.root.configure(cursor="")
        return self._convs[k]

    def _lines(self, widget) -> list[str]:
        return [ln.strip() for ln in widget.get("1.0", "end").splitlines() if ln.strip()]

    def _fixed_size(self) -> int:
        try:
            return max(500, min(1_000_000, int(self.v_fixed.get())))
        except ValueError:
            return 5000

    def _split_options(self) -> SplitOptions:
        return SplitOptions(
            mode=self.v_split_mode.get(), use_default_rules=self.v_default_rules.get(),
            numeric_titles=self.v_numeric.get(), prefixes=self._lines(self.t_prefix),
            prefix_with_number=self.v_prefix_num.get(), regexes=self._lines(self.t_regex),
            fixed_size=self._fixed_size(), fallback_to_fixed=self.v_fallback.get())

    def _cover_mode_key(self) -> str:
        return _rev(COVER_MODES).get(self.v_cover_mode.get(), "fallback")

    def _cover_style(self) -> CoverStyle:
        pal = self.v_palette.get()
        return CoverStyle(palette="auto" if pal == PALETTE_AUTO else pal, show_author=self.v_show_author.get(),
                          frame=self.v_frame.get(), font_path=self.cover_font_path)

    def _make_config(self) -> JobConfig:
        opts = self._split_options()
        validate_options(opts)                                   # 錯誤的 regex 在這裡就擋下來
        cm = self._cover_mode_key()
        if self.v_fmt.get() == "epub" and cm == "image" and not self.cover_image_path:
            raise ValueError("封面模式是「使用我選的圖片」，但還沒選擇圖片。")
        txt = TxtOptions(encoding=_rev(ENCODINGS).get(self.v_enc.get(), "utf-8"),
                         blank_line_between_paragraphs=self.v_blank.get(), indent=self.v_indent.get(),
                         newline="\r\n" if self.v_crlf.get() else "\n")
        return JobConfig(mode=self._mode_key(), fmt=self.v_fmt.get(), split=opts, txt=txt,
                         vertical=self.v_vertical.get(), resplit_epub=self.v_resplit.get(),
                         cover_mode=cm, cover_image=self.cover_image_path, cover_style=self._cover_style(),
                         script_hint=self.script_hint)

    def _set_busy(self, busy: bool):
        for w in self._busy_widgets:
            w.configure(state="disabled" if busy else "normal")
        self.btn_cancel.configure(state="normal" if busy else "disabled")

    def _run(self, fn, tag):
        self._set_busy(True)
        self.pbar["value"] = 0
        self.worker.start(fn, tag=tag)

    def _mark_dirty(self):
        if self.raw_book is not None and not self.worker.busy:
            self.btn_analyze.configure(text="重新分析章節 ●")

    def _on_format(self):
        if self.v_fmt.get() == "epub":
            self.lf_txt.grid_remove()
            self.lf_epub.grid()
        else:
            self.lf_epub.grid_remove()
            self.lf_txt.grid()
        self._update_cover_warning()

    def _on_mode_change(self):
        self.refresh_tree()
        self._update_cover_warning()
        self._schedule_cover_preview()

    # ------------------------------------------------------------ 檔案 ----
    def pick_files(self):
        paths = filedialog.askopenfilenames(
            title="選取 TXT 或 EPUB", initialdir=self.settings.get("last_in_dir") or None,
            filetypes=[("文字或電子書", "*.txt *.epub"), ("TXT", "*.txt"), ("EPUB", "*.epub"), ("所有檔案", "*.*")])
        if paths:
            self.settings["last_in_dir"] = os.path.dirname(paths[0])
            self.add_files(list(paths))

    def add_files(self, paths: list[str]):
        if self.worker.busy:
            messagebox.showinfo(APP_TITLE, "目前有工作進行中，請稍後再加入檔案。")
            return
        good = []
        for p in paths:
            if os.path.isdir(p):
                good += [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.lower().endswith(SUPPORTED)]
            elif p.lower().endswith(SUPPORTED):
                good.append(p)
            else:
                self._log(f"略過不支援的檔案：{os.path.basename(p)}")
        new = [p for p in good if p not in self.files]
        if not new:
            return
        first = len(self.files)
        for p in new:
            self.files.append(p)
            self.lb_files.insert("end", os.path.basename(p))
        if self.current_idx < 0:
            self._select_file(first)

    def _select_file(self, idx: int):
        self.lb_files.selection_clear(0, "end")
        self.lb_files.selection_set(idx)
        self._load_selected()

    def _on_file_select(self, _e=None):
        sel = self.lb_files.curselection()
        if not sel or sel[0] == self.current_idx:
            return
        if self.worker.busy:
            self.lb_files.selection_clear(0, "end")
            if self.current_idx >= 0:
                self.lb_files.selection_set(self.current_idx)
            return
        self._load_selected()

    def _load_selected(self):
        sel = self.lb_files.curselection()
        if not sel:
            return
        self.current_idx = sel[0]
        self.src_cache = {}
        self.cover_image_path = ""
        self.analyze(new_file=True)

    def remove_file(self):
        sel = self.lb_files.curselection()
        if not sel or self.worker.busy:
            return
        i = sel[0]
        self.lb_files.delete(i)
        del self.files[i]
        if i == self.current_idx:
            self._reset_book()
            if self.files:
                self._select_file(min(i, len(self.files) - 1))
        elif i < self.current_idx:
            self.current_idx -= 1

    def clear_files(self):
        if self.worker.busy:
            return
        self.files.clear()
        self.lb_files.delete(0, "end")
        self._reset_book()

    def _reset_book(self):
        self.current_idx = -1
        self.raw_book = None
        self.undo_stack.clear()
        self.v_info.set("尚未選取檔案")
        self.refresh_tree()
        self._schedule_cover_preview()

    # ------------------------------------------------------------ 分析 ----
    def analyze(self, new_file: bool = False):
        if self.worker.busy:
            return
        if self.current_idx < 0 or self.current_idx >= len(self.files):
            messagebox.showinfo(APP_TITLE, "請先選取檔案。")
            return
        path = self.files[self.current_idx]
        try:
            cfg = self._make_config()
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        title = "" if new_file else self.v_title.get().strip()
        author = "" if new_file else self.v_author.get().strip()
        cache = self.src_cache
        self._log(f"分析：{os.path.basename(path)}")
        self._run(lambda ctx: load_book(path, cfg, title, author, cache, ctx), tag=("analyze", new_file))

    def _apply_analysis(self, result, new_file: bool):
        book, info = result
        self.raw_book = book
        self.undo_stack.clear()
        self.script_hint = info.get("hint", "")
        if new_file:
            self.v_title.set(book.title)
            self.v_author.set(book.author)
        sc = info.get("script") or {}
        parts = [info.get("type", "")]
        if info.get("encoding"):
            parts.append("編碼 " + info["encoding"])
        if sc.get("label"):
            parts.append("原文：" + sc["label"])
        self._info_text = "｜".join(parts)
        self.v_info.set(self._info_text)
        self.btn_analyze.configure(text="分析章節")
        n = len(book.chapters)
        self._log(f"偵測到 {n} 個章節。")
        for w in book.warnings[:10]:
            self._log("⚠ " + w)
        if len(book.warnings) > 10:
            self._log(f"⚠ 另有 {len(book.warnings) - 10} 則警告未顯示")
        self.refresh_tree()
        self._update_cover_warning()
        self._schedule_cover_preview()

    # ------------------------------------------------------------ 預覽樹 ----
    def _display_titles(self, titles: list[str]) -> list[str]:
        try:
            conv = self._conv()
        except Exception:
            return titles
        if conv.mode == "keep" or not titles:
            return titles
        out = conv.convert("\n".join(titles)).split("\n")
        return out if len(out) == len(titles) else titles

    def refresh_tree(self, select=None):
        keep = [int(i) for i in self.tree.selection()] if select == "keep" else []
        self.tree.delete(*self.tree.get_children())
        book = self.raw_book
        if book is None:
            self.v_summary.set("")
            return
        chs = book.chapters
        for i, (c, t) in enumerate(zip(chs, self._display_titles([c.title for c in chs]))):
            self.tree.insert("", "end", iid=str(i), tags=("skip",) if c.skip else (),
                             values=("☐" if c.skip else "☑", t, f"{c.char_count:,}"))
        want = keep if select == "keep" else ([select] if isinstance(select, int) else [])
        want = [i for i in want if 0 <= i < len(chs)]
        if want:
            self.tree.selection_set([str(i) for i in want])
            self.tree.see(str(want[0]))
        used = [c for c in chs if not c.skip]
        self.v_summary.set(f"共 {len(chs)} 章，輸出 {len(used)} 章，約 {sum(c.char_count for c in used):,} 字"
                           + (f"　⚠ {len(book.warnings)} 則章號提醒" if book.warnings else ""))

    def _sel(self) -> list[int]:
        return sorted(int(i) for i in self.tree.selection())

    def _edit(self, func, *args, keep=False):
        if self.worker.busy or self.raw_book is None:
            return
        try:
            new, sel = func(self.raw_book.chapters, *args)
        except ValueError as e:
            messagebox.showwarning(APP_TITLE, str(e))
            return
        self.undo_stack.append(self.raw_book.chapters)
        self.raw_book.chapters = new
        self.refresh_tree("keep" if keep else sel)
        self._schedule_cover_preview()

    def undo(self):
        if self.worker.busy or not self.undo_stack or self.raw_book is None:
            return
        self.raw_book.chapters = self.undo_stack.pop()
        self.refresh_tree()

    def cmd_merge(self):
        self._edit(editing.merge, self._sel())

    def cmd_delete(self):
        sel = self._sel()
        if len(sel) > 20 and not messagebox.askyesno(APP_TITLE, f"確定刪除選取的 {len(sel)} 章？（可用「復原」還原）"):
            return
        self._edit(editing.delete, sel)

    def cmd_toggle(self):
        self._edit(editing.set_skip, self._sel(), None, keep=True)

    def cmd_all(self, skip: bool):
        if self.raw_book is not None:
            self._edit(editing.set_skip, range(len(self.raw_book.chapters)), skip)

    def cmd_rename(self, index: int | None = None):
        sel = self._sel()
        if index is None:
            if len(sel) != 1:
                messagebox.showinfo(APP_TITLE, "請先選取一個章節。")
                return
            index = sel[0]
        cur = self.tree.set(str(index), "title")
        name = simpledialog.askstring(APP_TITLE, "章節標題：", initialvalue=cur, parent=self.root)
        if name is not None:
            self._edit(editing.rename, index, name)

    def cmd_split(self):
        sel = self._sel()
        if len(sel) != 1:
            messagebox.showinfo(APP_TITLE, "請先選取一個章節。")
            return
        ch = self.raw_book.chapters[sel[0]]
        if len(ch.paragraphs) < 2:
            messagebox.showinfo(APP_TITLE, "這一章段落太少，無法拆分。")
            return
        dlg = SplitDialog(self.root, self.tree.set(str(sel[0]), "title"), ch.paragraphs, ch.html is not None)
        if dlg.result:
            k, title = dlg.result
            self._edit(editing.split_at, sel[0], k, title)

    def _on_tree_click(self, ev):
        if self.worker.busy:
            return
        if self.tree.identify("region", ev.x, ev.y) == "cell" and self.tree.identify_column(ev.x) == "#1":
            iid = self.tree.identify_row(ev.y)
            if iid:
                self._edit(editing.set_skip, [int(iid)], None, keep=True)
                return "break"

    def _on_tree_double(self, ev):
        if self.tree.identify("region", ev.x, ev.y) == "cell" and self.tree.identify_column(ev.x) == "#2":
            iid = self.tree.identify_row(ev.y)
            if iid:
                self.cmd_rename(int(iid))

    # ------------------------------------------------------------ 封面 ----
    def pick_cover_image(self):
        p = filedialog.askopenfilename(title="選擇封面圖片", filetypes=[("圖片", "*.jpg *.jpeg *.png *.webp *.bmp *.gif"), ("所有檔案", "*.*")])
        if p:
            self.cover_image_path = p
            self.v_cover_mode.set(COVER_MODES["image"])
            self._schedule_cover_preview()

    def pick_font(self):
        p = filedialog.askopenfilename(title="選擇字型檔", filetypes=[("字型", "*.ttf *.otf *.ttc *.otc"), ("所有檔案", "*.*")])
        if p:
            self.cover_font_path = p
            self._refresh_font_label()
            self._schedule_cover_preview()

    def _refresh_font_label(self):
        self.v_font.set(os.path.basename(self.cover_font_path) if self.cover_font_path else "（自動尋找系統字型）")

    def search_cover_web(self):
        title = self.v_title.get().strip()
        if not title:
            messagebox.showinfo(APP_TITLE, "請先填寫書名。")
            return
        q = quote(f"{title} {self.v_author.get().strip()} 封面".strip())
        webbrowser.open(f"https://www.google.com/search?tbm=isch&q={q}")
        self._log("已開啟瀏覽器。挑好圖片存檔後，按「選擇圖片…」匯入（也可從原書網站下載）。")

    def _update_cover_warning(self):
        msg = ""
        b = self.raw_book
        if self.v_fmt.get() == "epub" and b is not None and b.cover and self._mode_key() != "keep" \
                and self._cover_mode_key() in ("fallback", "keep"):
            msg = "原封面是圖片，圖上的文字不會跟著簡繁轉換；若封面有字，建議改用「自動產生」。"
        self.v_cover_warn.set(msg)

    def _schedule_cover_preview(self):
        if self._cover_after is not None:
            self.root.after_cancel(self._cover_after)
        self._cover_after = self.root.after(350, self._update_cover_preview)
        self._update_cover_warning()

    def _show_cover(self, img, msg: str = "", info: str = ""):
        if img is not None:
            self._cover_photo = ImageTk.PhotoImage(img)
            self.lbl_cover.configure(image=self._cover_photo, text="")
        else:
            self._cover_photo = None
            self.lbl_cover.configure(image="", text=msg)
        self.v_cover_info.set(info)

    def _update_cover_preview(self):
        self._cover_after = None
        mode = self._cover_mode_key()
        book = self.raw_book
        try:
            if mode == "none":
                return self._show_cover(None, "（不使用封面）")
            if mode == "image":
                if not self.cover_image_path:
                    return self._show_cover(None, "（尚未選擇圖片）\n按「選擇圖片…」")
                im = Image.open(self.cover_image_path)
                size = im.size
                im.thumbnail(THUMB)
                return self._show_cover(im.convert("RGB"), info=f"{size[0]}×{size[1]}（輸出時縮至長邊 2400 以內）")
            has = bool(book and book.cover)
            if mode == "keep" and not has:
                return self._show_cover(None, "（原書沒有封面）")
            if has and mode in ("keep", "fallback"):
                return self._show_cover(thumbnail(book.cover, THUMB), info="沿用原書封面")
            conv = self._conv()
            title = conv.convert(self.v_title.get().strip() or "未命名")
            author = conv.convert(self.v_author.get().strip())
            lang = conv.language or ("zh-CN" if self.script_hint == "sc" else "")
            img = generate_cover_image(title, author, script_for_language(lang), self._cover_style())
            img.thumbnail(THUMB)
            self._show_cover(img, info="自動產生")
        except FontNotFoundError as e:
            self._show_cover(None, str(e))
        except Exception as e:
            self._show_cover(None, f"（無法預覽：{e}）")

    # ------------------------------------------------------------ 輸出 ----
    def export(self):
        if self.worker.busy:
            return
        book = self.raw_book
        if book is None:
            messagebox.showinfo(APP_TITLE, "請先選取檔案並分析章節。")
            return
        if not any(not c.skip for c in book.chapters):
            messagebox.showwarning(APP_TITLE, "沒有勾選任何要輸出的章節。")
            return
        try:
            cfg = self._make_config()
            shown = self._conv().convert(self.v_title.get().strip()) or "output"
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        outdir = self.settings.get("last_out_dir") or None
        base = safe_filename(shown)
        if cfg.fmt == "txt_multi":
            d = filedialog.askdirectory(title="選擇輸出資料夾（會在裡面建立以書名命名的資料夾）", initialdir=outdir)
            if not d:
                return
            out = os.path.join(d, base)
            if os.path.isdir(out) and os.listdir(out) and not messagebox.askyesno(APP_TITLE, f"「{base}」資料夾已有檔案，同名檔案會被覆蓋，繼續嗎？"):
                return
            self.settings["last_out_dir"] = d
        else:
            ext = ".epub" if cfg.fmt == "epub" else ".txt"
            out = filedialog.asksaveasfilename(title="輸出檔案", defaultextension=ext, initialfile=base + ext,
                                               initialdir=outdir, filetypes=[(ext.upper()[1:], "*" + ext)])
            if not out:
                return
            self.settings["last_out_dir"] = os.path.dirname(out)
        title, author = self.v_title.get().strip(), self.v_author.get().strip()
        self._log(f"輸出 → {out}")
        self._run(lambda ctx: export_job(book, cfg, out, ctx, title, author), tag=("export", out))

    def batch(self):
        if self.worker.busy:
            return
        if not self.files:
            messagebox.showinfo(APP_TITLE, "請先加入檔案。")
            return
        try:
            cfg = self._make_config()
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        if not messagebox.askyesno(APP_TITLE, f"將依目前設定轉換全部 {len(self.files)} 個檔案（章節自動切分，不套用手動編輯）。\n繼續嗎？"):
            return
        d = filedialog.askdirectory(title="選擇輸出資料夾", initialdir=self.settings.get("last_out_dir") or None)
        if not d:
            return
        self.settings["last_out_dir"] = d
        files = list(self.files)
        cfg.script_hint = ""                       # 批次時各檔自己判斷
        self._log(f"批次轉換 {len(files)} 個檔案 → {d}")
        self._run(lambda ctx: batch_job(files, d, cfg, ctx), tag=("batch", d))

    # ------------------------------------------------------------ Worker 回呼 ----
    def _on_progress(self, frac: float, text: str):
        self.pbar["value"] = frac * 100
        self.v_status.set(text)

    def _on_done(self, result, tag):
        self._set_busy(False)
        kind = tag[0]
        if kind == "analyze":
            self.v_status.set("")
            self._apply_analysis(result, tag[1])
        elif kind == "export":
            self.v_status.set("完成")
            n = f"（{len(result)} 個檔案）" if isinstance(result, list) else ""
            self._log(f"完成：{tag[1]} {n}")
            if messagebox.askyesno(APP_TITLE, f"轉換完成！\n{tag[1]}\n\n要開啟所在資料夾嗎？"):
                open_folder(tag[1])
        elif kind == "batch":
            ok, failed = result
            self.v_status.set("批次完成")
            self._log(f"批次完成：成功 {len(ok)}，失敗 {len(failed)}")
            for p, err in failed:
                self._log(f"  失敗 {os.path.basename(p)}：{err}")
            if messagebox.askyesno(APP_TITLE, f"批次完成：成功 {len(ok)}，失敗 {len(failed)}。\n\n要開啟輸出資料夾嗎？"):
                open_folder(tag[1])

    def _on_error(self, exc: Exception, tb: str, tag):
        self._set_busy(False)
        self.v_status.set("發生錯誤")
        self._log(f"錯誤：{exc}")
        self._log(tb.strip().splitlines()[-1] if tb else "")
        if isinstance(exc, FontNotFoundError):
            msg = f"{exc}\n\n請到「封面」分頁按「選擇字型…」，或改用其他封面模式。"
        elif isinstance(exc, ImportError):
            msg = f"缺少必要套件：{exc}\n請執行 pip install -r requirements.txt"
        else:
            msg = f"{type(exc).__name__}：{exc}"
        messagebox.showerror(APP_TITLE, msg)

    def _on_cancelled(self, tag):
        self._set_busy(False)
        self.v_status.set("已取消")
        self._log("已取消。")

    # ------------------------------------------------------------ 結束 ----
    def _collect_settings(self) -> dict:
        s = dict(self.settings)
        pal = self.v_palette.get()
        s.update(
            mode=self._mode_key(), fmt=self.v_fmt.get(), enc=_rev(ENCODINGS).get(self.v_enc.get(), "utf-8"),
            indent=self.v_indent.get(), blank=self.v_blank.get(), crlf=self.v_crlf.get(),
            vertical=self.v_vertical.get(), split_mode=self.v_split_mode.get(),
            default_rules=self.v_default_rules.get(), numeric=self.v_numeric.get(),
            prefix_num=self.v_prefix_num.get(), fixed_size=self._fixed_size(), fallback=self.v_fallback.get(),
            resplit=self.v_resplit.get(), prefixes="\n".join(self._lines(self.t_prefix)),
            regexes="\n".join(self._lines(self.t_regex)), cover_mode=self._cover_mode_key(),
            palette="auto" if pal == PALETTE_AUTO else pal, show_author=self.v_show_author.get(),
            frame=self.v_frame.get(), font_path=self.cover_font_path, geometry=self.root.geometry())
        return s

    def _on_close(self):
        if self.worker.busy:
            if not messagebox.askyesno(APP_TITLE, "還有工作進行中，確定要結束嗎？"):
                return
            self.worker.cancel()
        settings.save(self._collect_settings())
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    missing = []
    for mod, pkg in (("opencc", "opencc-python-reimplemented"), ("bs4", "beautifulsoup4"), ("PIL", "Pillow")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(APP_TITLE, "缺少必要套件：" + "、".join(missing) + "\n請執行：pip install -r requirements.txt")
        return
    App().run()

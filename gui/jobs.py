"""GUI 背景工作的實際內容（不碰任何 Tk 物件，所以能在背景執行緒執行，也能直接測試）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Optional

from core.api import (CoverStyle, OutputOptions, SplitOptions, TxtOptions, analyze_script,
                      build_book_from_txt, convert_book, cover_warning, default_output_name,
                      export_book, is_epub, load_epub_book, load_txt_source, resplit_book,
                      set_cover)
from core.converter import Converter, script_stats
from core.models import Book
from core.txt_writer import safe_filename


class Cancelled(Exception):
    pass


class NullCtx:
    """沒有 GUI 時（測試、命令列）使用的空 ctx。"""
    def progress(self, frac: float, text: str = ""): pass
    def log(self, msg: str): pass
    def check(self): pass


@dataclass
class JobConfig:
    mode: str = "s2twp"                 # 簡繁轉換模式
    fmt: str = "epub"                   # txt / txt_multi / epub
    split: SplitOptions = field(default_factory=SplitOptions)
    txt: TxtOptions = field(default_factory=TxtOptions)
    vertical: bool = False
    resplit_epub: bool = False          # EPUB 輸入時重新切分（預設沿用原書章節）
    cover_mode: str = "fallback"
    cover_image: str = ""
    cover_style: CoverStyle = field(default_factory=CoverStyle)
    script_hint: str = ""               # "sc" / "tc"：不轉換時，用來決定 EPUB 語言與封面字型


def _stage(ctx, lo: float, hi: float, label: str):
    def cb(i, total):
        ctx.check()
        ctx.progress(lo + (hi - lo) * i / max(total, 1), label)
    return cb


def script_hint_from_stats(stats: Optional[dict]) -> str:
    label = (stats or {}).get("label", "")
    return "sc" if label == "簡體為主" else "tc" if label == "繁體為主" else ""


def load_book(path: str, cfg: JobConfig, title: str = "", author: str = "",
              cache: Optional[dict] = None, ctx=None):
    """讀檔並切分。回傳 (Book, info)；info 含 type / encoding / script(dict) / hint。"""
    ctx = ctx or NullCtx()
    ctx.progress(0.05, "讀取檔案…")
    info: dict = {}
    if is_epub(path):
        book = load_epub_book(path)
        info["type"] = "EPUB"
        sample = []
        for ch in book.chapters:
            if not ch.skip:
                sample.extend(ch.paragraphs[:200])
            if sum(map(len, sample)) > 30000:
                break
        try:
            info["script"] = script_stats("\n".join(sample))
        except ImportError:
            info["script"] = None
        ctx.check()
        if cfg.resplit_epub:
            ctx.progress(0.5, "重新切分章節…")
            book = resplit_book(book, cfg.split)
        if title:
            book.title = title
        if author:
            book.author = author
    else:
        src = (cache or {}).get(path) or load_txt_source(path)
        if cache is not None:
            cache[path] = src
        info.update(type="TXT", encoding=src.encoding)
        try:
            info["script"] = analyze_script(src)
        except ImportError:
            info["script"] = None
        ctx.check()
        ctx.progress(0.5, "偵測章節…")
        book = build_book_from_txt(src, cfg.split, title, author)
    info["hint"] = script_hint_from_stats(info.get("script"))
    ctx.progress(1.0, "分析完成")
    return book, info


def export_job(book: Book, cfg: JobConfig, out_path: str, ctx=None,
               title: str = "", author: str = ""):
    """轉換並輸出。book 為原文字形、可能經過使用者編輯的章節；不會被修改。"""
    ctx = ctx or NullCtx()
    b = replace(book, title=title or book.title, author=author or book.author)
    conv = Converter(cfg.mode)
    epub = cfg.fmt == "epub"
    new = convert_book(b, conv, _stage(ctx, 0.0, 0.6, "簡繁轉換中…"),
                       text="auto" if epub else True, html=epub)
    if not new.language and cfg.script_hint:
        new.language = "zh-CN" if cfg.script_hint == "sc" else "zh-TW"
    if epub:
        ctx.progress(0.62, "處理封面…")
        w = cover_warning(new, cfg.mode, cfg.cover_mode)
        if w:
            ctx.log("提醒：" + w)
        set_cover(new, cfg.cover_mode, cfg.cover_image, cfg.cover_style)
    ctx.check()
    out = OutputOptions(cfg.fmt, cfg.txt, cfg.vertical)
    result = export_book(new, out_path, out, _stage(ctx, 0.65, 1.0, "寫出檔案…"))
    ctx.progress(1.0, "完成")
    return result


def _unique(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    k = 2
    while os.path.exists(f"{base}_{k}{ext}"):
        k += 1
    return f"{base}_{k}{ext}"


def batch_job(files: list[str], out_dir: str, cfg: JobConfig, ctx=None):
    """批次：每個檔案依目前設定自動切分、轉換、輸出（不做手動編輯）。回傳 (成功清單, 失敗清單)。"""
    ctx = ctx or NullCtx()
    os.makedirs(out_dir, exist_ok=True)
    ok, failed = [], []
    n = len(files)
    for i, path in enumerate(files):
        ctx.check()
        name = os.path.basename(path)
        ctx.log(f"[{i + 1}/{n}] {name}")

        class Sub:                         # 把單檔進度映射到整體進度
            def progress(_, frac, text=""):
                ctx.progress((i + frac) / n, f"[{i + 1}/{n}] {name}  {text}")
            def log(_, msg): ctx.log("    " + msg)
            def check(_): ctx.check()

        try:
            sub = Sub()
            book, _info = load_book(path, replace(cfg, cover_image=cfg.cover_image), ctx=_Scaled(sub, 0, 0.3))
            conv = Converter(cfg.mode)
            base = safe_filename(conv.convert(book.title) or os.path.splitext(name)[0])
            ext = {"txt": ".txt", "txt_multi": "", "epub": ".epub"}[cfg.fmt]
            out = _unique(os.path.join(out_dir, base + ext))
            if cfg.fmt == "txt_multi":
                k, o = 2, out
                while os.path.exists(o):
                    o, k = f"{out}_{k}", k + 1
                out = o
            c2 = replace(cfg, script_hint=_info.get("hint", "") or cfg.script_hint)
            export_job(book, c2, out, _Scaled(sub, 0.3, 1.0))
            ok.append(out)
            ctx.log("    → " + out)
        except Cancelled:
            raise
        except Exception as e:             # 單檔失敗不中斷整批
            failed.append((path, str(e)))
            ctx.log(f"    失敗：{e}")
    ctx.progress(1.0, "批次完成")
    return ok, failed


class _Scaled:
    """把 0~1 的進度壓縮到 [lo, hi] 區間。"""
    def __init__(self, ctx, lo, hi):
        self.ctx, self.lo, self.hi = ctx, lo, hi
    def progress(self, frac, text=""):
        self.ctx.progress(self.lo + (self.hi - self.lo) * frac, text)
    def log(self, msg): self.ctx.log(msg)
    def check(self): self.ctx.check()

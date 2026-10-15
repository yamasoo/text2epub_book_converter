"""TXT 輸出：單一檔案、依章節拆成多檔。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .models import Book, Chapter

ENCODINGS = {
    "utf-8": "UTF-8",
    "utf-8-sig": "UTF-8（含 BOM）",
    "big5hkscs": "Big5",
    "gb18030": "GB18030（簡體 GBK）",
    "utf-16": "UTF-16",
}


@dataclass
class TxtOptions:
    encoding: str = "utf-8"
    blank_line_between_paragraphs: bool = True
    indent: bool = True            # 段首縮排兩個全形空白
    include_header: bool = True    # 單檔最前面放書名與作者
    newline: str = "\n"            # Windows 記事本舊版可選 "\r\n"
    filename_pattern: str = "{index:04d}_{title}"   # 多檔輸出用


_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str, maxlen: int = 80) -> str:
    name = _BAD.sub("_", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
    return (name[:maxlen].rstrip(" .") or "untitled")


def _render_chapter(ch: Chapter, o: TxtOptions) -> str:
    sep = "\n\n" if o.blank_line_between_paragraphs else "\n"
    pre = "\u3000\u3000" if o.indent else ""
    body = sep.join(pre + p for p in ch.paragraphs)
    return ch.title + ("\n\n" + body if body else "")


def _encode(text: str, o: TxtOptions) -> bytes:
    if o.newline != "\n":
        text = text.replace("\n", o.newline)
    return text.encode(o.encoding, errors="replace")   # 目標編碼沒有的字會變 ?


def active_chapters(book: Book) -> list[Chapter]:
    return [c for c in book.chapters if not c.skip]


def write_single_txt(book: Book, path: str, o: TxtOptions | None = None,
                     progress: Optional[Callable[[int, int], None]] = None) -> str:
    o = o or TxtOptions()
    chapters = active_chapters(book)
    parts = []
    if o.include_header and book.title:
        head = book.title
        if book.author:
            head += "\n作者：" + book.author
        parts.append(head)
    for i, ch in enumerate(chapters, 1):
        parts.append(_render_chapter(ch, o))
        if progress:
            progress(i, len(chapters))
    Path(path).write_bytes(_encode("\n\n\n".join(parts) + "\n", o))
    return path


def write_multi_txt(book: Book, out_dir: str, o: TxtOptions | None = None,
                    progress: Optional[Callable[[int, int], None]] = None) -> list[str]:
    o = o or TxtOptions()
    chapters = active_chapters(book)
    os.makedirs(out_dir, exist_ok=True)
    written = []
    used = set()
    for i, ch in enumerate(chapters, 1):
        base = safe_filename(o.filename_pattern.format(index=i, title=ch.title))
        name, k = base, 2
        while name.lower() in used:
            name = f"{base}_{k}"
            k += 1
        used.add(name.lower())
        p = os.path.join(out_dir, name + ".txt")
        Path(p).write_bytes(_encode(_render_chapter(ch, o) + "\n", o))
        written.append(p)
        if progress:
            progress(i, len(chapters))
    return written

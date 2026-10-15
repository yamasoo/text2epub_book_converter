"""對外的高階介面：GUI 與命令列都只呼叫這裡。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

from .converter import Converter, script_stats
from .cover import CoverStyle, FontNotFoundError, generate_cover, prepare_cover_image, script_for_language
from .epub_reader import read_epub
from .epub_writer import write_epub
from .htmlutil import convert_html
from .models import Book, Chapter
from .reader import TxtSource, load_txt_source
from .splitter import SplitOptions, check_sequence, split_text, validate_options
from .txt_writer import TxtOptions, safe_filename, write_multi_txt, write_single_txt

Progress = Optional[Callable[[int, int], None]]

__all__ = [
    "load_txt_source", "build_book_from_txt", "load_epub_book", "resplit_book",
    "convert_book", "export_book", "OutputOptions", "SplitOptions", "TxtOptions",
    "is_epub", "analyze_script", "default_output_name",
    "set_cover", "cover_warning", "CoverStyle", "FontNotFoundError",
]


def is_epub(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".epub"


# ---------- 輸入 ----------

def build_book_from_txt(src: TxtSource, opts: SplitOptions | None = None,
                        title: str = "", author: str = "") -> Book:
    """依切分設定把 TXT 變成 Book（原文字形，尚未轉換）。"""
    opts = opts or SplitOptions()
    validate_options(opts)
    title = title or src.title_guess
    chapters = split_text(src.text, title, opts)
    book = Book(
        title=title,
        author=author or src.author_guess,
        chapters=chapters,
        source_path=src.path,
        source_encoding=src.encoding,
    )
    book.warnings = check_sequence(chapters) if opts.mode == "auto" else []
    return book


def load_epub_book(path: str) -> Book:
    """EPUB 預設沿用原書章節。"""
    return read_epub(path)


def resplit_book(book: Book, opts: SplitOptions) -> Book:
    """EPUB 的「重新切分」選項：把正文攤平後依規則重切。
    注意：重切後章節改由純文字產生，原書章節內的圖片與版面會捨棄。"""
    validate_options(opts)
    lines: list[str] = []
    for ch in book.chapters:
        if ch.skip:
            continue
        lines.append(ch.title)         # 把原標題放回去，讓規則有機會偵測到
        lines.extend(ch.paragraphs)
    chapters = split_text("\n".join(lines), book.title, opts)
    new = Book(title=book.title, author=book.author, chapters=chapters, cover=book.cover,
               language=book.language, source_path=book.source_path)
    new.warnings = check_sequence(chapters) if opts.mode == "auto" else []
    return new


# ---------- 轉換 ----------

def convert_book(book: Book, converter: Converter, progress: Progress = None,
                 text: bool | str = True, html: bool = True) -> Book:
    """回傳轉換後的新 Book，原 Book 不變。
    text：True 轉換所有 paragraphs；"auto" 只轉沒有 HTML 的章節（輸出 EPUB 用）；False 不轉。
    html：轉換 EPUB 章節 HTML 的文字節點（輸出 TXT 時傳 False）。
    被標為略過的章節只轉標題，內容原樣保留（反正不會輸出）。
    沒轉換的那份內容保持原樣，不應拿去輸出。"""
    new = Book(
        title=converter.convert(book.title),
        author=converter.convert(book.author),
        cover=book.cover,
        language=converter.language or book.language,
        source_path=book.source_path,
        source_encoding=book.source_encoding,
        warnings=list(book.warnings),
        resources=book.resources,
        css=book.css,
    )
    total = len(book.chapters)
    for i, ch in enumerate(book.chapters, 1):
        paras, h = list(ch.paragraphs), ch.html
        if not ch.skip:
            if text is True or (text == "auto" and ch.html is None):
                body = converter.convert(ch.text)
                paras = body.split("\n") if body else []
            if html and ch.html is not None:
                h = convert_html(ch.html, converter)
        new.chapters.append(Chapter(title=converter.convert(ch.title), paragraphs=paras, html=h, skip=ch.skip))
        if progress:
            progress(i, total)
    return new


def analyze_script(src: TxtSource) -> dict:
    return script_stats(src.text)


# ---------- 封面 ----------

COVER_MODES = {
    "fallback": "沿用原封面，沒有就自動產生",
    "keep": "沿用原封面（沒有就不放）",
    "image": "使用我選的圖片",
    "auto": "自動產生（書名＋背景）",
    "none": "不使用封面",
}


def set_cover(book: Book, mode: str = "fallback", image_path: str = "",
              style: CoverStyle | None = None) -> Optional[bytes]:
    """設定 book.cover 並回傳。請在 convert_book 之後呼叫（用轉換後的書名、作者與字形）。
    自動產生可能丟 FontNotFoundError，GUI 應提示使用者選字型。"""
    if mode == "none":
        book.cover = None
    elif mode == "image":
        if not image_path:
            raise ValueError("請選擇封面圖片")
        book.cover = prepare_cover_image(image_path)
    elif mode == "auto" or (mode == "fallback" and not book.cover):
        book.cover = generate_cover(book.title, book.author, script_for_language(book.language), style)
    elif mode == "keep" or mode == "fallback":
        if book.cover:                       # 原圖統一縮小成 JPEG，避免 EPUB 過大
            book.cover = prepare_cover_image(book.cover)
    else:
        raise ValueError(f"未知的封面模式：{mode}")
    return book.cover


def cover_warning(book: Book, converter_mode: str, cover_mode: str) -> str:
    """原封面是圖片，圖上的字無法跟著簡繁轉換，沿用時提醒使用者。"""
    if book.cover and converter_mode != "keep" and cover_mode in ("fallback", "keep"):
        return "沿用的原封面是圖片，圖上的文字不會跟著簡繁轉換；若封面有字，建議改用「自動產生」。"
    return ""


# ---------- 輸出 ----------

@dataclass
class OutputOptions:
    format: str = "txt"                 # txt / txt_multi / epub
    txt: TxtOptions = field(default_factory=TxtOptions)
    vertical: bool = False              # EPUB 直排


FORMAT_LABELS = {"txt": "單一 TXT", "txt_multi": "依章節拆成多個 TXT", "epub": "EPUB"}


def default_output_name(book: Book, fmt: str) -> str:
    base = safe_filename(book.title or "output")
    return {"txt": base + ".txt", "txt_multi": base, "epub": base + ".epub"}[fmt]


def export_book(book: Book, out_path: str, opts: OutputOptions, progress: Progress = None):
    """out_path：txt / epub 為檔案路徑，txt_multi 為資料夾路徑。回傳寫出的路徑（多檔回傳 list）。"""
    if opts.format == "txt":
        return write_single_txt(book, out_path, opts.txt, progress)
    if opts.format == "txt_multi":
        return write_multi_txt(book, out_path, opts.txt, progress)
    if opts.format == "epub":
        return write_epub(book, out_path, opts.vertical, progress)
    raise ValueError(f"未知的輸出格式：{opts.format}")

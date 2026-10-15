"""統一的中間資料結構：TXT 與 EPUB 讀進來後都轉成 Book。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Chapter:
    title: str
    paragraphs: list[str] = field(default_factory=list)
    html: Optional[str] = None   # EPUB 輸入時保留原 HTML（第 2 步使用）
    skip: bool = False           # 輸出時略過（封面、版權頁等）

    @property
    def text(self) -> str:
        return "\n".join(self.paragraphs)

    @property
    def char_count(self) -> int:
        return sum(len(p) for p in self.paragraphs)


@dataclass
class Book:
    title: str = ""
    author: str = ""
    chapters: list[Chapter] = field(default_factory=list)
    cover: Optional[bytes] = None
    language: str = ""            # 例如 zh-TW / zh-CN，空字串代表未指定
    source_path: str = ""
    source_encoding: str = ""
    warnings: list[str] = field(default_factory=list)
    # EPUB 輸入時保留：圖片等資源 {檔名: (media_type, bytes)}，以及合併後的 CSS
    resources: dict[str, tuple[str, bytes]] = field(default_factory=dict)
    css: str = ""

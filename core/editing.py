"""章節編輯：合併、拆分、改名、刪除、切換略過。

全部是純函式：傳入章節列表，回傳 (新列表, 建議選取的索引)。不修改傳入的列表與章節，
所以 GUI 只要把舊列表存起來就能做「復原」。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Optional

from .models import Chapter


def merge(chapters: list[Chapter], indices: Iterable[int]):
    idx = sorted(set(indices))
    if not idx:
        raise ValueError("請先選取章節")
    if len(idx) == 1:
        if idx[0] + 1 >= len(chapters):
            raise ValueError("最後一章沒有下一章可以合併")
        idx.append(idx[0] + 1)
    if idx != list(range(idx[0], idx[-1] + 1)):
        raise ValueError("只能合併相連的章節")
    group = [chapters[i] for i in idx]

    title, paras = group[0].title, list(group[0].paragraphs)
    for c in group[1:]:
        if not paras:                      # 前面只有標題（如「第一卷」）：標題接起來，內容用後面的
            title = f"{title} {c.title}".strip()
            paras = list(c.paragraphs)
        else:                              # 後面章節的標題保留成一行，內容不遺失
            if c.title.strip():
                paras.append(c.title.strip())
            paras.extend(c.paragraphs)
    html = "".join(c.html for c in group) if all(c.html is not None for c in group) else None
    merged = Chapter(title, paras, html, all(c.skip for c in group))
    return chapters[:idx[0]] + [merged] + chapters[idx[-1] + 1:], idx[0]


def split_at(chapters: list[Chapter], index: int, para_index: int, new_title: str = ""):
    """從第 para_index 段（0 起算）開始切成新章節。
    注意：EPUB 原書章節拆分後改由純文字產生，該章內的圖片與版面會捨棄。"""
    ch = chapters[index]
    if not 0 < para_index < len(ch.paragraphs):
        raise ValueError("拆分位置必須在第 2 段之後、最後一段以內")
    first = Chapter(ch.title, ch.paragraphs[:para_index], None, ch.skip)
    second = Chapter(new_title.strip() or f"{ch.title}（續）", ch.paragraphs[para_index:], None, ch.skip)
    return chapters[:index] + [first, second] + chapters[index + 1:], index + 1


def delete(chapters: list[Chapter], indices: Iterable[int]):
    drop = set(indices)
    if not drop:
        raise ValueError("請先選取章節")
    new = [c for i, c in enumerate(chapters) if i not in drop]
    return new, None


def rename(chapters: list[Chapter], index: int, title: str):
    title = title.strip()
    if not title:
        raise ValueError("標題不能是空的")
    new = list(chapters)
    new[index] = replace(chapters[index], title=title)
    return new, index


def set_skip(chapters: list[Chapter], indices: Iterable[int], value: Optional[bool] = None):
    """value=None 時各章各自反轉。"""
    sel = set(indices)
    if not sel:
        raise ValueError("請先選取章節")
    new = [replace(c, skip=(not c.skip if value is None else value)) if i in sel else c
           for i, c in enumerate(chapters)]
    return new, None

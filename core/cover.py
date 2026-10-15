"""封面：尋找中文字型、自動產生封面、處理使用者自選圖片。

封面來源優先序（由 api.set_cover 處理）：EPUB 內建 -> 使用者圖片 -> 自動產生（保底）
"""
from __future__ import annotations

import glob
import io
import os
import re
import sys
import zlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

COVER_W, COVER_H = 1600, 2400        # 2:3
MAX_EDGE = 2400                       # 使用者圖片的長邊上限
JPEG_QUALITY = 90


class FontNotFoundError(RuntimeError):
    pass


# ---------------------------------------------------------------- 字型 ----

_ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")


def _bundle_dirs() -> list[str]:
    dirs = [_ASSET_DIR]
    if getattr(sys, "_MEIPASS", None):                       # PyInstaller
        dirs.insert(0, os.path.join(sys._MEIPASS, "assets", "fonts"))
    return dirs


# 依序嘗試；每項為檔名或萬用字元。bold 優先給書名用。
_SYSTEM_FONTS = {
    "tc": [
        r"C:\Windows\Fonts\msjhbd.ttc", r"C:\Windows\Fonts\msjh.ttc",
        "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/**/NotoSansCJK*.ttc", "/usr/share/fonts/**/NotoSerifCJK*.ttc",
        "/usr/share/fonts/**/wqy-*.tt*", r"C:\Windows\Fonts\mingliu.ttc",
    ],
    "sc": [
        r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/**/NotoSansCJK*.ttc", "/usr/share/fonts/**/NotoSerifCJK*.ttc",
        "/usr/share/fonts/**/wqy-*.tt*", r"C:\Windows\Fonts\simsun.ttc",
    ],
}
_FONT_EXT = (".ttf", ".otf", ".ttc", ".otc")


def script_for_language(lang: str) -> str:
    """book.language -> 'sc' / 'tc'（簡體 zh-CN 用簡體字型，其餘用繁體字型）"""
    return "sc" if (lang or "").lower() in ("zh-cn", "zh-hans", "zh-sg") else "tc"


def _ttc_index(path: str, script: str) -> int:
    """TTC 內含多地區字形時，挑名稱帶 TC / SC 的那一個；其餘字型用 0。"""
    if not path.lower().endswith((".ttc", ".otc")):
        return 0
    want = "SC" if script == "sc" else "TC"
    for i in range(0, 16):
        try:
            fam = ImageFont.truetype(path, 20, index=i).getname()[0]
        except Exception:
            break
        if fam.split()[-1] == want and "Mono" not in fam:
            return i
    return 0


def _expand(pattern: str) -> list[str]:
    if any(c in pattern for c in "*?["):
        return sorted(glob.glob(pattern, recursive=True))
    return [pattern] if os.path.exists(pattern) else []


def _bundled_fonts() -> list[str]:
    out = []
    for d in _bundle_dirs():
        if os.path.isdir(d):
            out += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(_FONT_EXT)]
    return out


@lru_cache(maxsize=None)
def _notdef_mask(path: str, index: int) -> bytes:
    f = ImageFont.truetype(path, 48, index=index)
    return bytes(f.getmask("\uffff"))


def font_supports(path: str, index: int, text: str) -> bool:
    """字型能不能真的畫出這些字（避免畫成方塊）。"""
    try:
        f = ImageFont.truetype(path, 48, index=index)
        nd = _notdef_mask(path, index)
        for ch in {c for c in text if not c.isspace()}:
            if bytes(f.getmask(ch)) == nd:
                return False
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class FontSpec:
    path: str
    index: int = 0

    def load(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.path, size, index=self.index)


def find_font(script: str = "tc", user_font: str = "", sample_text: str = "") -> FontSpec:
    """依序：使用者指定 -> 程式內附 -> 系統字型。找不到丟 FontNotFoundError。"""
    candidates: list[str] = []
    if user_font:
        candidates.append(user_font)
    candidates += _bundled_fonts()
    for pat in _SYSTEM_FONTS.get(script, _SYSTEM_FONTS["tc"]):
        candidates += _expand(pat)
    seen = set()
    for p in candidates:
        if p in seen or not os.path.isfile(p):
            continue
        seen.add(p)
        idx = _ttc_index(p, script)
        if font_supports(p, idx, sample_text or "書名作者章"):
            return FontSpec(p, idx)
        if p == user_font:
            raise FontNotFoundError(f"你選的字型無法顯示中文：{p}")
    raise FontNotFoundError(
        "找不到可用的中文字型。請在封面設定選擇一個字型檔（.ttf/.otf/.ttc），"
        "或把字型放進 assets/fonts 資料夾。")


# ---------------------------------------------------------------- 配色 ----

# 名稱: (上緣色, 下緣色)
PALETTES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "深藍": ((28, 44, 84), (12, 20, 46)),
    "墨綠": ((30, 72, 60), (12, 36, 30)),
    "酒紅": ((110, 28, 40), (52, 12, 22)),
    "米黃": ((240, 228, 200), (214, 196, 160)),
    "灰黑": ((70, 72, 78), (22, 23, 27)),
    "紫藤": ((84, 58, 116), (38, 24, 62)),
    "靛青": ((38, 92, 108), (16, 46, 60)),
    "暖橘": ((214, 122, 64), (150, 66, 34)),
}


def palette_names() -> list[str]:
    return list(PALETTES)


def _luminance(c) -> float:
    r, g, b = (x / 255 for x in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ---------------------------------------------------------------- 排版 ----

_NO_START = set("，。、；：！？）」』】》〉,.;:!?)]}…—～·’”")     # 避頭點
_NO_END = set("（「『【《〈([{‘“")                               # 避尾點
_TOKEN = re.compile(r"[A-Za-z0-9_'’\-.]+|\s+|.", re.S)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _wrap(tokens: list[str], font, maxw: float) -> list[str]:
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        if tok.isspace():
            if cur:
                cur += " "
            continue
        if cur and font.getlength(cur + tok) > maxw:
            # 避頭點：行首不能是標點，把前一個字帶下來
            if tok[0] in _NO_START and len(cur) > 1:
                lines.append(cur[:-1].rstrip())
                cur = cur[-1] + tok
                continue
            # 避尾點：行尾不能是開括號，帶到下一行
            if cur[-1] in _NO_END and len(cur) > 1:
                lines.append(cur[:-1].rstrip())
                cur = cur[-1] + tok
                continue
            lines.append(cur.rstrip())
            cur = tok
        else:
            cur += tok
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def _balanced(tokens, font, maxw: float) -> list[str]:
    """行數不變的前提下盡量讓各行等長，避免最後一行只剩一個字。"""
    base = _wrap(tokens, font, maxw)
    n = len(base)
    if n <= 1:
        return base
    lo = max(font.getlength(t) for t in tokens if not t.isspace())
    hi = maxw
    best = base
    for _ in range(14):
        mid = (lo + hi) / 2
        cand = _wrap(tokens, font, mid)
        if len(cand) <= n:
            best, hi = cand, mid
        else:
            lo = mid
    return best


def _fit_title(title: str, spec: FontSpec, maxw: int, maxh: int,
               max_size: int = 300, min_size: int = 80, spacing: float = 1.3):
    tokens = _tokens(title)
    size = max_size
    while size >= min_size:
        font = spec.load(size)
        lines = _balanced(tokens, font, maxw)
        if len(lines) * size * spacing <= maxh and all(font.getlength(l) <= maxw * 1.001 for l in lines):
            return font, size, lines
        size -= 10
    font = spec.load(min_size)                     # 太長：硬截斷加刪節號
    lines = _balanced(tokens, font, maxw)
    max_lines = max(1, int(maxh // (min_size * spacing)))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return font, min_size, lines


# ---------------------------------------------------------------- 產生 ----

@dataclass
class CoverStyle:
    palette: str = "auto"        # "auto"：依書名固定挑一組（同一本書每次相同）；或 PALETTES 的名稱
    show_author: bool = True
    frame: bool = True           # 外框裝飾
    font_path: str = ""          # 使用者自選字型


def pick_palette(title: str, name: str = "auto") -> str:
    if name in PALETTES:
        return name
    names = list(PALETTES)
    return names[zlib.crc32(title.encode("utf-8")) % len(names)]


def _gradient(top, bottom) -> Image.Image:
    img = Image.new("RGB", (COVER_W, COVER_H))
    d = ImageDraw.Draw(img)
    for y in range(COVER_H):
        d.line([(0, y), (COVER_W, y)], fill=_mix(top, bottom, y / (COVER_H - 1)))
    return img


def generate_cover_image(title: str, author: str = "", script: str = "tc",
                         style: CoverStyle | None = None) -> Image.Image:
    style = style or CoverStyle()
    title = (title or "未命名").strip()
    author = (author or "").strip() if style.show_author else ""
    spec = find_font(script, style.font_path, title + author)

    top, bottom = PALETTES[pick_palette(title, style.palette)]
    img = _gradient(top, bottom)
    draw = ImageDraw.Draw(img)
    avg = _mix(top, bottom, 0.5)
    dark_bg = _luminance(avg) < 0.55
    fg = (248, 246, 240) if dark_bg else (34, 30, 26)
    accent = _mix(avg, fg, 0.55)

    if style.frame:
        m = 90
        draw.rectangle([m, m, COVER_W - m, COVER_H - m], outline=accent, width=6)
        draw.rectangle([m + 24, m + 24, COVER_W - m - 24, COVER_H - m - 24], outline=accent, width=2)

    area_top, area_bottom = 420, 1560
    font, size, lines = _fit_title(title, spec, COVER_W - 2 * 230, area_bottom - area_top)
    line_h = size * 1.3
    y = (area_top + area_bottom) / 2 - line_h * len(lines) / 2 + line_h / 2
    for ln in lines:
        draw.text((COVER_W / 2, y), ln, font=font, fill=fg, anchor="mm")
        y += line_h

    if author:
        draw.line([(COVER_W / 2 - 110, 1700), (COVER_W / 2 + 110, 1700)], fill=accent, width=4)
        asize = 96
        afont = spec.load(asize)
        while afont.getlength(author) > COVER_W - 2 * 260 and asize > 48:
            asize -= 6
            afont = spec.load(asize)
        draw.text((COVER_W / 2, 1830), author, font=afont, fill=fg, anchor="mm")
    return img


def to_jpeg(img: Image.Image, quality: int = JPEG_QUALITY) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def generate_cover(title: str, author: str = "", script: str = "tc",
                   style: CoverStyle | None = None) -> bytes:
    return to_jpeg(generate_cover_image(title, author, script, style))


# ---------------------------------------------------------- 使用者圖片 ----

def prepare_cover_image(source, max_edge: int = MAX_EDGE, quality: int = JPEG_QUALITY) -> bytes:
    """source 可為檔案路徑或 bytes。轉成 RGB JPEG、長邊縮到 max_edge 以內。"""
    img = Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_edge:
        r = max_edge / max(w, h)
        img = img.resize((max(1, round(w * r)), max(1, round(h * r))), Image.LANCZOS)
    return to_jpeg(img, quality)


def thumbnail(data: bytes, box: tuple[int, int] = (200, 300)) -> Image.Image:
    """給 GUI 預覽用（Tkinter 以 ImageTk.PhotoImage 包起來）。"""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail(box, Image.LANCZOS)
    return img


def cover_size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size

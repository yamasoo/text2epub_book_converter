"""TXT 讀取：編碼偵測、文字正規化、從檔名猜書名與作者。"""
from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from charset_normalizer import from_bytes as _cn_from_bytes
except ImportError:  # 選配
    _cn_from_bytes = None

_BOMS = [
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),   # 必須先於 UTF-16 LE 檢查
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
]

# 這些名稱統一換成範圍更大的編碼，避免少數字解不出來
_ENC_UPGRADE = {
    "gb2312": "gb18030", "gbk": "gb18030", "gb18030": "gb18030",
    "big5": "big5hkscs", "big5hkscs": "big5hkscs", "cp950": "big5hkscs",
}


def _cjk_score(text: str) -> float:
    """常用漢字／標點／ASCII 的比例，用來在 GBK 與 Big5 之間擇優。"""
    if not text:
        return 0.0
    good = 0
    for ch in text:
        o = ord(ch)
        if (0x4E00 <= o <= 0x9FFF or o < 0x80 or 0x3000 <= o <= 0x303F
                or 0xFF00 <= o <= 0xFFEF or 0x2000 <= o <= 0x206F):
            good += 1
    return good / len(text)


def detect_encoding(data: bytes) -> str:
    for bom, enc in _BOMS:
        if data.startswith(bom):
            return enc
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    if _cn_from_bytes is not None:
        best = _cn_from_bytes(data).best()
        if best is not None and best.encoding:
            enc = _ENC_UPGRADE.get(best.encoding.lower(), best.encoding)
            try:
                data.decode(enc)
                return enc
            except (UnicodeDecodeError, LookupError):
                pass

    # 後備：GBK 與 Big5 都試，挑看起來比較像正常中文的
    candidates = []
    sample = data[:200000]
    for enc in ("gb18030", "big5hkscs"):
        try:
            candidates.append((_cjk_score(sample.decode(enc)), enc))
        except UnicodeDecodeError:
            continue
    if candidates:
        return max(candidates)[1]
    return "gb18030"


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for ch in ("\ufeff", "\u200b", "\u200c", "\u200d", "\x00"):
        text = text.replace(ch, "")
    return text


def read_text_file(path: str | Path) -> tuple[str, str]:
    """回傳 (正規化後文字, 偵測到的編碼)。"""
    data = Path(path).read_bytes()
    enc = detect_encoding(data)
    text = data.decode(enc, errors="replace")
    return normalize_text(text), enc


_NOISE_BRACKET = re.compile(
    r"[\[【(（][^\]】)）]*?(?:下载|下載|txt|TXT|全本|完结|完結|精校|校对|校對|电子书|電子書|合集|整理|版)[^\]】)）]*?[\]】)）]"
)
_AUTHOR_IN_NAME = re.compile(r"作者\s*[：:_]?\s*([^\s_\-《》\[\]【】()（）]+)")
_TXT_NOISE = re.compile(r"(?:txt|TXT)?\s*(?:全集|全本|下载|下載)(?:\s*txt)?", re.I)


def parse_filename(path: str | Path) -> tuple[str, str]:
    """從檔名猜 (書名, 作者)，猜不到作者則回傳空字串。"""
    stem = Path(path).stem
    author = ""
    m = _AUTHOR_IN_NAME.search(stem)
    if m:
        author = m.group(1)
        stem = stem[:m.start()] + stem[m.end():]
    m = re.search(r"《(.+?)》", stem)
    if m:
        title = m.group(1)
    else:
        title = _NOISE_BRACKET.sub("", stem)
        title = _TXT_NOISE.sub("", title)
    title = title.strip(" _-–—·　\t")
    return (title or Path(path).stem), author


def guess_author_from_text(text: str, head: int = 3000) -> str:
    m = re.search(r"(?m)^[\s\u3000]*作者\s*[：:]\s*([^\n]{1,30})$", text[:head])
    return m.group(1).strip() if m else ""


@dataclass
class TxtSource:
    """讀進記憶體的原始 TXT，重複分析章節時不必重讀檔案。"""
    path: str
    text: str
    encoding: str
    title_guess: str
    author_guess: str


def load_txt_source(path: str | Path) -> TxtSource:
    text, enc = read_text_file(path)
    title, author = parse_filename(path)
    if not author:
        author = guess_author_from_text(text)
    return TxtSource(str(path), text, enc, title, author)

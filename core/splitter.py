"""章節切分：預設規則、自訂句首、自訂 regex、固定字數、不切分。

比對時不轉換原文；使用者輸入的句首會同時展開成「原樣／簡體／繁體」三種寫法，
所以簡體書可以輸入繁體句首，反之亦然。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .converter import text_variants
from .models import Chapter

_NUM = r"[0-9０-９零〇○一二三四五六七八九十百千万萬两兩壹贰貳叁參肆伍陆陸柒捌玖拾佰仟]+"
_UNITS = "章回节節卷集部篇话話幕折"
_SPECIAL = (
    "序章|序言|序幕|序|楔子|引子|前言|开篇|開篇|尾声|尾聲|后记|後記|终章|終章|终卷|終卷|"
    "番外|外传|外傳|附录|附錄|完结感言|完結感言|作者的话|作者的話|大结局|大結局"
)

_P_CHAPTER = re.compile(rf"^第\s*{_NUM}\s*[{_UNITS}]")
_P_ENGLISH = re.compile(r"^(?:chapter|chap\.?|ch\.?)\s*(?:\d+|(?-i:[IVXLCDM]+))(?![A-Za-z])", re.I)
_P_SPECIAL = re.compile(rf"^(?:{_SPECIAL})(?=$|[\s：:、.．·\-—–_（(\[【篇0-9０-９一二三四五六七八九十])")
_P_NUMERIC = re.compile(r"^[0-9０-９]{1,5}(?:\s*[、.．:：]\s*|\s+)(?=\S)")

_DEFAULT_PATTERNS = [_P_CHAPTER, _P_ENGLISH, _P_SPECIAL]


@dataclass
class SplitOptions:
    mode: str = "auto"                # auto（規則偵測）/ fixed（固定字數）/ none（不切分）
    use_default_rules: bool = True
    numeric_titles: bool = False      # 「1. 標題」「001 標題」這類純數字標題（容易誤判，預設關）
    prefixes: list[str] = field(default_factory=list)   # 自訂句首
    prefix_with_number: bool = False  # 句首後面必須接數字，例如「卷3」
    regexes: list[str] = field(default_factory=list)    # 自訂 regex
    fixed_size: int = 5000            # 固定字數；auto 找不到章節時也用它保底
    fallback_to_fixed: bool = True
    max_title_len: int = 40
    preface_title: str = "前言"


def validate_options(opts: SplitOptions) -> None:
    """GUI 在按下「分析」前呼叫，有問題丟 ValueError。"""
    for r in opts.regexes:
        if not r.strip():
            continue
        try:
            re.compile(r)
        except re.error as e:
            raise ValueError(f"自訂 regex 有誤：{r}（{e}）") from e


# ---------- 中文數字 ----------

_DIGITS = {
    "零": 0, "〇": 0, "○": 0, "一": 1, "壹": 1, "二": 2, "两": 2, "兩": 2, "贰": 2, "貳": 2,
    "三": 3, "叁": 3, "參": 3, "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陆": 6, "陸": 6,
    "七": 7, "柒": 7, "八": 8, "捌": 8, "九": 9, "玖": 9,
}
_UNIT = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_BIG = {"万": 10000, "萬": 10000}


def parse_number(s: str):
    """把「一百零五」「１２」「105」轉成 int，失敗回 None。"""
    s = unicodedata.normalize("NFKC", s.strip())
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if any(c not in _DIGITS and c not in _UNIT and c not in _BIG for c in s):
        return None
    if not any(c in _UNIT or c in _BIG for c in s):   # 純位置寫法：一二三 -> 123
        return int("".join(str(_DIGITS[c]) for c in s))
    total = section = num = 0
    for c in s:
        if c in _DIGITS:
            num = _DIGITS[c]
        elif c in _UNIT:
            section += (num or 1) * _UNIT[c]
            num = 0
        else:  # 萬
            section += num
            total += (section or 1) * _BIG[c]
            section = num = 0
    return total + section + num


# ---------- 標題比對 ----------

def normalize_line(line: str) -> str:
    s = line.replace("\u3000", " ").replace("\xa0", " ").strip()
    s = re.sub(r"^[#＃*＊\s]+", "", s)
    s = re.sub(r"[*＊\s]+$", "", s)
    return s


def _tail_ok(tail: str) -> bool:
    """「第三章」後面接的若像一整句話，就不是標題。"""
    t = tail.strip()
    if not t:
        return True
    if t[-1] in "。，,；;、" or "。" in t:
        return False
    return not tail.startswith("的")


class TitleMatcher:
    def __init__(self, opts: SplitOptions):
        self.opts = opts
        self._prefixes: list[str] = []
        self._prefix_res: list[re.Pattern] = []
        self._user_res: list[re.Pattern] = []
        for p in opts.prefixes:
            p = p.strip()
            if not p:
                continue
            for v in text_variants(p):
                if opts.prefix_with_number:
                    self._prefix_res.append(re.compile("^" + re.escape(v) + r"\s*" + _NUM))
                else:
                    self._prefixes.append(v)
        for r in opts.regexes:
            if r.strip():
                self._user_res.append(re.compile(r))

    def is_title(self, s: str) -> bool:
        """s 須為 normalize_line 後的非空字串。"""
        if len(s) > self.opts.max_title_len:
            return False
        o = self.opts
        if o.use_default_rules:
            for pat in _DEFAULT_PATTERNS:
                m = pat.match(s)
                if m and _tail_ok(s[m.end():]):
                    return True
        if o.numeric_titles:
            m = _P_NUMERIC.match(s)
            if m and _tail_ok(s[m.end():]):
                return True
        for p in self._prefixes:
            if s.startswith(p):
                return True
        for r in self._prefix_res:
            if r.match(s):
                return True
        for r in self._user_res:
            if r.search(s):
                return True
        return False


# ---------- 切分 ----------

def to_paragraphs(text: str) -> list[str]:
    out = []
    for raw in text.split("\n"):
        s = raw.strip(" \t\u3000\xa0")
        if s:
            out.append(s)
    return out


def _clean_title(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def split_fixed(paras: list[str], size: int, title_fmt: str = "第{n}部分") -> list[Chapter]:
    size = max(int(size), 500)
    chapters: list[Chapter] = []
    buf: list[str] = []
    n = 0
    for p in paras:
        buf.append(p)
        n += len(p)
        if n >= size:
            chapters.append(Chapter(title_fmt.format(n=len(chapters) + 1), buf))
            buf, n = [], 0
    if buf:
        if chapters and n < size * 0.3:
            chapters[-1].paragraphs.extend(buf)   # 尾巴太短就併回上一段
        else:
            chapters.append(Chapter(title_fmt.format(n=len(chapters) + 1), buf))
    return chapters


def _split_by_titles(paras: list[str], matcher: TitleMatcher, opts: SplitOptions) -> list[Chapter]:
    chapters: list[Chapter] = []
    preface: list[str] = []
    cur = None
    for p in paras:
        s = normalize_line(p)
        if s and matcher.is_title(s):
            cur = Chapter(_clean_title(s))
            chapters.append(cur)
        elif cur is None:
            preface.append(p)
        else:
            cur.paragraphs.append(p)
    if chapters and preface:
        pre = Chapter(opts.preface_title, preface)
        if pre.char_count < 60:        # 只有書名／作者之類的幾行，預設略過
            pre.skip = True
        chapters.insert(0, pre)
    return chapters


def split_text(text: str, book_title: str = "", opts: SplitOptions | None = None) -> list[Chapter]:
    opts = opts or SplitOptions()
    paras = to_paragraphs(text)
    if opts.mode == "none":
        return [Chapter(book_title or "全文", paras)]
    if opts.mode == "fixed":
        return split_fixed(paras, opts.fixed_size)

    chapters = _split_by_titles(paras, TitleMatcher(opts), opts)
    if not chapters:
        if opts.fallback_to_fixed and opts.fixed_size > 0:
            return split_fixed(paras, opts.fixed_size)
        return [Chapter(book_title or "全文", paras)]
    return chapters


# ---------- 章號檢查 ----------

_P_SEQ = re.compile(rf"^第\s*({_NUM})\s*[章回话話节節]")


def check_sequence(chapters: list[Chapter], limit: int = 20) -> list[str]:
    """檢查「第N章」是否缺號、重複；遇到第1章視為新的一卷重新計數。"""
    warns: list[str] = []
    prev = None
    for ch in chapters:
        m = _P_SEQ.match(ch.title)
        if not m:
            continue
        n = parse_number(m.group(1))
        if n is None:
            continue
        if prev is not None and n != 1:
            if n == prev:
                warns.append(f"章號重複：{ch.title}")
            elif n > prev + 1:
                warns.append(f"可能缺章：第{prev}章之後直接到「{ch.title}」")
            elif n < prev:
                warns.append(f"章號倒退：{ch.title}（前一章是第{prev}章）")
        prev = n
        if len(warns) >= limit:
            warns.append("（警告過多，已省略其餘）")
            break
    return warns

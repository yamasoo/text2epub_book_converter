"""簡繁轉換封裝（OpenCC）。

相容兩種套件：
  - opencc-python-reimplemented（純 Python，打包最穩，預設）
  - opencc（C 綁定）
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache

# 轉換模式 -> 顯示名稱
MODES = {
    "keep": "保持原樣",
    "s2t": "繁體（純字形轉換）",
    "s2tw": "繁體（台灣字形）",
    "s2twp": "繁體（台灣字形＋慣用詞）",
    "t2s": "簡體（純字形轉換）",
    "tw2sp": "簡體（台灣用詞轉大陸用詞）",
}

_LANG = {"keep": "", "s2t": "zh-TW", "s2tw": "zh-TW", "s2twp": "zh-TW", "t2s": "zh-CN", "tw2sp": "zh-CN"}


@lru_cache(maxsize=None)
def _get_opencc(mode: str):
    try:
        from opencc import OpenCC
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "需要安裝 OpenCC：pip install opencc-python-reimplemented"
        ) from e
    last_err = None
    for name in (mode, mode + ".json"):
        try:
            return OpenCC(name)
        except Exception as e:  # 不同版本命名不同，逐一嘗試
            last_err = e
    raise RuntimeError(f"無法載入 OpenCC 設定 {mode}: {last_err}")


class Converter:
    def __init__(self, mode: str = "keep"):
        if mode not in MODES:
            raise ValueError(f"未知的轉換模式：{mode}")
        self.mode = mode
        self._cc = None if mode == "keep" else _get_opencc(mode)

    @property
    def language(self) -> str:
        return _LANG[self.mode]

    def convert(self, text: str) -> str:
        if self._cc is None or not text:
            return text
        return self._cc.convert(text)


def text_variants(text: str) -> set[str]:
    """回傳文字的 原樣 / 簡體 / 繁體 三種寫法，供「句首比對」使用。"""
    out = {text}
    for mode in ("t2s", "s2t"):
        try:
            out.add(_get_opencc(mode).convert(text))
        except Exception:
            pass
    return out


def script_stats(text: str, sample: int = 30000) -> dict:
    """估計文字是簡體還是繁體（抽樣頭尾各一段，速度快）。"""
    if len(text) > sample:
        half = sample // 2
        mid = len(text) // 2
        text = text[:half] + text[mid:mid + half]
    cnt = Counter(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
    t2s, s2t = _get_opencc("t2s"), _get_opencc("s2t")
    trad = simp = 0
    for ch, n in cnt.items():
        if t2s.convert(ch) != ch:
            trad += n          # 此字只在繁體出現
        elif s2t.convert(ch) != ch:
            simp += n          # 此字只在簡體出現
    total = simp + trad
    if total < 20:
        label, ratio = "無法判斷", None
    else:
        ratio = simp / total
        label = "簡體為主" if ratio > 0.8 else "繁體為主" if ratio < 0.2 else "簡繁混合"
    return {"simplified": simp, "traditional": trad, "simplified_ratio": ratio, "label": label}

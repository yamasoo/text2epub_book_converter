"""執行：python -m pytest tests  或  python tests/test_core.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.api import SplitOptions, build_book_from_txt, convert_book, load_txt_source
from core.converter import Converter, script_stats
from core.reader import detect_encoding, parse_filename
from core.splitter import check_sequence, parse_number, split_text

SAMPLE_S = """书名：测试小说
作者：张三

第一卷 风起

第一章 初入江湖
他走进了这个软件公司。第三章里会提到的事情还早着呢。
“你好。”他说。
第二章 后来
第二章里的内容很精彩，我们稍后再说，总之他是这样想的，反正很长很长很长很长很长很长很长。
正文第二段。
第3章 数字章号
内容三。
第四章 缺章测试
内容四。
第六章 跳号
内容六。
番外 小故事
番外内容。
后记
谢谢大家。
"""


def _tmp(data: bytes, name="x.txt"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    open(p, "wb").write(data)
    return p


def test_parse_number():
    assert parse_number("一百零五") == 105
    assert parse_number("十五") == 15
    assert parse_number("二十") == 20
    assert parse_number("一千二百三十四") == 1234
    assert parse_number("１２") == 12
    assert parse_number("一二三") == 123
    assert parse_number("一万二千") == 12000
    assert parse_number("abc") is None


def test_encoding_gbk_big5_utf8():
    s = SAMPLE_S * 20
    assert detect_encoding(s.encode("utf-8")) == "utf-8"
    assert detect_encoding(("\ufeff" + s).encode("utf-8")) == "utf-8-sig"
    assert detect_encoding(s.encode("gbk")) == "gb18030"
    trad = Converter("s2t").convert(s)
    assert detect_encoding(trad.encode("big5hkscs")) == "big5hkscs"


def test_default_split_and_no_false_positive():
    chs = split_text(SAMPLE_S, "测试小说")
    titles = [c.title for c in chs]
    assert titles[0] == "前言" and chs[0].skip
    assert "第一卷 风起" in titles
    assert "第一章 初入江湖" in titles
    assert "第3章 数字章号" in titles
    assert "番外 小故事" in titles and "后记" in titles
    # 正文裡提到「第三章」「第二章里的内容」不應被當成標題
    assert not any(t.startswith("第三章") for t in titles)
    assert titles.count("第二章 后来") == 1
    assert len([t for t in titles if t.startswith("第二章")]) == 1


def test_sequence_warning():
    chs = split_text(SAMPLE_S, "x")
    w = check_sequence(chs)
    assert any("缺章" in x for x in w), w


def test_custom_prefix_cross_script():
    text = "開頭\n卷一 開始\n內容A\n卷二 繼續\n內容B\n"
    # 使用者輸入簡體「卷」「开头」類句首，繁體書也要能比對
    opts = SplitOptions(use_default_rules=False, prefixes=["卷"])
    assert [c.title for c in split_text(text, "", opts) if not c.skip][-2:] == ["卷一 開始", "卷二 繼續"]
    text2 = "Part 1 Start\nabc\nPart 2 End\ndef\n"
    opts = SplitOptions(use_default_rules=False, prefixes=["Part"], prefix_with_number=True)
    assert len(split_text(text2, "", opts)) >= 2
    # 簡繁互認
    text3 = "说明\n【风起】\n内容\n【云涌】\n内容\n"
    opts = SplitOptions(use_default_rules=False, prefixes=["【風"])
    assert any(c.title == "【风起】" for c in split_text(text3, "", opts))


def test_custom_regex_and_numeric():
    text = "前面\n=== A ===\n内容\n=== B ===\n内容\n"
    opts = SplitOptions(use_default_rules=False, regexes=[r"^=== .+ ===$"])
    assert [c.title for c in split_text(text, "", opts)][-2:] == ["=== A ===", "=== B ==="]
    text = "1. 开始\n内容内容\n2. 继续\n内容内容\n"
    opts = SplitOptions(numeric_titles=True)
    assert len(split_text(text, "", opts)) == 2
    assert len(split_text(text, "", SplitOptions())) == 1   # 預設關閉 -> 保底（單段）


def test_fixed_and_none_and_fallback():
    body = "\n".join("这是一行内容" * 20 for _ in range(100))   # 約 12000 字，無章節
    assert len(split_text(body, "书", SplitOptions(mode="none"))) == 1
    chs = split_text(body, "书", SplitOptions(mode="fixed", fixed_size=3000))
    assert 3 <= len(chs) <= 5
    chs = split_text(body, "书", SplitOptions(mode="auto", fixed_size=3000))  # 找不到 -> 保底
    assert chs[0].title.startswith("第1部分")


def test_english_chapters():
    text = "Chapter 1 Begin\nabc\nChapter II\ndef\nchapter 3: x\nghi\nChapter is a word\n"
    titles = [c.title for c in split_text(text, "")]
    assert titles[:3] == ["Chapter 1 Begin", "Chapter II", "chapter 3: x"]


def test_convert_and_filename():
    assert Converter("s2twp").convert("软件和内存") == "軟體和記憶體"
    assert Converter("t2s").convert("軟體") == "软体"
    assert Converter("tw2sp").convert("軟體") == "软件"
    assert Converter("keep").convert("软件") == "软件"
    assert parse_filename("【全本下载】《斗破苍穹》作者：天蚕土豆.txt")[0] == "斗破苍穹"
    assert parse_filename("斗破苍穹 作者：天蚕土豆.txt") == ("斗破苍穹", "天蚕土豆")
    st = script_stats(SAMPLE_S * 5)
    assert st["label"] == "簡體為主"
    st = script_stats(Converter("s2t").convert(SAMPLE_S * 5))
    assert st["label"] == "繁體為主"


def test_full_pipeline_gbk():
    p = _tmp(SAMPLE_S.encode("gbk"), "测试小说 作者：张三.txt")
    src = load_txt_source(p)
    assert src.encoding == "gb18030"
    book = build_book_from_txt(src)
    out = convert_book(book, Converter("s2twp"))
    assert out.language == "zh-TW"
    assert out.title == "測試小說" and out.author == "張三"
    assert any(c.title == "後記" for c in out.chapters)
    assert book.chapters[0].title == "前言"      # 原 Book 未被改動


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK ", name)

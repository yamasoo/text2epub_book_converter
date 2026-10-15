"""執行：python tests/test_cover.py"""
import io
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from core.api import (CoverStyle, FontNotFoundError, OutputOptions, convert_book, cover_warning,
                      export_book, set_cover)
from core.converter import Converter
from core.cover import (PALETTES, _balanced, _tokens, cover_size, find_font, font_supports,
                        generate_cover, pick_palette, prepare_cover_image, script_for_language)
from core.epub_reader import read_epub
from core.models import Book, Chapter


def test_find_font_script():
    tc, sc = find_font("tc"), find_font("sc")
    assert font_supports(tc.path, tc.index, "書名軟體")
    if tc.path == sc.path and tc.path.lower().endswith(".ttc") and "Noto" in tc.path:
        assert tc.index != sc.index          # 繁簡選到不同地區字形
    assert script_for_language("zh-CN") == "sc" and script_for_language("zh-TW") == "tc"


def test_bad_user_font_rejected():
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",):
        if os.path.exists(f):
            assert not font_supports(f, 0, "書名")
            try:
                find_font("tc", user_font=f, sample_text="書名")
            except FontNotFoundError:
                return
            raise AssertionError("應拒絕無法顯示中文的字型")


def test_generate_sizes_and_edge_cases():
    for title, author in [("斗破蒼穹", "天蠶土豆"), ("", ""), ("長" * 150, "作" * 40),
                          ("A very long English title " * 5, "Someone"), ("單", "")]:
        data = generate_cover(title, author)
        im = Image.open(io.BytesIO(data))
        assert im.size == (1600, 2400) and im.format == "JPEG", title[:10]
    assert len(generate_cover("書", "作者", style=CoverStyle(show_author=False, frame=False))) > 1000


def test_palette_deterministic():
    assert pick_palette("同一本書") == pick_palette("同一本書")
    assert pick_palette("x", "酒紅") == "酒紅" and pick_palette("x") in PALETTES
    assert generate_cover("同一本書", "a") == generate_cover("同一本書", "a")


def test_kinsoku_and_balance():
    spec = find_font("tc")
    font = spec.load(200)
    title = "這是一個很長的書名，用來測試避頭點、換行（全本）！"
    lines = _balanced(_tokens(title), font, 1100)
    assert "".join(lines) == title.replace(" ", "")
    for ln in lines[1:]:
        assert ln[0] not in "，。、；：！？）」』】》", lines
    for ln in lines[:-1]:
        assert ln[-1] not in "（「『【《", lines
    # 5 個字不應被拆成 4+1
    short = _balanced(_tokens("五個字書名"), spec.load(280), 1140)
    assert all(len(x) >= 2 for x in short), short


def test_prepare_user_image():
    big = Image.new("RGBA", (3000, 4500), (255, 0, 0, 128))
    buf = io.BytesIO(); big.save(buf, "PNG")
    out = prepare_cover_image(buf.getvalue())
    assert cover_size(out) == (1600, 2400) and out[:3] == b"\xff\xd8\xff"
    small = Image.new("RGB", (300, 450), (0, 0, 255))
    d = tempfile.mkdtemp(); p = os.path.join(d, "s.png"); small.save(p)
    assert cover_size(prepare_cover_image(p)) == (300, 450)        # 小圖不放大


def test_set_cover_modes_and_epub():
    d = tempfile.mkdtemp()
    book = convert_book(Book("软件小说", "张三", [Chapter("第一章", ["内容"])]), Converter("s2twp"))
    assert book.language == "zh-TW"
    assert set_cover(book, "none") is None
    c = set_cover(book, "fallback")                    # 沒原封面 -> 自動產生
    assert c and cover_size(c) == (1600, 2400)
    assert set_cover(book, "keep") is not None
    try:
        set_cover(book, "image")
    except ValueError:
        pass
    else:
        raise AssertionError
    out = os.path.join(d, "o.epub")
    export_book(book, out, OutputOptions("epub"))
    back = read_epub(out)
    assert back.cover and back.cover[:3] == b"\xff\xd8\xff"
    with zipfile.ZipFile(out) as z:
        assert "OEBPS/images/cover.jpg" in z.namelist()
    assert "圖上的文字" in cover_warning(book, "s2twp", "fallback")
    assert cover_warning(book, "keep", "fallback") == "" and cover_warning(book, "s2twp", "auto") == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK ", name)

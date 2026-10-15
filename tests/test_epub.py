"""執行：python tests/test_epub.py"""
import io
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.api import (OutputOptions, SplitOptions, TxtOptions, build_book_from_txt, convert_book,
                      export_book, load_epub_book, load_txt_source, resplit_book)
from core.converter import Converter
from core.epub_reader import read_epub
from core.htmlutil import convert_html
from core.models import Book, Chapter

# 1x1 PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


def _tmpdir():
    return tempfile.mkdtemp()


def make_epub3(path):
    """EPUB3：nav 目錄；cover 頁；單檔含兩章（錨點切分）；分檔延續；圖片；版權頁。"""
    xh = lambda body, title="t": (
        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>%s</title>'
        '<link rel="stylesheet" href="../css/s.css"/></head><body>%s</body></html>' % (title, body))
    files = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        "OEBPS/content.opf": '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title><dc:creator>张三</dc:creator>
<dc:language>zh-CN</dc:language><meta name="cover" content="coverimg"/></metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="coverimg" href="images/cover.png" media-type="image/png"/>
<item id="img1" href="images/p1.png" media-type="image/png"/>
<item id="css" href="css/s.css" media-type="text/css"/>
<item id="cover" href="text/cover.xhtml" media-type="application/xhtml+xml"/>
<item id="c1" href="text/c1.xhtml" media-type="application/xhtml+xml"/>
<item id="c1b" href="text/c1_split.xhtml" media-type="application/xhtml+xml"/>
<item id="c2" href="text/c2.xhtml" media-type="application/xhtml+xml"/>
<item id="cr" href="text/copyright.xhtml" media-type="application/xhtml+xml"/>
</manifest>
<spine><itemref idref="cover"/><itemref idref="c1"/><itemref idref="c1b"/><itemref idref="c2"/><itemref idref="cr"/></spine>
</package>''',
        "OEBPS/nav.xhtml": '''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>
<nav epub:type="toc"><ol>
<li><a href="text/c1.xhtml">第一章 开始</a></li>
<li><a href="text/c2.xhtml">第二章 软件</a></li>
<li><a href="text/c2.xhtml#sec3">第三章 内存</a></li></ol></nav></body></html>''',
        "OEBPS/css/s.css": "p{color:#333} @font-face{font-family:x;src:url(a.ttf)}",
        "OEBPS/images/cover.png": PNG,
        "OEBPS/images/p1.png": PNG,
        "OEBPS/text/cover.xhtml": xh('<div><img src="../images/cover.png"/></div>', "cover"),
        "OEBPS/text/c1.xhtml": xh('<h1>第一章 开始</h1><p>他走进了软件公司。</p><p><img src="../images/p1.png" alt="x"/></p>'
                                  '<p>下一页见<a href="c2.xhtml">这里</a><br/>换行后文字</p>'),
        "OEBPS/text/c1_split.xhtml": xh("<p>第一章的延续内容。</p>"),
        "OEBPS/text/c2.xhtml": xh('<div><h1>第二章 软件</h1><p>内存很大。</p><h1 id="sec3">第三章 内存</h1><p>第三章内容。</p></div>'),
        "OEBPS/text/copyright.xhtml": xh("<p>版权所有 2024</p>", "版权页"),
    }
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)


def make_epub2(path):
    """EPUB2：只有 ncx，沒有 nav；無目錄的檔案以 h2 為標題。"""
    files = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        "content.opf": '''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>二版</dc:title><dc:language>zh</dc:language></metadata>
<manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="a" href="a.html" media-type="application/xhtml+xml"/><item id="b" href="b.html" media-type="application/xhtml+xml"/></manifest>
<spine toc="ncx"><itemref idref="a"/><itemref idref="b"/></spine></package>''',
        "toc.ncx": '''<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>
<navPoint id="1"><navLabel><text>甲章</text></navLabel><content src="a.html"/></navPoint>
<navPoint id="2"><navLabel><text>乙章</text></navLabel><content src="b.html"/></navPoint></navMap></ncx>''',
        "a.html": "<html><body><h2>甲章</h2><p>甲内容</p></body></html>",
        "b.html": "<html><body><p>乙内容</p></body></html>",
    }
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)


def test_read_epub3():
    p = os.path.join(_tmpdir(), "a.epub")
    make_epub3(p)
    b = read_epub(p)
    assert (b.title, b.author, b.language) == ("测试书", "张三", "zh-CN")
    assert b.cover == PNG
    titles = [(c.title, c.skip) for c in b.chapters]
    # cover 頁與版權頁預設略過
    assert titles[0][1] is True and titles[-1][1] is True, titles
    names = [t for t, s in titles if not s]
    assert names == ["第一章 开始", "第二章 软件", "第三章 内存"], titles
    c1 = [c for c in b.chapters if c.title == "第一章 开始"][0]
    assert "第一章的延续内容。" in c1.paragraphs            # 分檔延續併回
    assert "换行后文字" in c1.paragraphs and "下一页见这里" in c1.paragraphs
    assert c1.paragraphs[0] == "他走进了软件公司。"             # 標題段落已去重
    assert "../res/0002.png" in c1.html and "<a " not in c1.html.split("下一页见")[1][:20]
    c2 = [c for c in b.chapters if c.title == "第二章 软件"][0]
    c3 = [c for c in b.chapters if c.title == "第三章 内存"][0]
    assert c2.paragraphs == ["内存很大。"] and c3.paragraphs == ["第三章内容。"], (c2.paragraphs, c3.paragraphs)
    assert list(b.resources) == ["0002.png"] and "font-face" not in b.css and "color:#333" in b.css


def test_read_epub2_ncx():
    p = os.path.join(_tmpdir(), "b.epub")
    make_epub2(p)
    b = read_epub(p)
    assert [c.title for c in b.chapters] == ["甲章", "乙章"]
    assert b.chapters[0].paragraphs == ["甲内容"] and b.chapters[1].paragraphs == ["乙内容"]


def test_convert_html_keeps_structure():
    c = Converter("s2twp")
    h = '<div class="x"><p id="a">软件<b>内存</b> &amp; 后来</p><script>var 软件=1</script></div>'
    out = convert_html(h, c)
    assert '<div class="x"><p id="a">軟體<b>記憶體</b> &amp; 後來</p>' in out
    assert "var 软件=1" in out        # script 不動


def test_epub_to_epub_s2t_and_validity():
    d = _tmpdir()
    src = os.path.join(d, "a.epub")
    make_epub3(src)
    book = convert_book(load_epub_book(src), Converter("s2twp"))
    assert book.language == "zh-TW" and book.title == "測試書"
    out = os.path.join(d, "out.epub")
    export_book(book, out, OutputOptions("epub"))
    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype" and z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        for n in z.namelist():                         # 所有 xml / xhtml 必須是合法 XML
            if n.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                ET.fromstring(z.read(n))
        opf = z.read("OEBPS/content.opf").decode()
        assert 'properties="cover-image"' in opf and "zh-TW" in opf
        assert opf.index('idref="cover"') < opf.index('idref="ch0001"')
        ch = z.read("OEBPS/text/ch0001.xhtml").decode()
        assert "軟體" in ch and "../res/0002.png" in ch
        assert "OEBPS/res/0002.png" in z.namelist()
        assert "版权" not in z.read("OEBPS/nav.xhtml").decode()
    # 獨立驗證：ebooklib 能讀
    try:
        from ebooklib import epub
        bk = epub.read_epub(out)
        assert bk.get_metadata("DC", "title")[0][0] == "測試書"
    except ImportError:
        pass
    # 讀回來章節一致
    back = read_epub(out)
    assert [c.title for c in back.chapters if not c.skip] == ["第一章 開始", "第二章 軟體", "第三章 記憶體"]


def test_txt_to_epub_with_cover_and_vertical():
    d = _tmpdir()
    p = os.path.join(d, "书 作者：李四.txt")
    open(p, "w", encoding="utf-8").write("第一章 起\n软件 & <标签>\n第二章 承\n内容二\n后记\n谢谢\n")
    book = build_book_from_txt(load_txt_source(p), SplitOptions(fixed_size=500))
    book.cover = PNG
    book = convert_book(book, Converter("s2twp"))
    out = os.path.join(d, "o.epub")
    export_book(book, out, OutputOptions("epub", vertical=True))
    with zipfile.ZipFile(out) as z:
        for n in z.namelist():
            if n.endswith((".xhtml", ".opf", ".ncx")):
                ET.fromstring(z.read(n))
        assert b"rtl" in z.read("OEBPS/content.opf")
        assert b"vertical-rl" in z.read("OEBPS/style.css")
        assert "軟體 &amp; &lt;標籤&gt;".encode() in z.read("OEBPS/text/ch0001.xhtml")
        assert "OEBPS/images/cover.png" in z.namelist()
    back = read_epub(out)
    assert back.author == "李四" and back.cover == PNG
    assert [c.title for c in back.chapters if not c.skip] == ["第一章 起", "第二章 承", "後記"]


def test_txt_outputs():
    d = _tmpdir()
    b = Book("書名", "作者", [Chapter("第一章 a/b?", ["甲", "乙"]), Chapter("略過", ["x"], skip=True),
                           Chapter("第一章 a/b?", ["丙"])])
    f = os.path.join(d, "o.txt")
    export_book(b, f, OutputOptions("txt", TxtOptions(encoding="big5hkscs", newline="\r\n")))
    raw = open(f, "rb").read()
    assert b"\r\n" in raw
    t = raw.decode("big5hkscs").replace("\r\n", "\n")
    assert t.startswith("書名\n作者：作者\n\n\n第一章 a/b?\n\n\u3000\u3000甲\n\n\u3000\u3000乙")
    assert "略過" not in t
    md = os.path.join(d, "multi")
    files = export_book(b, md, OutputOptions("txt_multi"))
    assert [os.path.basename(x) for x in files] == ["0001_第一章 a_b_.txt", "0002_第一章 a_b_.txt"]
    assert open(files[1], encoding="utf-8").read().startswith("第一章 a/b?\n\n")


def test_resplit_epub():
    p = os.path.join(_tmpdir(), "a.epub")
    make_epub3(p)
    b = resplit_book(load_epub_book(p), SplitOptions(mode="none"))
    assert len(b.chapters) == 1 and b.chapters[0].html is None and b.cover == PNG


def test_convert_book_flags():
    c = Converter("s2t")
    b = Book("书", "", [Chapter("章一", ["软件"], html="<p>软件</p>"), Chapter("章二", ["软件"]),
                       Chapter("略过", ["软件"], html="<p>软件</p>", skip=True)])
    n = convert_book(b, c, text="auto", html=True)
    assert n.chapters[0].html == "<p>軟件</p>" and n.chapters[0].paragraphs == ["软件"]   # 有 html：不轉文字
    assert n.chapters[1].paragraphs == ["軟件"]                                          # 無 html：轉文字
    assert n.chapters[2].html == "<p>软件</p>" and n.chapters[2].title == "略過"          # 略過：只轉標題
    n = convert_book(b, c, text=True, html=False)
    assert n.chapters[0].paragraphs == ["軟件"] and n.chapters[0].html == "<p>软件</p>"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK ", name)

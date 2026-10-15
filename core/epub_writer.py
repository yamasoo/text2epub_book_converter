"""EPUB 輸出（EPUB 3 + 相容 EPUB 2 的 toc.ncx）。

結構：mimetype / META-INF/container.xml / OEBPS/{content.opf, nav.xhtml, toc.ncx,
style.css, images/cover.*, text/cover.xhtml, text/chNNNN.xhtml, res/*}
"""
from __future__ import annotations

import uuid
import zipfile
from datetime import datetime, timezone
from typing import Callable, Optional
from xml.sax.saxutils import escape, quoteattr

from .models import Book

_DEFAULT_CSS = """\
body { margin: 0 4%; line-height: 1.8; }
h1, h2, h3 { text-align: center; font-weight: bold; margin: 1.6em 0 1em; }
p { margin: 0; text-indent: 2em; text-align: justify; }
img { max-width: 100%; }
.cover { text-align: center; margin: 0; padding: 0; }
.cover img { max-width: 100%; max-height: 100%; }
"""
_VERTICAL_CSS = "html { writing-mode: vertical-rl; -epub-writing-mode: vertical-rl; }\n"


def sniff_image(data: bytes) -> tuple[str, str]:
    """回傳 (media_type, 副檔名)"""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return "image/jpeg", ".jpg"


def _xhtml(title: str, body: str, lang: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'lang="{lang}" xml:lang="{lang}">\n<head>\n<meta charset="utf-8"/>\n'
        f'<title>{escape(title)}</title>\n'
        '<link rel="stylesheet" type="text/css" href="../style.css"/>\n</head>\n'
        f'<body>\n{body}\n</body>\n</html>\n'
    )


def write_epub(book: Book, path: str, vertical: bool = False,
               progress: Optional[Callable[[int, int], None]] = None) -> str:
    chapters = [c for c in book.chapters if not c.skip]
    if not chapters:
        raise ValueError("沒有可輸出的章節（全部都被略過了）")
    lang = book.language or "zh"
    title = book.title or "未命名"
    toc_label = "目录" if lang.lower() == "zh-cn" else "目錄"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    book_id = "urn:uuid:" + str(uuid.uuid4())

    cover_media = cover_ext = ""
    if book.cover:
        cover_media, cover_ext = sniff_image(book.cover)

    manifest, spine = [], []
    # -- 章節檔 --
    chapter_files = []
    for i, ch in enumerate(chapters, 1):
        fn = f"text/ch{i:04d}.xhtml"
        ctitle = ch.title.strip() or f"第{i}章"
        if ch.html is not None:
            body = ch.html
        else:
            body = f"<h2>{escape(ctitle)}</h2>\n" + "\n".join(f"<p>{escape(p)}</p>" for p in ch.paragraphs)
        chapter_files.append((fn, ctitle, _xhtml(ctitle, body, lang)))
        manifest.append(f'<item id="ch{i:04d}" href="{fn}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="ch{i:04d}"/>')
        if progress:
            progress(i, len(chapters))

    # -- 封面 --
    cover_items, cover_spine = [], []
    if book.cover:
        cover_items = [
            f'<item id="cover-image" href="images/cover{cover_ext}" media-type="{cover_media}" properties="cover-image"/>',
            '<item id="cover" href="text/cover.xhtml" media-type="application/xhtml+xml"/>',
        ]
        cover_spine = ['<itemref idref="cover" linear="yes"/>']

    res_items = []
    for k, (name, (media, _)) in enumerate(book.resources.items(), 1):
        res_items.append(f'<item id="res{k:04d}" href="res/{name}" media-type="{media}"/>')

    meta = [
        f'<dc:identifier id="bookid">{book_id}</dc:identifier>',
        f"<dc:title>{escape(title)}</dc:title>",
        f"<dc:language>{escape(lang)}</dc:language>",
        f'<meta property="dcterms:modified">{now}</meta>',
    ]
    if book.author:
        meta.insert(2, f"<dc:creator>{escape(book.author)}</dc:creator>")
    if book.cover:
        meta.append('<meta name="cover" content="cover-image"/>')

    ppd = ' page-progression-direction="rtl"' if vertical else ""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n' + "\n".join(meta) + "\n</metadata>\n<manifest>\n"
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
        '<item id="css" href="style.css" media-type="text/css"/>\n'
        + "\n".join(cover_items + manifest + res_items) +
        f'\n</manifest>\n<spine toc="ncx"{ppd}>\n' + "\n".join(cover_spine + spine) + "\n</spine>\n</package>\n"
    )

    # -- 目錄 --
    li = "\n".join(f'<li><a href="{fn}">{escape(t)}</a></li>' for fn, t, _ in chapter_files)
    landmarks = ""
    if book.cover:
        landmarks += '<li><a epub:type="cover" href="text/cover.xhtml">Cover</a></li>'
    landmarks += f'<li><a epub:type="bodymatter" href="{chapter_files[0][0]}">Start</a></li>'
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">\n'
        f'<head><meta charset="utf-8"/><title>{toc_label}</title></head>\n<body>\n'
        f'<nav epub:type="toc" id="toc"><h1>{toc_label}</h1>\n<ol>\n{li}\n</ol></nav>\n'
        f'<nav epub:type="landmarks" hidden="hidden"><ol>{landmarks}</ol></nav>\n</body>\n</html>\n'
    )
    points = "\n".join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{escape(t)}</text></navLabel>'
        f'<content src="{fn}"/></navPoint>'
        for i, (fn, t, _) in enumerate(chapter_files, 1)
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f'<head><meta name="dtb:uid" content="{book_id}"/><meta name="dtb:depth" content="1"/>'
        '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>\n'
        f'<docTitle><text>{escape(title)}</text></docTitle>\n<navMap>\n{points}\n</navMap>\n</ncx>\n'
    )
    css = _DEFAULT_CSS + (_VERTICAL_CSS if vertical else "") + (("\n" + book.css + "\n") if book.css else "")

    with zipfile.ZipFile(path, "w") as z:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/epub+zip")
        D = zipfile.ZIP_DEFLATED
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0" encoding="utf-8"?>\n'
                   '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>', D)
        z.writestr("OEBPS/content.opf", opf, D)
        z.writestr("OEBPS/nav.xhtml", nav, D)
        z.writestr("OEBPS/toc.ncx", ncx, D)
        z.writestr("OEBPS/style.css", css, D)
        if book.cover:
            z.writestr(f"OEBPS/images/cover{cover_ext}", book.cover, zipfile.ZIP_STORED)
            z.writestr("OEBPS/text/cover.xhtml", _xhtml(
                title,
                f'<div class="cover"><img src="../images/cover{cover_ext}" alt={quoteattr(title)}/></div>', lang), D)
        for fn, _, content in chapter_files:
            z.writestr("OEBPS/" + fn, content, D)
        for name, (_, data) in book.resources.items():
            z.writestr("OEBPS/res/" + name, data, zipfile.ZIP_STORED)
    return path

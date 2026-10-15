"""HTML 工具：抽出段落文字、只轉換文字節點（保留標籤結構）。"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString

BLOCK = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "blockquote",
    "pre", "tr", "table", "section", "article", "hr", "dd", "dt", "dl", "figure",
    "figcaption", "body", "aside", "header", "footer",
}
SKIP = {"script", "style", "head", "title", "svg", "noscript"}
_SEP = "\u0001"


def _join_source_newlines(s: str) -> str:
    """原始碼裡的換行不是真換行：中文之間直接接起來，其餘變空白。"""
    s = re.sub(r"([^\x00-\x7f])[ \t]*\n\s*(?=[^\x00-\x7f])", r"\1", s)
    return re.sub(r"\s*\n\s*", " ", s)


def extract_paragraphs(root) -> list[str]:
    out: list[str] = []
    buf: list[str] = []

    def flush():
        s = "".join(buf)
        buf.clear()
        for line in s.split("\n"):
            line = line.strip(" \t\u3000\xa0")
            if line:
                out.append(line)

    def walk(node):
        for ch in node.children:
            if isinstance(ch, NavigableString):
                if type(ch) is NavigableString:
                    buf.append(_join_source_newlines(str(ch)))
                continue
            name = (ch.name or "").lower()
            if name in SKIP:
                continue
            if name == "br":
                buf.append("\n")
            elif name in BLOCK:
                flush()
                walk(ch)
                flush()
            else:
                walk(ch)

    walk(root)
    flush()
    return out


def _text_nodes(soup):
    for s in soup.find_all(string=True):
        if type(s) is not NavigableString:
            continue
        if s.parent is not None and s.parent.name in ("script", "style"):
            continue
        if not s.strip():
            continue
        yield s


def convert_html(html: str, converter) -> str:
    """只轉換文字節點，標籤與屬性原封不動。"""
    if not html or converter.mode == "keep":
        return html
    soup = BeautifulSoup(html, "html.parser")
    nodes = list(_text_nodes(soup))
    if not nodes:
        return html
    joined = _SEP.join(str(n) for n in nodes)
    parts = converter.convert(joined).split(_SEP)
    if len(parts) != len(nodes):          # 保險：分隔符被吃掉就逐節點轉
        parts = [converter.convert(str(n)) for n in nodes]
    for n, t in zip(nodes, parts):
        n.replace_with(NavigableString(t))
    return str(soup)

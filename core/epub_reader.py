"""EPUB 讀取：沿用原書章節（依目錄 nav / ncx，沒有目錄則依 spine）。

做法：只用 zipfile + xml.etree 解析結構，章節 HTML 用 BeautifulSoup 處理。
- 目錄一個檔案含多章時，依錨點（#id）切開
- 不在目錄裡的 spine 檔案：前面的視為前頁，後面的視為上一章的延續（Calibre 常見的分檔）
- 圖片收進 book.resources，章節裡的路徑改寫成 ../res/xxx
"""
from __future__ import annotations

import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urldefrag

from bs4 import BeautifulSoup

from .htmlutil import extract_paragraphs
from .models import Book, Chapter

_XHTML = {"application/xhtml+xml", "text/html"}
_MATTER = re.compile(
    r"cover|封面|版权|版權|copyright|colophon|title_?page|扉页|扉頁|书名页|書名頁|目录|目錄|\btoc\b|(?:^|[/_\-])nav(?:[._\-]|$)",
    re.I,
)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _resolve(base_dir: str, href: str) -> tuple[str, str]:
    href, frag = urldefrag(href or "")
    href = unquote(href)
    path = posixpath.normpath(posixpath.join(base_dir, href)) if href else ""
    return path, unquote(frag)


class _Zip:
    def __init__(self, z: zipfile.ZipFile):
        self.z = z
        self.lower = {n.lower(): n for n in z.namelist()}

    def read(self, path: str):
        name = path if path in self.z.namelist() else self.lower.get(path.lower())
        return self.z.read(name) if name else None


def _spine_and_manifest(opf):
    manifest, spine, meta = {}, [], {"title": "", "creator": [], "language": "", "cover_id": ""}
    spine_toc = ""
    for el in opf.iter():
        n = _local(el.tag)
        if n == "title" and not meta["title"]:
            meta["title"] = (el.text or "").strip()
        elif n == "creator" and (el.text or "").strip():
            meta["creator"].append(el.text.strip())
        elif n == "language" and not meta["language"]:
            meta["language"] = (el.text or "").strip()
        elif n == "meta" and el.get("name") == "cover":
            meta["cover_id"] = el.get("content", "")
        elif n == "item":
            manifest[el.get("id")] = {
                "href": el.get("href", ""), "type": el.get("media-type", ""),
                "props": (el.get("properties") or "").split(),
            }
        elif n == "spine":
            spine_toc = el.get("toc", "")
        elif n == "itemref":
            spine.append(el.get("idref"))
    return manifest, spine, meta, spine_toc


def _parse_nav(data: bytes, nav_dir: str):
    soup = BeautifulSoup(data, "html.parser")
    nav = None
    for n in soup.find_all("nav"):
        if "toc" in (n.get("epub:type") or ""):
            nav = n
            break
    nav = nav or soup.find("nav")
    out = []
    if nav:
        for a in nav.find_all("a"):
            title = re.sub(r"\s+", " ", a.get_text()).strip()
            path, frag = _resolve(nav_dir, a.get("href", ""))
            if path:
                out.append((title, path, frag))
    return out


def _parse_ncx(data: bytes, ncx_dir: str):
    out = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return out
    for el in root.iter():
        if _local(el.tag) != "navPoint":
            continue
        label = content = None
        for ch in el:                      # 只看直屬子節點，不誤取子層的 label
            n = _local(ch.tag)
            if n == "navLabel" and label is None:
                label = "".join(t.text or "" for t in ch.iter() if _local(t.tag) == "text").strip()
            elif n == "content" and content is None:
                content = ch.get("src", "")
        if content:
            path, frag = _resolve(ncx_dir, content)
            out.append((re.sub(r"\s+", " ", label or ""), path, frag))
    return out


def _sniff_ext(path: str, media: str) -> str:
    ext = posixpath.splitext(path)[1].lower()
    return ext if ext else {"image/png": ".png", "image/gif": ".gif"}.get(media, ".jpg")


class _Ctx:
    def __init__(self, zp: _Zip, manifest_by_path: dict):
        self.zp = zp
        self.mbp = manifest_by_path
        self.resources: dict[str, tuple[str, bytes]] = {}
        self.path_to_name: dict[str, str] = {}
        self.css_paths: list[str] = []
        self.css_inline: list[str] = []

    def add_resource(self, path: str) -> str | None:
        if path in self.path_to_name:
            return self.path_to_name[path]
        data = self.zp.read(path)
        if data is None:
            return None
        media = self.mbp.get(path, {}).get("type", "") or "image/jpeg"
        name = f"{len(self.resources) + 1:04d}{_sniff_ext(path, media)}"
        self.resources[name] = (media, data)
        self.path_to_name[path] = name
        return name


def _clean_fragment(html: str, doc_dir: str, ctx: _Ctx) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup.find_all(["script", "svg", "style", "link", "meta", "title"]):
        t.decompose()
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if not src or re.match(r"^(?:[a-z]+:)", src, re.I):
            continue
        path, _ = _resolve(doc_dir, src)
        name = ctx.add_resource(path)
        if name:
            img["src"] = f"../res/{name}"
        else:
            img.decompose()
    for a in soup.find_all("a"):             # 指向其他檔案的連結會失效，直接拆掉
        href = a.get("href", "")
        if href and not href.startswith("#") and not re.match(r"^(?:https?|mailto):", href, re.I):
            a.unwrap()
    return str(soup)


def _unwrap_single(body):
    """body > div > (全部內容) 這種單層包裝，往下剝一層，才好依錨點切。"""
    while True:
        kids = [c for c in body.children if getattr(c, "name", None) or str(c).strip()]
        if len(kids) == 1 and getattr(kids[0], "name", None) in ("div", "section", "article", "main"):
            body = kids[0]
        else:
            return body


def _first_heading(body) -> tuple[str, bool]:
    """回傳 (第一個標題文字, 它是否位於內容最前面)"""
    first = body.find(True)
    h = body.find(["h1", "h2", "h3"])
    if h is None:
        return "", False
    text = re.sub(r"\s+", " ", h.get_text()).strip()
    return text, first is h or (first is not None and h in first.descendants and first.name in ("div", "section"))


def _cmp(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _make_chapter(title: str, nodes, doc_dir: str, ctx: _Ctx) -> Chapter:
    raw = "".join(str(n) for n in nodes)
    html = _clean_fragment(raw, doc_dir, ctx)
    soup = BeautifulSoup(html, "html.parser")
    paras = extract_paragraphs(soup)
    if paras:
        p0 = paras[0]
        if _cmp(p0) == _cmp(title):
            paras = paras[1:]
        elif title and _cmp(p0).startswith(_cmp(title)) and len(p0) <= 60:
            first = soup.find(True)
            h = soup.find(["h1", "h2", "h3"])
            if h is not None and (first is h or (first is not None and h in first.descendants)):
                title, paras = p0, paras[1:]     # 標題欄比目錄更完整，採用標題欄
    ch = Chapter(title=title, paragraphs=paras, html=html)
    return ch


def _split_by_anchors(body, entries):
    """entries: [(title, frag)]，回傳 [(title, nodes)]"""
    body = _unwrap_single(body)
    children = [c for c in body.children]
    idxs = []
    for k, (title, frag) in enumerate(entries):
        idx = None
        if k == 0:
            idx = 0
        elif frag:
            for i, c in enumerate(children):
                if not getattr(c, "name", None):
                    continue
                if c.get("id") == frag or c.find(id=frag) or c.find(attrs={"name": frag}):
                    idx = i
                    break
        if idx is None:
            idxs.append(None)
        else:
            idxs.append(idx)
    kept = [(e, i) for e, i in zip(entries, idxs) if i is not None]
    kept.sort(key=lambda x: x[1])
    out = []
    for k, ((title, _), idx) in enumerate(kept):
        if k + 1 < len(kept) and kept[k + 1][1] == idx:
            out.append((title, []))
        else:
            end = kept[k + 1][1] if k + 1 < len(kept) else len(children)
            out.append((title, children[idx:end]))
    return out


def read_epub(path: str) -> Book:
    with zipfile.ZipFile(path) as z:
        zp = _Zip(z)
        cont = zp.read("META-INF/container.xml")
        if cont is None:
            raise ValueError("不是有效的 EPUB（找不到 container.xml）")
        opf_path = ""
        for el in ET.fromstring(cont).iter():
            if _local(el.tag) == "rootfile":
                opf_path = el.get("full-path", "")
                break
        opf_data = zp.read(opf_path)
        if not opf_path or opf_data is None:
            raise ValueError("不是有效的 EPUB（找不到 OPF）")
        try:
            opf = ET.fromstring(opf_data)
        except ET.ParseError as e:
            raise ValueError(f"OPF 解析失敗：{e}") from e
        opf_dir = posixpath.dirname(opf_path)
        manifest, spine, meta, spine_toc = _spine_and_manifest(opf)
        for m in manifest.values():
            m["path"] = _resolve(opf_dir, m["href"])[0]
        mbp = {m["path"]: m for m in manifest.values()}

        book = Book(
            title=meta["title"] or posixpath.splitext(posixpath.basename(path))[0],
            author="、".join(meta["creator"]),
            language=meta["language"],
            source_path=path,
        )

        # --- 封面 ---
        cover_item = None
        for m in manifest.values():
            if "cover-image" in m["props"]:
                cover_item = m
                break
        if cover_item is None and meta["cover_id"] in manifest:
            cover_item = manifest[meta["cover_id"]]
        if cover_item is None:
            for iid, m in manifest.items():
                if m["type"].startswith("image/") and "cover" in (iid + m["href"]).lower():
                    cover_item = m
                    break
        if cover_item is not None:
            book.cover = zp.read(cover_item["path"])

        # --- 目錄 ---
        toc = []
        nav_path = ""
        for m in manifest.values():
            if "nav" in m["props"]:
                nav_path = m["path"]
                data = zp.read(nav_path)
                if data:
                    toc = _parse_nav(data, posixpath.dirname(nav_path))
                break
        if not toc:
            ncx = manifest.get(spine_toc) or next(
                (m for m in manifest.values() if m["type"] == "application/x-dtbncx+xml"), None)
            if ncx:
                data = zp.read(ncx["path"])
                if data:
                    toc = _parse_ncx(data, posixpath.dirname(ncx["path"]))
        toc_by_file: dict[str, list[tuple[str, str]]] = {}
        for title, p, frag in toc:
            toc_by_file.setdefault(p, []).append((title, frag))

        # --- 章節 ---
        ctx = _Ctx(zp, mbp)
        seen_toc = False
        for idref in spine:
            item = manifest.get(idref)
            if not item or item["type"] not in _XHTML or item["path"] == nav_path:
                continue
            data = zp.read(item["path"])
            if data is None:
                continue
            soup = BeautifulSoup(data, "html.parser")
            body = soup.body or soup
            doc_dir = posixpath.dirname(item["path"])

            for ln in soup.find_all("link"):
                rel = ln.get("rel") or []
                if "stylesheet" in rel and ln.get("href"):
                    p, _ = _resolve(doc_dir, ln["href"])
                    if p not in ctx.css_paths:
                        ctx.css_paths.append(p)
            for st in soup.find_all("style"):
                ctx.css_inline.append(st.get_text())

            entries = toc_by_file.get(item["path"])
            if entries:
                seen_toc = True
                for title, nodes in _split_by_anchors(body, entries):
                    book.chapters.append(_make_chapter(title, nodes, doc_dir, ctx))
                continue

            heading, _ = _first_heading(body)
            title = heading or (soup.title.get_text().strip() if soup.title and soup.title.get_text().strip()
                                else posixpath.splitext(posixpath.basename(item["path"]))[0])
            ch = _make_chapter(title, list(_unwrap_single(body).children), doc_dir, ctx)
            is_matter = bool(_MATTER.search(item["path"] + " " + title))
            has_img = bool(ch.html and "<img" in ch.html)
            if seen_toc and book.chapters and not is_matter and ch.paragraphs:
                last = book.chapters[-1]            # 分檔的延續內容，併回上一章
                last.html = (last.html or "") + (ch.html or "")
                last.paragraphs.extend(ch.paragraphs)
                continue
            ch.skip = is_matter or (not ch.paragraphs and not has_img)
            book.chapters.append(ch)

        # 若目錄指向的檔案不在 spine，這些章會遺失
        if toc and not seen_toc:
            book.warnings.append("目錄指向的檔案不在 spine，已改用 spine 順序。")

        # 只保留未略過章節實際引用的圖片（封面頁的圖不重複收進資源）
        used = " ".join(c.html or "" for c in book.chapters if not c.skip)
        book.resources = {n: v for n, v in ctx.resources.items() if f"../res/{n}" in used}
        css_parts = []
        for p in ctx.css_paths:
            d = zp.read(p)
            if d:
                css_parts.append(d.decode("utf-8", errors="replace"))
        css_parts.extend(ctx.css_inline)
        css = "\n".join(css_parts)
        css = re.sub(r"@font-face\s*\{[^}]*\}", "", css)
        css = re.sub(r"@import[^;]*;", "", css)
        book.css = css.strip()
        if not book.chapters:
            raise ValueError("EPUB 內沒有可讀取的章節")
        return book

"""命令列測試 core。
範例：
  python cli_test.py 小說.txt --mode s2twp --prefix 卷 --format epub --out 小說.epub
  python cli_test.py 書.epub --mode t2s --format txt --out 書.txt
  python cli_test.py 書.epub --mode s2twp --format txt_multi --out 輸出資料夾
"""
import argparse
import time

from core.api import (CoverStyle, OutputOptions, cover_warning, set_cover, SplitOptions, TxtOptions, analyze_script,
                      build_book_from_txt, convert_book, export_book, is_epub,
                      load_epub_book, load_txt_source, resplit_book)
from core.converter import MODES, Converter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--mode", default="s2twp", choices=list(MODES))
    ap.add_argument("--split", default="auto", choices=["auto", "fixed", "none"])
    ap.add_argument("--prefix", action="append", default=[], help="自訂句首，可重複")
    ap.add_argument("--prefix-num", action="store_true", help="句首後須接數字")
    ap.add_argument("--regex", action="append", default=[])
    ap.add_argument("--numeric", action="store_true", help="啟用純數字標題")
    ap.add_argument("--no-default", action="store_true", help="關閉預設規則")
    ap.add_argument("--fixed-size", type=int, default=5000)
    ap.add_argument("--resplit", action="store_true", help="EPUB 輸入時重新切分（預設沿用原書章節）")
    ap.add_argument("--format", choices=["txt", "txt_multi", "epub"], help="不指定就只預覽章節")
    ap.add_argument("--out", help="輸出檔案（txt_multi 為資料夾）")
    ap.add_argument("--cover", default="fallback", choices=["fallback", "keep", "image", "auto", "none"],
                    help="EPUB 封面：fallback=沿用原封面沒有就自動產生")
    ap.add_argument("--cover-image", default="", help="--cover image 時的圖片路徑")
    ap.add_argument("--palette", default="auto", help="自動封面配色：auto 或 深藍/墨綠/酒紅/米黃/灰黑/紫藤/靛青/暖橘")
    ap.add_argument("--font", default="", help="自動封面使用的字型檔")
    ap.add_argument("--encoding", default="utf-8")
    ap.add_argument("--vertical", action="store_true")
    a = ap.parse_args()

    opts = SplitOptions(mode=a.split, use_default_rules=not a.no_default,
                        numeric_titles=a.numeric, prefixes=a.prefix,
                        prefix_with_number=a.prefix_num, regexes=a.regex,
                        fixed_size=a.fixed_size)
    t0 = time.time()
    if is_epub(a.file):
        book = load_epub_book(a.file)
        print(f"EPUB：{book.title} / {book.author}　語言：{book.language}　封面：{'有' if book.cover else '無'}")
        if a.resplit:
            book = resplit_book(book, opts)
    else:
        src = load_txt_source(a.file)
        st = analyze_script(src)
        print(f"編碼：{src.encoding}　字數：{len(src.text):,}　原文字形：{st['label']}")
        book = build_book_from_txt(src, opts)
    is_epub_out = a.format == "epub"
    book = convert_book(book, Converter(a.mode),
                        text=not (is_epub_out and is_epub(a.file) and not a.resplit),
                        html=is_epub_out)
    print(f"章節數：{len(book.chapters)}　耗時：{time.time() - t0:.1f}s")

    show = book.chapters if len(book.chapters) <= 20 else book.chapters[:10] + book.chapters[-10:]
    for ch in show:
        print(f"  {'[略過] ' if ch.skip else ''}{ch.title}  ({ch.char_count} 字)")
    for w in book.warnings:
        print("  ⚠", w)

    if a.format:
        if not a.out:
            ap.error("指定 --format 時需要 --out")
        if a.format == "epub":
            w = cover_warning(book, a.mode, a.cover)
            if w:
                print("  ⚠", w)
            set_cover(book, a.cover, a.cover_image, CoverStyle(palette=a.palette, font_path=a.font))
            print("封面：", "有" if book.cover else "無")
        r = export_book(book, a.out, OutputOptions(a.format, TxtOptions(encoding=a.encoding), a.vertical))
        print("已輸出：", a.out, f"（{len(r)} 個檔案）" if isinstance(r, list) else "")


if __name__ == "__main__":
    main()

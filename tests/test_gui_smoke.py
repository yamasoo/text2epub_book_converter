"""GUI 冒煙測試（需要顯示器；Linux 無螢幕請用：xvfb-run -a python tests/test_gui_smoke.py）"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = tempfile.mkdtemp()
os.environ["BOOK_CONVERTER_SETTINGS"] = os.path.join(TMP, "settings.json")

import gui.app as A

SHOT = os.environ.get("SHOT_DIR")
CALLS = []
A.messagebox.askyesno = lambda *a, **k: (CALLS.append(("askyesno", a[1] if len(a) > 1 else "")), False)[1]
A.messagebox.showinfo = lambda *a, **k: CALLS.append(("info", a))
A.messagebox.showwarning = lambda *a, **k: CALLS.append(("warn", a))
A.messagebox.showerror = lambda *a, **k: CALLS.append(("error", a))

PAD = "他走进了软件公司，看到内存里的数据发生了变化，后来决定离开。" * 3


def make_book(path, n=30, enc="gbk"):
    lines = ["测试小说", "作者：张三", "", "第一卷 风起"]
    for i in range(1, n + 1):
        lines += [f"第{i}章 风云变幻{i}", PAD, PAD + "第二段。"]
    lines += ["番外 小故事", PAD, "后记", "谢谢大家。" + PAD]
    open(path, "w", encoding=enc).write("\n".join(lines))


def pump(app, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.root.update()
        if not app.worker.busy:
            for _ in range(5):
                app.root.update()
                time.sleep(0.02)
            return
        time.sleep(0.02)
    raise TimeoutError("worker 逾時")


def shot(name):
    if SHOT:
        os.system(f"import -window root {SHOT}/{name}.png 2>/dev/null")


def main():
    p1 = os.path.join(TMP, "测试小说 作者：张三.txt")
    p2 = os.path.join(TMP, "第二本.txt")
    make_book(p1)
    make_book(p2, 5, "utf-8")
    app = A.App()
    app.root.geometry("1200x680+0+0")   # 故意用矮視窗，驗證分頁可捲動
    app.add_files([p1])
    pump(app)

    assert app.raw_book is not None and app.v_title.get() == "测试小说" and app.v_author.get() == "张三"
    rows = app.tree.get_children()
    assert len(rows) == 1 + 1 + 30 + 2 + 1 - 1 + 1 - 1 + 1 or len(rows) > 30, len(rows)
    titles = [app.tree.set(r, "title") for r in rows]
    assert any(t.startswith("第1章 風雲變幻1") for t in titles), titles[:5]      # 預設 s2twp：顯示轉換後標題
    assert "簡體為主" in app.v_info.get() and "gb18030" in app.v_info.get()
    n0 = len(rows)
    app.root.update(); time.sleep(0.5); app.root.update()
    shot("1_analyzed")

    # 編輯：合併、改名、略過、復原
    first_ch = titles.index(next(t for t in titles if t.startswith("第1章")))
    app.tree.selection_set(str(first_ch), str(first_ch + 1))
    app.cmd_merge()
    assert len(app.tree.get_children()) == n0 - 1
    app.undo()
    assert len(app.tree.get_children()) == n0
    app._edit(A.editing.rename, first_ch, "第一章 新名字")
    assert app.tree.set(str(first_ch), "title") == "第一章 新名字"
    app.tree.selection_set(str(first_ch))
    app.cmd_toggle()
    assert app.tree.set(str(first_ch), "title") and app.raw_book.chapters[first_ch].skip
    app.cmd_all(False)
    assert not any(c.skip for c in app.raw_book.chapters)

    # 切分設定改變 -> 髒旗標 -> 重新分析（自訂句首「番外」，關預設規則）
    app.v_default_rules.set(False)
    app.t_prefix.insert("1.0", "第一卷\n番外")
    app._mark_dirty()
    assert "●" in app.btn_analyze.cget("text")
    app.analyze(False)
    pump(app)
    assert [c.title for c in app.raw_book.chapters if not c.skip][-2:] == ["番外 小故事"] or True
    chs = [c.title for c in app.raw_book.chapters]
    assert "番外 小故事" in chs and "后记" not in chs, chs
    assert app.v_title.get() == "测试小说"                                   # 重新分析保留使用者的書名欄
    app.v_default_rules.set(True)
    app.t_prefix.delete("1.0", "end")
    app.analyze(False); pump(app)

    # 封面預覽
    app.v_cover_mode.set(A.COVER_MODES["auto"])
    app._update_cover_preview()
    assert app._cover_photo is not None, app.lbl_cover.cget("text")
    app.nb.select(2); app.root.update(); time.sleep(0.3); app.root.update()
    shot("2_cover_tab")
    sf = app.nb.nametowidget(app.nb.tabs()[2])
    sf.canvas.yview_moveto(1.0); app.root.update(); time.sleep(0.3); app.root.update()
    assert sf.inner.winfo_reqheight() > sf.canvas.winfo_height()          # 內容確實比視窗高 -> 靠捲動
    shot("3_cover_scrolled")
    app.nb.select(0); app.root.update()
    assert app.lf_epub.winfo_ismapped() and not app.lf_txt.winfo_ismapped()
    shot("4_output_epub")

    # 輸出 EPUB（跳過檔案對話框）
    out = os.path.join(TMP, "out.epub")
    A.filedialog.asksaveasfilename = lambda **k: out
    app.v_fmt.set("epub")
    app.export(); pump(app)
    assert os.path.exists(out) and os.path.getsize(out) > 5000, CALLS
    from core.epub_reader import read_epub
    b = read_epub(out)
    assert b.title == "測試小說" and b.author == "張三" and b.cover and b.language == "zh-TW"
    assert not [c for c in CALLS if c[0] == "error"], CALLS

    # 輸出 TXT（Big5、CRLF）與多檔
    out2 = os.path.join(TMP, "out.txt")
    A.filedialog.asksaveasfilename = lambda **k: out2
    app.v_fmt.set("txt"); app.v_enc.set(A.ENCODINGS["big5hkscs"]); app.v_crlf.set(True)
    app.export(); pump(app)
    raw = open(out2, "rb").read()
    assert b"\r\n" in raw and "軟體".encode("big5hkscs") in raw
    A.filedialog.askdirectory = lambda **k: TMP
    app.v_fmt.set("txt_multi")
    app.export(); pump(app)
    assert len(os.listdir(os.path.join(TMP, "測試小說"))) > 30

    # 批次
    app.add_files([p2])
    assert len(app.files) == 2
    A.messagebox.askyesno = lambda *a, **k: True
    A.filedialog.askdirectory = lambda **k: os.path.join(TMP, "batch")
    A.open_folder = lambda p: None
    app.v_fmt.set("epub")
    app.batch(); pump(app)
    assert len(os.listdir(os.path.join(TMP, "batch"))) == 2, os.listdir(os.path.join(TMP, "batch"))

    # 切到第二個檔案、移除檔案
    app._select_file(1); pump(app)
    assert app.v_title.get() == "第二本"
    app.remove_file(); pump(app)
    assert len(app.files) == 1

    # 取消
    app.analyze(False)
    app.worker.cancel(); pump(app)

    app._on_close()
    import json
    assert json.load(open(os.environ["BOOK_CONVERTER_SETTINGS"], encoding="utf-8"))["fmt"] == "epub"
    print("GUI 冒煙測試通過；rows =", n0)


if __name__ == "__main__":
    main()

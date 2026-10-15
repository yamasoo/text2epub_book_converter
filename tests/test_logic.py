"""章節編輯與背景工作流程（不需要 Tk）。執行：python tests/test_logic.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import editing as ed
from core.api import CoverStyle, SplitOptions, TxtOptions
from core.models import Chapter
from gui.jobs import Cancelled, JobConfig, NullCtx, batch_job, export_job, load_book
from gui import settings

PAD = "他走进了软件公司，看到内存里的数据发生了变化，后来决定离开。" * 3
SAMPLE = f"书名\n作者：张三\n第一卷 风起\n第一章 初入\n软件内容甲。{PAD}\n第二章 后来\n内容乙。{PAD}\n番外 小故事\n番外内容。\n"


def _chs():
    return [Chapter("卷", []), Chapter("一", ["a", "b", "c"], "<p>a</p>"), Chapter("二", ["d"], "<p>d</p>"),
            Chapter("三", ["e"])]


def test_merge():
    c = _chs()
    new, sel = ed.merge(c, [0])                       # 空的卷標題併入下一章
    assert new[0].title == "卷 一" and new[0].paragraphs == ["a", "b", "c"] and len(new) == 3 and sel == 0
    new, _ = ed.merge(c, [1, 2])                      # 內容併入，後章標題保留成一行
    assert new[1].paragraphs == ["a", "b", "c", "二", "d"] and new[1].html == "<p>a</p><p>d</p>"
    new, _ = ed.merge(c, [2, 3])
    assert new[2].html is None                        # 有一章沒 html -> 改用純文字
    for bad in ([0, 2], [3], []):
        try:
            ed.merge(c, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    assert len(c) == 4 and c[1].title == "一"          # 原列表不變（復原用）


def test_split_delete_rename_skip():
    c = _chs()
    new, sel = ed.split_at(c, 1, 2, "新章")
    assert [x.title for x in new] == ["卷", "一", "新章", "二", "三"] and new[2].paragraphs == ["c"] and sel == 2
    assert new[1].html is None
    for k in (0, 3):
        try:
            ed.split_at(c, 1, k)
        except ValueError:
            pass
        else:
            raise AssertionError(k)
    assert [x.title for x in ed.delete(c, [0, 2])[0]] == ["一", "三"]
    assert ed.rename(c, 3, " 新名 ")[0][3].title == "新名" and c[3].title == "三"
    new, _ = ed.set_skip(c, [1, 2])
    assert new[1].skip and new[2].skip and not new[3].skip
    new, _ = ed.set_skip(new, [1], False)
    assert not new[1].skip


def test_jobs_load_export_txt_epub():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "测试 作者：张三.txt")
    open(p, "w", encoding="gbk").write(SAMPLE)
    cfg = JobConfig(mode="s2twp", fmt="txt", txt=TxtOptions())
    book, info = load_book(p, cfg)
    assert info["type"] == "TXT" and info["hint"] == "sc" and info["encoding"] == "gb18030"
    assert [c.title for c in book.chapters if not c.skip][:2] == ["第一卷 风起", "第一章 初入"]
    out = os.path.join(d, "o.txt")
    export_job(book, cfg, out)
    t = open(out, encoding="utf-8").read()
    assert "軟體內容甲" in t and t.startswith("测试") is False
    cfg.fmt = "epub"
    out2 = os.path.join(d, "o.epub")
    export_job(book, cfg, out2, title="自訂書名")
    from core.epub_reader import read_epub
    b = read_epub(out2)
    assert b.title == "自訂書名" and b.cover and b.language == "zh-TW"
    # 不轉換 + 簡體來源 -> 語言以 hint 判斷
    cfg2 = JobConfig(mode="keep", fmt="epub", script_hint=info["hint"])
    export_job(book, cfg2, os.path.join(d, "k.epub"))
    assert read_epub(os.path.join(d, "k.epub")).language == "zh-CN"


def test_batch_and_cancel():
    d = tempfile.mkdtemp()
    files = []
    for i in range(3):
        p = os.path.join(d, f"书{i}.txt")
        open(p, "w", encoding="utf-8").write(SAMPLE)
        files.append(p)
    bad = os.path.join(d, "x.epub")
    open(bad, "wb").write(b"not a zip")
    cfg = JobConfig(mode="s2twp", fmt="epub")
    logs = []

    class C(NullCtx):
        def log(self, m): logs.append(m)

    ok, failed = batch_job(files + [bad], os.path.join(d, "out"), cfg, C())
    assert len(ok) == 3 and len(failed) == 1 and all(os.path.exists(x) for x in ok)
    ok2, _ = batch_job(files[:1], os.path.join(d, "out"), cfg, C())      # 同名不覆蓋
    assert os.path.basename(ok2[0]) == "書0_2.epub", ok2

    class Cancel(NullCtx):
        def check(self): raise Cancelled()

    try:
        batch_job(files, os.path.join(d, "out2"), cfg, Cancel())
    except Cancelled:
        pass
    else:
        raise AssertionError


def test_settings_roundtrip():
    p = os.path.join(tempfile.mkdtemp(), "s", "settings.json")
    os.environ["BOOK_CONVERTER_SETTINGS"] = p
    assert settings.load() == {}
    settings.save({"mode": "t2s", "prefixes": "卷\n部"})
    assert settings.load()["prefixes"] == "卷\n部"
    open(p, "w").write("{壞掉")
    assert settings.load() == {}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK ", name)

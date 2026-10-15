"""對話框。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class SplitDialog(tk.Toplevel):
    """選擇「從哪一段開始是新章節」。result = (段落索引, 新標題) 或 None。"""

    def __init__(self, parent, chapter_title: str, paragraphs: list[str], has_html: bool = False):
        super().__init__(parent)
        self.title(f"拆分章節：{chapter_title}")
        self.transient(parent)
        self.resizable(True, True)
        self.result = None
        self._paras = paragraphs

        ttk.Label(self, text="點選「新章節的第一段」：").pack(anchor="w", padx=10, pady=(10, 4))
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=10)
        self.lb = tk.Listbox(box, width=90, height=18, activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(box, command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for i, p in enumerate(paragraphs):
            self.lb.insert("end", f"{i + 1:>4}  {p[:80]}{'…' if len(p) > 80 else ''}")
        self.lb.bind("<<ListboxSelect>>", self._on_select)
        self.lb.bind("<Double-Button-1>", lambda e: self._ok())

        row = ttk.Frame(self)
        row.pack(fill="x", padx=10, pady=8)
        ttk.Label(row, text="新章節標題：").pack(side="left")
        self.v_title = tk.StringVar()
        ttk.Entry(row, textvariable=self.v_title).pack(side="left", fill="x", expand=True)

        if has_html:
            ttk.Label(self, foreground="#b45309", wraplength=560,
                      text="注意：EPUB 原書章節拆分後會改由純文字產生，該章內的圖片與版面會捨棄。"
                      ).pack(anchor="w", padx=10)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=10)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="拆分", command=self._ok).pack(side="right", padx=6)

        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.lb.focus_set()
        self.wait_visibility()
        self.wait_window(self)

    def _on_select(self, _e=None):
        sel = self.lb.curselection()
        if sel:
            p = self._paras[sel[0]]
            if len(p) <= 40:
                self.v_title.set(p)

    def _ok(self):
        sel = self.lb.curselection()
        if not sel:
            return
        self.result = (sel[0], self.v_title.get())
        self.destroy()

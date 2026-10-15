"""背景執行緒：工作在子執行緒跑，結果透過 queue 回到 Tk 主執行緒處理。"""
from __future__ import annotations

import queue
import threading
import traceback

from .jobs import Cancelled


class _Ctx:
    def __init__(self, q: queue.Queue, cancel: threading.Event):
        self._q, self._cancel = q, cancel
        self._last = (-1.0, "")

    def progress(self, frac: float, text: str = ""):
        if abs(frac - self._last[0]) >= 0.004 or text != self._last[1]:   # 避免訊息洪水
            self._last = (frac, text)
            self._q.put(("progress", frac, text))

    def log(self, msg: str):
        self._q.put(("log", msg))

    def check(self):
        if self._cancel.is_set():
            raise Cancelled()


class Worker:
    """同一時間只跑一個工作。四個 handler 都在主執行緒被呼叫。"""

    def __init__(self, root, on_progress, on_log, on_done, on_error, on_cancel):
        self.root = root
        self.h = dict(progress=on_progress, log=on_log, done=on_done, error=on_error, cancelled=on_cancel)
        self.busy = False
        self._cancel = threading.Event()
        self._q: queue.Queue = queue.Queue()

    def start(self, fn, *args, tag=None):
        if self.busy:
            raise RuntimeError("已有工作進行中")
        self.busy = True
        self._cancel.clear()
        self._q = q = queue.Queue()
        ctx = _Ctx(q, self._cancel)

        def target():
            try:
                q.put(("done", fn(ctx, *args), tag))
            except Cancelled:
                q.put(("cancelled", None, tag))
            except Exception as e:
                q.put(("error", e, traceback.format_exc(), tag))

        threading.Thread(target=target, daemon=True).start()
        self.root.after(60, self._poll)

    def cancel(self):
        self._cancel.set()

    def _poll(self):
        finished = False
        try:
            while True:
                msg = self._q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self.h["progress"](msg[1], msg[2])
                elif kind == "log":
                    self.h["log"](msg[1])
                elif kind == "done":
                    self.busy = False
                    self.h["done"](msg[1], msg[2])
                    finished = True
                elif kind == "cancelled":
                    self.busy = False
                    self.h["cancelled"](msg[2])
                    finished = True
                elif kind == "error":
                    self.busy = False
                    self.h["error"](msg[1], msg[2], msg[3])
                    finished = True
        except queue.Empty:
            pass
        if not finished and self.busy:
            self.root.after(60, self._poll)

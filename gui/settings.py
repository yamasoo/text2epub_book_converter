"""記住上次的設定（JSON，放在使用者家目錄）。"""
from __future__ import annotations

import json
import os
from pathlib import Path


def settings_path() -> Path:
    env = os.environ.get("BOOK_CONVERTER_SETTINGS")
    return Path(env) if env else Path.home() / ".book_converter" / "settings.json"


def load() -> dict:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(data: dict) -> None:
    try:
        p = settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass        # 存不了設定不該讓程式出錯

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path

from .utils import atomic_write_json

logger = logging.getLogger(__name__)

DEFAULT_SYNC = {
    "sleep": 5.0,
    "jitter": 0.2,
    "timeout": 60.0,
    "connect_timeout": 15.0,
    "retries": 3,
    "download_concurrency": 4,
    "export_json": True,
    "avatar_sleep": 1.5,
    "max_pages": 0,
    "max_images": 0,
}

DEFAULT_WEB = {
    "host": "127.0.0.1",
    "port": 5000,
    "page_size": 24,
    "thumbnail_width": 400,
    "auto_open": True,
    "debug": False,
}

DEFAULT_COOKIES = {
    "sessdata": "",
    "bili_jct": "",
    "buvid3": "",
    "updated_at": 0,
}


def _clamp(value, low, high, cast=float, default=0):
    try:
        number = cast(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clean_sync(raw: dict) -> dict:
    data = copy.deepcopy(DEFAULT_SYNC)
    data.update(raw if isinstance(raw, dict) else {})
    return {
        "sleep": _clamp(data.get("sleep"), 0, 300, float, 5.0),
        "jitter": _clamp(data.get("jitter"), 0, 1, float, 0.2),
        "timeout": _clamp(data.get("timeout"), 5, 600, float, 60.0),
        "connect_timeout": _clamp(data.get("connect_timeout"), 1, 120, float, 15.0),
        "retries": _clamp(data.get("retries"), 0, 10, int, 3),
        "download_concurrency": _clamp(data.get("download_concurrency"), 1, 16, int, 4),
        "export_json": bool(data.get("export_json", True)),
        "avatar_sleep": _clamp(data.get("avatar_sleep"), 0, 30, float, 1.5),
        "max_pages": _clamp(data.get("max_pages"), 0, 10000, int, 0),
        "max_images": _clamp(data.get("max_images"), 0, 1000000, int, 0),
    }


def _clean_web(raw: dict) -> dict:
    data = copy.deepcopy(DEFAULT_WEB)
    data.update(raw if isinstance(raw, dict) else {})
    return {
        "host": "127.0.0.1",
        "port": _clamp(data.get("port"), 1, 65535, int, 5000),
        "page_size": _clamp(data.get("page_size"), 1, 60, int, 24),
        "thumbnail_width": _clamp(data.get("thumbnail_width"), 120, 1200, int, 400),
        "auto_open": bool(data.get("auto_open", True)),
        "debug": bool(data.get("debug", False)),
    }


class SettingsStore:
    """User-owned settings and cookies under <data>/config/."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.config_dir = self.data_dir / "config"
        self.settings_path = self.config_dir / "settings.json"
        self.cookies_path = self.config_dir / "cookies.json"
        self._lock = threading.RLock()
        self.ensure()

    def ensure(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        if not self.settings_path.is_file():
            atomic_write_json(
                self.settings_path,
                {"sync": copy.deepcopy(DEFAULT_SYNC), "web": copy.deepcopy(DEFAULT_WEB)},
            )
        if not self.cookies_path.is_file():
            atomic_write_json(self.cookies_path, copy.deepcopy(DEFAULT_COOKIES))
            self._chmod_private(self.cookies_path)

    @staticmethod
    def _chmod_private(path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _read_json(self, path: Path, fallback: dict) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return copy.deepcopy(fallback)
        except Exception as exc:
            logger.error("读取用户配置失败 %s：%s，已使用默认值", path, exc)
            try:
                path.replace(path.with_name(path.name + f".broken-{int(time.time())}"))
            except OSError:
                pass
            return copy.deepcopy(fallback)
        return data if isinstance(data, dict) else copy.deepcopy(fallback)

    def load(self) -> dict:
        with self._lock:
            raw = self._read_json(self.settings_path, {})
            return {
                "sync": _clean_sync(raw.get("sync") if isinstance(raw, dict) else {}),
                "web": _clean_web(raw.get("web") if isinstance(raw, dict) else {}),
            }

    def sync(self) -> dict:
        return self.load()["sync"]

    def web(self) -> dict:
        return self.load()["web"]

    def cookies(self) -> dict:
        with self._lock:
            raw = self._read_json(self.cookies_path, DEFAULT_COOKIES)
            return {
                "sessdata": str(raw.get("sessdata") or "").strip(),
                "bili_jct": str(raw.get("bili_jct") or "").strip(),
                "buvid3": str(raw.get("buvid3") or "").strip(),
                "updated_at": int(raw.get("updated_at") or 0),
            }

    def has_cookies(self) -> bool:
        cookies = self.cookies()
        return bool(cookies["sessdata"] and cookies["buvid3"])

    def masked_cookies(self) -> dict:
        cookies = self.cookies()
        return {
            "sessdata": bool(cookies["sessdata"]),
            "bili_jct": bool(cookies["bili_jct"]),
            "buvid3": bool(cookies["buvid3"]),
            "updated_at": cookies["updated_at"],
            "configured": bool(cookies["sessdata"] and cookies["buvid3"]),
        }

    def save_cookies(
        self, sessdata: str, bili_jct: str = "", buvid3: str = ""
    ) -> dict:
        with self._lock:
            payload = {
                "sessdata": (sessdata or "").strip(),
                "bili_jct": (bili_jct or "").strip(),
                "buvid3": (buvid3 or "").strip(),
                "updated_at": int(time.time()),
            }
            atomic_write_json(self.cookies_path, payload)
            self._chmod_private(self.cookies_path)
            return payload

    def clear_cookies(self) -> None:
        with self._lock:
            atomic_write_json(self.cookies_path, copy.deepcopy(DEFAULT_COOKIES))
            self._chmod_private(self.cookies_path)

    def save_options(self, sync: dict | None = None, web: dict | None = None) -> dict:
        with self._lock:
            current = self.load()
            if sync is not None:
                current["sync"] = _clean_sync({**current["sync"], **sync})
            if web is not None:
                current["web"] = _clean_web({**current["web"], **web})
            atomic_write_json(self.settings_path, current)
            return current

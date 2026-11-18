from __future__ import annotations

import logging
import os
import shutil
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .settings import SettingsStore

logger = logging.getLogger(__name__)


@dataclass
class GeneralConfig:
    data_dir: Path
    sleep: float = 5.0
    jitter: float = 0.2
    timeout: float = 60.0
    connect_timeout: float = 15.0
    retries: int = 3
    download_concurrency: int = 4
    export_json: bool = True
    avatar_sleep: float = 1.5
    max_pages: int = 0
    max_images: int = 0


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 5000
    page_size: int = 24
    thumbnail_width: int = 400
    auto_open: bool = True
    debug: bool = False


@dataclass
class Config:
    base_dir: Path
    data_dir: Path
    general: GeneralConfig
    web: WebConfig
    settings_store: SettingsStore
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""

    @property
    def config_dir(self) -> Path:
        return self.data_dir / "config"

    @property
    def settings_path(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def cookies_path(self) -> Path:
        return self.config_dir / "cookies.json"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bili.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def avatars_dir(self) -> Path:
        return self.data_dir / "avatars"

    @property
    def thumbnails_dir(self) -> Path:
        return self.data_dir / "thumbnails"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def job_logs_dir(self) -> Path:
        return self.logs_dir / "jobs"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def legacy_dir(self) -> Path:
        return self.data_dir / "legacy"

    @property
    def has_cookie(self) -> bool:
        return bool(self.sessdata and self.buvid3)

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.config_dir,
            self.images_dir,
            self.avatars_dir,
            self.thumbnails_dir,
            self.exports_dir,
            self.logs_dir,
            self.job_logs_dir,
            self.backups_dir,
            self.legacy_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def require_cookie(self) -> None:
        if not self.has_cookie:
            raise SystemExit(
                "还没有配置 B 站 Cookie。请启动 WebUI，在“设置”页面填写，"
                "或直接编辑 data/config/cookies.json。"
            )


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _move_legacy(path: Path, destination_dir: Path, new_name: str) -> Path | None:
    if not path.exists():
        return None
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / new_name
    if target.exists():
        target = destination_dir / f"{target.stem}-{int(time.time())}{target.suffix}"
    try:
        shutil.move(str(path), str(target))
        logger.warning("旧用户数据已移动到：%s", target)
        return target
    except OSError as exc:
        logger.error("移动旧文件失败 %s：%s", path, exc)
        return None


def _import_legacy_config(path: Path) -> tuple[dict, dict]:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("读取旧 config.toml 失败：%s", exc)
        return {}, {}
    general = raw.get("general") or {}
    web = raw.get("web") or {}
    sync_keys = (
        "sleep", "jitter", "timeout", "connect_timeout", "retries",
        "download_concurrency", "export_json", "avatar_sleep", "max_pages", "max_images",
    )
    web_keys = ("port", "page_size", "thumbnail_width", "auto_open", "debug")
    return (
        {key: general[key] for key in sync_keys if key in general},
        {key: web[key] for key in web_keys if key in web},
    )


def load_config(
    base_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
    sleep: float | None = None,
    max_pages: int | None = None,
    max_images: int | None = None,
    host: str | None = None,
    port: int | None = None,
    debug: bool | None = None,
    auto_open: bool | None = None,
    config_path: str | Path | None = None,
    user_list: str | Path | None = None,
) -> Config:
    """Load user data from <data>/config, importing legacy root files once."""
    root = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parent.parent
    root.mkdir(parents=True, exist_ok=True)

    chosen_data = data_dir or os.environ.get("BILI3_DATA_DIR") or "data"
    data_path = Path(chosen_data)
    if not data_path.is_absolute():
        data_path = (root / data_path).resolve()

    settings_path = data_path / "config" / "settings.json"
    cookies_path = data_path / "config" / "cookies.json"
    had_settings = settings_path.is_file()
    had_cookies = cookies_path.is_file()

    store = SettingsStore(data_path)

    # One-time import of the old root config.toml.
    legacy_config = root / "config.toml"
    if legacy_config.is_file():
        if not had_settings:
            sync_values, web_values = _import_legacy_config(legacy_config)
            if sync_values or web_values:
                store.save_options(sync=sync_values or None, web=web_values or None)
        _move_legacy(legacy_config, data_path / "legacy", "config.toml.migrated")

    # One-time import of the old root .env cookie file.
    legacy_env = root / ".env"
    if legacy_env.is_file():
        if not had_cookies or not store.has_cookies():
            values = _parse_env_file(legacy_env)
            sessdata = values.get("BILI_SESSDATA", "")
            if sessdata:
                store.save_cookies(
                    sessdata,
                    values.get("BILI_JCT", ""),
                    values.get("BILI_BUVID3", ""),
                )
        if store.has_cookies():
            _move_legacy(legacy_env, data_path / "legacy", "env.migrated")
        else:
            logger.warning("没有从根目录 .env 读取到有效 Cookie，暂时保留该文件")

    sync_values = store.sync()
    web_values = store.web()
    cookies = store.cookies()

    general = GeneralConfig(
        data_dir=data_path,
        sleep=float(sync_values["sleep"] if sleep is None else max(0.0, sleep)),
        jitter=float(sync_values["jitter"]),
        timeout=float(sync_values["timeout"]),
        connect_timeout=float(sync_values["connect_timeout"]),
        retries=int(sync_values["retries"]),
        download_concurrency=int(sync_values["download_concurrency"]),
        export_json=bool(sync_values["export_json"]),
        avatar_sleep=float(sync_values["avatar_sleep"]),
        max_pages=max(0, int(sync_values["max_pages"] if max_pages is None else max_pages)),
        max_images=max(0, int(sync_values["max_images"] if max_images is None else max_images)),
    )
    web = WebConfig(
        host=str(host or web_values["host"] or "127.0.0.1"),
        port=int(port or web_values["port"]),
        page_size=max(1, min(60, int(web_values["page_size"]))),
        thumbnail_width=max(120, min(1200, int(web_values["thumbnail_width"]))),
        auto_open=bool(web_values["auto_open"] if auto_open is None else auto_open),
        debug=bool(web_values["debug"] if debug is None else debug),
    )

    config = Config(
        base_dir=root,
        data_dir=data_path,
        general=general,
        web=web,
        settings_store=store,
        sessdata=cookies["sessdata"],
        bili_jct=cookies["bili_jct"],
        buvid3=cookies["buvid3"],
    )
    config.ensure_dirs()
    return config

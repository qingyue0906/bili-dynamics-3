from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = """
[general]
data_dir = "data"
user_list = "user_list.txt"
sleep = 5.0
jitter = 0.2
timeout = 60
connect_timeout = 15
retries = 3
download_concurrency = 4
export_json = true
max_pages = 0
max_images = 0

[web]
host = "127.0.0.1"
port = 5000
page_size = 24
thumbnail_width = 400
debug = false
"""


@dataclass
class GeneralConfig:
    data_dir: Path
    user_list: Path
    sleep: float = 5.0
    jitter: float = 0.2
    timeout: float = 60.0
    connect_timeout: float = 15.0
    retries: int = 3
    download_concurrency: int = 4
    export_json: bool = True
    max_pages: int = 0
    max_images: int = 0


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 5000
    page_size: int = 24
    thumbnail_width: int = 400
    debug: bool = False


@dataclass
class Config:
    base_dir: Path
    data_dir: Path
    general: GeneralConfig
    web: WebConfig
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bili.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

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
    def has_cookie(self) -> bool:
        return bool(self.sessdata and self.buvid3)

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.images_dir,
            self.thumbnails_dir,
            self.exports_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def require_cookie(self) -> None:
        if not self.has_cookie:
            raise SystemExit(
                "缺少 B 站 Cookie。请在项目根目录的 .env 中填写 "
                "BILI_SESSDATA / BILI_JCT / BILI_BUVID3，参考 .env.example。"
            )


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(
    base_dir: str | Path | None = None,
    config_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    user_list: str | Path | None = None,
    sleep: float | None = None,
    max_pages: int | None = None,
    max_images: int | None = None,
    host: str | None = None,
    port: int | None = None,
    debug: bool | None = None,
) -> Config:
    root = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parent.parent
    root.mkdir(parents=True, exist_ok=True)

    _load_env_file(root / ".env")

    raw = tomllib.loads(DEFAULT_CONFIG)
    cfg_file = Path(config_path) if config_path else root / "config.toml"
    if cfg_file.is_file():
        raw.update(tomllib.loads(cfg_file.read_text(encoding="utf-8")))

    general_raw = raw.get("general") or {}
    web_raw = raw.get("web") or {}

    def resolve(value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (root / path).resolve()

    chosen_data = data_dir if data_dir is not None else general_raw.get("data_dir", "data")
    chosen_list = user_list if user_list is not None else general_raw.get("user_list", "user_list.txt")

    general = GeneralConfig(
        data_dir=resolve(chosen_data),
        user_list=resolve(chosen_list),
        sleep=float(general_raw.get("sleep", 5.0) if sleep is None else sleep),
        jitter=max(0.0, float(general_raw.get("jitter", 0.2))),
        timeout=float(general_raw.get("timeout", 60.0)),
        connect_timeout=float(general_raw.get("connect_timeout", 15.0)),
        retries=max(0, int(general_raw.get("retries", 3))),
        download_concurrency=max(1, int(general_raw.get("download_concurrency", 4))),
        export_json=bool(general_raw.get("export_json", True)),
        max_pages=max(0, int(general_raw.get("max_pages", 0) if max_pages is None else max_pages)),
        max_images=max(0, int(general_raw.get("max_images", 0) if max_images is None else max_images)),
    )

    web = WebConfig(
        host=str(web_raw.get("host", "127.0.0.1") if host is None else host),
        port=int(web_raw.get("port", 5000) if port is None else port),
        page_size=max(1, min(60, int(web_raw.get("page_size", 24)))),
        thumbnail_width=max(120, min(1200, int(web_raw.get("thumbnail_width", 400)))),
        debug=bool(web_raw.get("debug", False) if debug is None else debug),
    )

    config = Config(
        base_dir=root,
        data_dir=general.data_dir,
        general=general,
        web=web,
        sessdata=os.environ.get("BILI_SESSDATA", "").strip(),
        bili_jct=os.environ.get("BILI_JCT", "").strip(),
        buvid3=os.environ.get("BILI_BUVID3", "").strip(),
    )
    config.ensure_dirs()
    return config

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

_WINDOWS_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TIME_RE = re.compile(
    r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?"
)


def to_int(value, default: int = 0) -> int:
    """Best-effort integer conversion that never raises."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def as_list(value) -> list:
    return value if isinstance(value, list) else []


def strip_query(url: str) -> str:
    """Remove query and fragment from a URL."""
    if not url:
        return ""
    return url.split("?", 1)[0].split("#", 1)[0]


def normalize_download_url(url: str) -> str:
    """Return an https URL without query/fragment for image downloads."""
    clean = strip_query(url)
    if not clean:
        return ""
    if clean.startswith("//"):
        clean = "https:" + clean
    elif clean.startswith("http://"):
        clean = "https://" + clean[7:]
    elif not clean.startswith(("http://", "https://")):
        clean = "https://" + clean
    return clean


def sanitize_filename(name: str, fallback: str = "image.jpg") -> str:
    """Make a single path component safe for Windows/NTFS."""
    name = unquote(name or "")
    name = _WINDOWS_BAD.sub("_", name).strip().strip(".")
    if not name:
        return fallback
    if len(name) > 180:
        stem, ext = os.path.splitext(name)
        name = stem[:170] + ext
    # Windows reserved device names (unlikely for image hashes, but cheap).
    if name.upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = "_" + name
    return name


def filename_from_url(url: str) -> str:
    """Derive the historical bili-dynamics filename from an image URL."""
    clean = strip_query(url)
    path = urlsplit(clean).path
    name = posixpath.basename(path)
    name = sanitize_filename(name, fallback="")
    if not name:
        digest = hashlib.sha1((url or "").encode("utf-8", "replace")).hexdigest()[:16]
        name = digest + ".jpg"
    if not os.path.splitext(name)[1]:
        name += ".jpg"
    return name


def parse_pub_time(value) -> int:
    """Parse bilibili's human-readable pub_time or an epoch value."""
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    match = _TIME_RE.search(text)
    if not match:
        return 0
    year, month, day, hour, minute, second = match.groups()
    try:
        return int(
            datetime(
                int(year),
                int(month),
                int(day),
                int(hour or 0),
                int(minute or 0),
                int(second or 0),
            ).timestamp()
        )
    except (TypeError, ValueError):
        return 0


def format_ts(ts: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime(fmt)
    except (OverflowError, OSError, ValueError):
        return ""


def slugify_name(name: str, max_len: int = 60) -> str:
    name = _WINDOWS_BAD.sub("_", (name or "").strip()).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "user"


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding=encoding, newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, data) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def resolve_image_path(data_dir: Path, image_dir: str, file_name: str) -> Path:
    """Resolve a stored image path and refuse path traversal."""
    base = Path(image_dir)
    if not base.is_absolute():
        base = Path(data_dir) / base
    base = base.resolve()
    target = (base / file_name).resolve()
    if not is_within(base, target):
        raise ValueError(f"非法图片路径：{target}")
    return target


def human_size(num_bytes: int) -> str:
    value = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"

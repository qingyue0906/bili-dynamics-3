from __future__ import annotations

import hashlib
import logging
import os

from .models import ImageRecord, PostRecord
from .utils import (
    as_dict,
    as_list,
    filename_from_url,
    normalize_download_url,
    parse_pub_time,
    strip_query,
    to_int,
)

logger = logging.getLogger(__name__)

MAJOR_OPUS = "MAJOR_TYPE_OPUS"
MAJOR_DRAW = "MAJOR_TYPE_DRAW"
MAJOR_UPOWER_COMMON = "MAJOR_TYPE_UPOWER_COMMON"
MAJOR_NONE = "MAJOR_TYPE_NONE"

DYNAMIC_FORWARD = "DYNAMIC_TYPE_FORWARD"


def parse_dynamic_id(item: dict) -> int | None:
    """Return a numeric dynamic id or None. Never raises."""
    if not isinstance(item, dict):
        return None
    raw = item.get("id_str")
    if raw in (None, ""):
        raw = item.get("id")
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    if not text.isdigit():
        return None
    value = int(text)
    return value if value > 0 else None


def _remote_size(value) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _make_image(url: str, width=0, height=0, size=None) -> ImageRecord | None:
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    clean = strip_query(url)
    if not clean:
        return None
    return ImageRecord(
        url=url,
        url_clean=normalize_download_url(url),
        file_name=filename_from_url(url),
        width=to_int(width),
        height=to_int(height),
        remote_size=_remote_size(size),
    )


def _dedupe_and_fix_names(images: list[ImageRecord]) -> list[ImageRecord]:
    seen: set[str] = set()
    unique: list[ImageRecord] = []
    for image in images:
        if image.url in seen:
            continue
        seen.add(image.url)
        unique.append(image)

    used: dict[str, str] = {}
    for image in unique:
        key = image.file_name.lower()
        if key in used and used[key] != image.url_clean:
            stem, ext = os.path.splitext(image.file_name)
            digest = hashlib.sha1(image.url.encode("utf-8", "replace")).hexdigest()[:8]
            image.file_name = f"{stem}_{digest}{ext}"
            key = image.file_name.lower()
        used[key] = image.url_clean
    return unique


def _extract_images(pics: list, draw_items: list) -> list[ImageRecord]:
    images: list[ImageRecord] = []
    for pic in pics:
        if not isinstance(pic, dict):
            continue
        image = _make_image(
            pic.get("url") or pic.get("src"),
            pic.get("width"),
            pic.get("height"),
            pic.get("size"),
        )
        if image:
            images.append(image)
    if images:
        return _dedupe_and_fix_names(images)

    for item in draw_items:
        if not isinstance(item, dict):
            continue
        image = _make_image(
            item.get("src") or item.get("url"),
            item.get("width"),
            item.get("height"),
            item.get("size"),
        )
        if image:
            images.append(image)
    return _dedupe_and_fix_names(images)


def _extract_post(item: dict, default_uid: int | None, include_forwards: bool) -> PostRecord | None:
    dynamic_id = parse_dynamic_id(item)
    if dynamic_id is None:
        return None

    modules = as_dict(item.get("modules"))
    author = as_dict(modules.get("module_author"))
    dynamic = as_dict(modules.get("module_dynamic"))
    major = as_dict(dynamic.get("major"))
    opus = as_dict(major.get("opus"))
    draw = as_dict(major.get("draw"))
    upower = as_dict(major.get("upower_common"))
    desc = as_dict(dynamic.get("desc"))
    summary = as_dict(opus.get("summary"))

    major_type = str(major.get("type") or "")
    if not major_type:
        if opus:
            major_type = MAJOR_OPUS
        elif draw:
            major_type = MAJOR_DRAW

    images = _extract_images(as_list(opus.get("pics")), as_list(draw.get("items")))

    uid = to_int(author.get("mid") or author.get("uid"), default=0)
    if not uid:
        uid = to_int(default_uid, default=0)

    author_name = str(author.get("name") or "")
    author_avatar = str(author.get("face") or "")

    # Forwarded dynamics have module_dynamic.major == null. We skip them by
    # default; optionally borrow the original content.
    if not major and not images:
        if include_forwards:
            orig = item.get("orig")
            if isinstance(orig, dict):
                origin = _extract_post(orig, default_uid, include_forwards=False)
                if origin and (origin.images or origin.is_charge):
                    origin.dynamic_id = str(dynamic_id)
                    origin.dynamic_type = str(item.get("type") or DYNAMIC_FORWARD)
                    origin.kind = "forward"
                    origin.is_forward = True
                    origin.uid = uid or origin.uid
                    origin.author_name = author_name or origin.author_name
                    origin.author_avatar = author_avatar or origin.author_avatar
                    origin.published_at = parse_pub_time(author.get("pub_ts")) or parse_pub_time(
                        author.get("pub_time")
                    )
                    return origin
        return None

    if not images and major_type != MAJOR_UPOWER_COMMON and not upower:
        # Text-only / video / article / invalid dynamic: not a gallery post.
        return None

    if not uid:
        return None

    published_at = to_int(author.get("pub_ts"), default=0)
    if not published_at:
        published_at = parse_pub_time(author.get("pub_time"))

    title = str(opus.get("title") or "")
    description = str(summary.get("text") or desc.get("text") or "").strip()
    is_charge = major_type == MAJOR_UPOWER_COMMON or bool(upower and not images)
    if is_charge:
        title = title or str(upower.get("title") or "")
        description = description or str(upower.get("title_prefix") or "")

    if major_type == MAJOR_DRAW or (draw and not opus):
        kind = "draw"
    elif major_type == MAJOR_OPUS or opus:
        kind = "opus"
    elif is_charge:
        kind = "charge"
    else:
        kind = "other"

    return PostRecord(
        dynamic_id=str(dynamic_id),
        uid=uid,
        dynamic_type=str(item.get("type") or ""),
        major_type=major_type,
        kind=kind,
        title=title,
        description=description,
        published_at=published_at,
        is_charge=is_charge,
        is_accessible=not is_charge,
        images=images,
        author_name=author_name,
        author_avatar=author_avatar,
    )


def extract_post(
    item: dict,
    default_uid: int | None = None,
    include_forwards: bool = False,
) -> PostRecord | None:
    """Extract one dynamic into a PostRecord.

    This is intentionally defensive: bilibili changes module shapes often and
    older dynamics use `major.draw` instead of `major.opus`. A single malformed
    item must never abort a whole sync.
    """
    try:
        return _extract_post(item, default_uid, include_forwards)
    except Exception:
        try:
            dynamic_id = item.get("id_str") if isinstance(item, dict) else "?"
        except Exception:
            dynamic_id = "?"
        logger.warning("解析动态失败 id=%s，已跳过", dynamic_id, exc_info=True)
        return None

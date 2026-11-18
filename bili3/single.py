from __future__ import annotations

import logging
import re
from typing import Callable

from .avatars import download_avatar
from .bilibili import BiliClient
from .config import Config
from .db import Database
from .download import Downloader
from .export import export_user
from .extract import extract_post
from .models import PostRecord
from .sync import ensure_user

logger = logging.getLogger(__name__)

_URL_PATTERNS = (
    re.compile(r"t\.bilibili\.com/(\d{8,})", re.I),
    re.compile(r"bilibili\.com/opus/(\d{8,})", re.I),
    re.compile(r"bilibili\.com/dynamic/(\d{8,})", re.I),
)
_NUMBER = re.compile(r"^\s*(\d{8,})\s*$")
_UID_NUMBER = re.compile(r"^\s*(\d+)\s*$")


def parse_dynamic_input(text: str) -> int | None:
    value = (text or "").strip()
    if not value:
        return None
    match = _NUMBER.match(value)
    if match:
        return int(match.group(1))
    for pattern in _URL_PATTERNS:
        match = pattern.search(value)
        if match:
            return int(match.group(1))
    return None


def parse_uid_input(text: str) -> int | None:
    value = (text or "").strip()
    if not value:
        return None
    match = _UID_NUMBER.match(value)
    if match:
        return int(match.group(1))
    match = re.search(r"space\.bilibili\.com/(\d+)", value, re.I)
    if match:
        return int(match.group(1))
    return None


async def _fetch_post(config: Config, dynamic_id: int) -> PostRecord | None:
    client = BiliClient(config)
    data = await client.fetch_dynamic(dynamic_id)
    item = data.get("item") if isinstance(data, dict) else None
    if item is None and isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, dict):
            item = nested.get("item")
    if not isinstance(item, dict):
        raise ValueError("接口没有返回动态详情")
    return extract_post(item, include_forwards=True)


async def preview_dynamic(config: Config, text: str) -> dict:
    dynamic_id = parse_dynamic_input(text)
    if dynamic_id is None:
        raise ValueError("无法识别动态 ID。请输入 t.bilibili.com/...、opus/... 或纯数字 ID")
    post = await _fetch_post(config, dynamic_id)
    if post is None:
        raise ValueError("这条动态不是图文动态，或者没有可下载的图片")
    return {
        "dynamic_id": post.dynamic_id,
        "uid": post.uid,
        "author": post.author_name,
        "title": post.title,
        "description": post.description,
        "image_count": len(post.images),
        "is_charge": post.is_charge,
        "kind": post.kind,
        "images": [
            {"url": image.url, "width": image.width, "height": image.height}
            for image in post.images[:8]
        ],
        "bili_url": f"https://t.bilibili.com/{post.dynamic_id}",
    }


async def download_dynamic(
    db: Database,
    config: Config,
    text: str,
    add_user: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> dict:
    dynamic_id = parse_dynamic_input(text)
    if dynamic_id is None:
        raise ValueError("无法识别动态 ID")
    post = await _fetch_post(config, dynamic_id)
    if post is None:
        raise ValueError("这条动态不是图文动态，或者没有可下载的图片")
    if not post.uid:
        raise ValueError("动态缺少作者 UID")

    existing = db.get_user(post.uid)
    user = ensure_user(db, config, post.uid, post.author_name)
    if existing is None:
        db.set_user_enabled(post.uid, bool(add_user))
    elif add_user and not user["enabled"]:
        db.set_user_enabled(post.uid, True)

    image_dir = str(user["image_dir"])
    with db.transaction():
        post_id = db.upsert_post(post)
        db.upsert_images(post_id, post.images)

    if post.author_avatar:
        try:
            await download_avatar(db, config, post.uid, post.author_avatar)
        except Exception:
            logger.warning("指定动态头像更新失败 uid=%s", post.uid, exc_info=True)
    if config.general.export_json:
        export_user(db, config, post.uid)

    rows = db.get_images_for_post(post_id)
    total = len(rows)
    if not rows:
        message = "图片已全部存在"
        if post.is_charge and not post.images:
            message = "充电专属动态，当前 Cookie 无权限读取图片"
        return {
            "dynamic_id": post.dynamic_id,
            "uid": post.uid,
            "image_count": len(post.images),
            "downloaded": 0,
            "message": message,
        }

    async with Downloader(db, config) as downloader:
        stats = await downloader.download_rows(
            rows,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
    return {
        "dynamic_id": post.dynamic_id,
        "uid": post.uid,
        "image_count": len(post.images),
        "downloaded": stats.ok,
        "skipped": stats.skipped,
        "failed": stats.failed,
        "dead": stats.dead,
        "restricted": stats.restricted,
        "message": f"已下载 {stats.ok} 张，跳过 {stats.skipped} 张，失败 {stats.failed} 张",
    }

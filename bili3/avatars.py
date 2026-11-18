from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Callable

import aiohttp
from PIL import Image, ImageOps

from .bilibili import BiliClient
from .config import Config
from .db import Database
from .utils import strip_query

logger = logging.getLogger(__name__)

AVATAR_SIZE = 160
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
    ),
    "Referer": "https://www.bilibili.com/",
}


def avatar_path(config: Config, avatar_file: str) -> Path:
    return config.avatars_dir / Path(avatar_file).name


async def download_avatar(
    db: Database,
    config: Config,
    uid: int,
    url: str,
    force: bool = False,
) -> Path | None:
    url = strip_query(url or "")
    if not url:
        return None
    user = db.get_user(uid)
    if not user:
        return None
    existing = str(user["avatar_file"] or "")
    if existing and not force and str(user["avatar_source"] or "") == url:
        path = avatar_path(config, existing)
        if path.is_file() and path.stat().st_size > 0:
            return path

    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    file_name = f"{int(uid)}_{digest}.webp"
    target = config.avatars_dir / file_name
    tmp = target.with_name(target.name + ".part")
    timeout = aiohttp.ClientTimeout(total=60, connect=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning("头像下载失败 uid=%s HTTP %s", uid, response.status)
                    return None
                payload = await response.read()
        if not payload:
            logger.warning("头像为空 uid=%s", uid)
            return None
        with Image.open(BytesIO(payload)) as image:
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGB")
            image = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
            image.save(tmp, "WEBP", quality=88, method=4)
        tmp.replace(target)
        db.set_user_avatar(uid, file_name, url, int(time.time()))
        logger.debug("头像已更新 uid=%s %s", uid, target)
        return target
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("头像处理失败 uid=%s：%s", uid, exc)
        return None
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


async def refresh_avatars(
    db: Database,
    config: Config,
    uids: list[int] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> dict[str, int]:
    users = db.list_users_with_counts()
    if uids:
        wanted = {int(uid) for uid in uids}
        users = [user for user in users if int(user["uid"]) in wanted]
    total = len(users)
    stats = {"ok": 0, "skipped": 0, "failed": 0, "total": total}
    client = BiliClient(config) if config.has_cookie else None

    for index, user in enumerate(users, 1):
        if cancel_check:
            cancel_check()
        uid = int(user["uid"])
        url = str(user["avatar_url"] or "")
        called_api = False
        if not url and client is not None:
            info = await client.fetch_user_info(uid)
            called_api = True
            name = str(info.get("name") or "")
            url = str(info.get("face") or "")
            if name or url:
                db.upsert_user(
                    uid,
                    name=name or user["name"],
                    avatar_url=url,
                    image_dir=user["image_dir"],
                )
        if url:
            path = await download_avatar(db, config, uid, url)
            if path:
                stats["ok"] += 1
            else:
                stats["failed"] += 1
        else:
            stats["skipped"] += 1
        if on_progress:
            on_progress(index, total)
        if called_api and config.general.avatar_sleep > 0 and index < total:
            await asyncio.sleep(config.general.avatar_sleep)
    logger.info(
        "头像补全完成：成功 %d，跳过 %d，失败 %d，共 %d",
        stats["ok"],
        stats["skipped"],
        stats["failed"],
        total,
    )
    return stats

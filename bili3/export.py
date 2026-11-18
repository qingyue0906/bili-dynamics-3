from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .db import Database
from .models import ImageRecord, PostRecord
from .utils import atomic_write_json, slugify_name

logger = logging.getLogger(__name__)

POST_FIELDS = (
    "dynamic_id",
    "uid",
    "dynamic_type",
    "major_type",
    "kind",
    "title",
    "description",
    "published_at",
    "is_charge",
    "is_accessible",
    "image_count",
)
IMAGE_FIELDS = (
    "url",
    "url_clean",
    "file_name",
    "width",
    "height",
    "remote_size",
    "local_size",
    "status",
    "http_status",
)


def export_user(db: Database, config: Config, uid: int) -> Path | None:
    data = db.export_user_data(uid)
    if not data:
        return None
    user = data["user"]
    payload = {
        "schema": 3,
        "exported_at": int(time.time()),
        "user": {
            "uid": user.get("uid"),
            "name": user.get("name"),
            "avatar_url": user.get("avatar_url"),
            "image_dir": user.get("image_dir"),
            "last_dynamic_id": user.get("last_dynamic_id"),
            "last_sync_at": user.get("last_sync_at"),
        },
        "posts": [],
    }
    for post in data["posts"]:
        post_item = {key: post.get(key) for key in POST_FIELDS}
        post_item["images"] = [
            {key: image.get(key) for key in IMAGE_FIELDS}
            for image in post.get("images", [])
        ]
        payload["posts"].append(post_item)

    path = config.exports_dir / f"{int(uid)}_{slugify_name(user.get('name') or str(uid))}.json"
    atomic_write_json(path, payload)
    logger.debug("已导出 %s", path)
    return path


def export_all(db: Database, config: Config) -> list[Path]:
    paths = []
    for user in db.list_users_with_counts():
        path = export_user(db, config, int(user["uid"]))
        if path:
            paths.append(path)
    return paths


@dataclass
class ImportStats:
    users: int = 0
    posts: int = 0
    images: int = 0
    skipped: int = 0


def import_exports(
    db: Database,
    config: Config,
    directory: Path | None = None,
    dry_run: bool = False,
) -> ImportStats:
    """Rebuild the SQLite database from v3 JSON exports."""
    source = Path(directory) if directory else config.exports_dir
    stats = ImportStats()
    if not source.is_dir():
        raise SystemExit(f"导出目录不存在：{source}")

    for path in sorted(source.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("读取导出失败 %s：%s", path, exc)
            stats.skipped += 1
            continue
        user = data.get("user") or {}
        uid_raw = user.get("uid")
        if not str(uid_raw or "").isdigit():
            logger.warning("导出缺少 uid，跳过：%s", path)
            stats.skipped += 1
            continue
        uid = int(uid_raw)
        name = str(user.get("name") or uid)
        image_dir = str(user.get("image_dir") or "")
        if not image_dir:
            image_dir = str(Path("images") / f"{uid}_{slugify_name(name)}")
        if not dry_run:
            db.upsert_user(
                uid,
                name=name,
                avatar_url=str(user.get("avatar_url") or ""),
                image_dir=image_dir,
            )
        max_id = 0
        posts = data.get("posts")
        posts = posts if isinstance(posts, list) else []
        try:
            if not dry_run:
                with db.transaction():
                    for item in posts:
                        post = _export_post_to_record(item, uid)
                        if post is None:
                            continue
                        post_id = db.upsert_post(post)
                        db.upsert_images(post_id, post.images)
                        stats.posts += 1
                        stats.images += len(post.images)
                        max_id = max(max_id, int(post.dynamic_id))
                    if max_id:
                        db.set_last_dynamic_id(uid, max_id, synced_at=user.get("last_sync_at"))
            else:
                for item in posts:
                    post = _export_post_to_record(item, uid)
                    if post is None:
                        continue
                    stats.posts += 1
                    stats.images += len(post.images)
            stats.users += 1
        except Exception as exc:
            logger.exception("导入用户 %s 失败：%s", uid, exc)
            stats.skipped += 1
    logger.info(
        "导入%s：用户 %d，动态 %d，图片 %d，跳过 %d",
        "预演" if dry_run else "完成",
        stats.users,
        stats.posts,
        stats.images,
        stats.skipped,
    )
    return stats


def _export_post_to_record(item: dict, uid: int) -> PostRecord | None:
    if not isinstance(item, dict):
        return None
    dynamic_id = str(item.get("dynamic_id") or "")
    if not dynamic_id.isdigit():
        return None
    images = []
    for image in item.get("images") or []:
        if not isinstance(image, dict):
            continue
        file_name = str(image.get("file_name") or "")
        url = str(image.get("url") or "")
        if not file_name or not url:
            continue
        images.append(
            ImageRecord(
                url=url,
                url_clean=str(image.get("url_clean") or url),
                file_name=file_name,
                width=int(image.get("width") or 0),
                height=int(image.get("height") or 0),
                remote_size=image.get("remote_size"),
                status=str(image.get("status") or "pending"),
                local_size=image.get("local_size"),
            )
        )
    return PostRecord(
        dynamic_id=dynamic_id,
        uid=uid,
        dynamic_type=str(item.get("dynamic_type") or ""),
        major_type=str(item.get("major_type") or ""),
        kind=str(item.get("kind") or "other"),
        title=str(item.get("title") or ""),
        description=str(item.get("description") or ""),
        published_at=int(item.get("published_at") or 0),
        is_charge=bool(item.get("is_charge")),
        is_accessible=bool(item.get("is_accessible", 1)),
        images=images,
    )

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from .config import Config
from .db import Database
from .export import export_user
from .models import ImageRecord, PostRecord
from .sync import load_user_list
from .utils import (
    filename_from_url,
    normalize_download_url,
    parse_pub_time,
    slugify_name,
    strip_query,
)

logger = logging.getLogger(__name__)

REMOVED_LIST = "user_list_因为一些问题而移出.txt"


@dataclass
class MigrateStats:
    users: int = 0
    skipped_users: list[str] = field(default_factory=list)
    posts: int = 0
    image_refs: int = 0
    images: int = 0
    ok_images: int = 0
    missing_images: int = 0
    moved_users: int = 0
    errors: list[str] = field(default_factory=list)


def _build_uid_map(src: Path, extra: dict[str, int] | None = None) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for path in (src / "user_list.txt", src / REMOVED_LIST):
        for name, uid in load_user_list(path):
            mapping.setdefault(name.strip(), uid)
    if extra:
        mapping.update(extra)
    # casefold aliases for folders whose spelling changed slightly
    for name, uid in list(mapping.items()):
        mapping.setdefault(name.casefold(), uid)
    return mapping


def _parse_uid_map(values: list[str] | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values or []:
        if "=" not in value:
            logger.warning("忽略无效 --uid-map：%s", value)
            continue
        name, uid_text = value.split("=", 1)
        if uid_text.strip().isdigit():
            result[name.strip()] = int(uid_text.strip())
    return result


def _find_local_file(folder: Path, url: str) -> tuple[str, int | None]:
    clean = strip_query(url)
    raw_name = os.path.basename(clean)
    candidates = [
        raw_name,
        unquote(raw_name),
        filename_from_url(url),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = folder / candidate
        if path.is_file():
            return candidate, path.stat().st_size
    return filename_from_url(url), None


def _load_info(info_path: Path) -> list | None:
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("读取失败 %s：%s", info_path, exc)
        return None
    if not isinstance(data, list):
        logger.warning("格式不是列表，跳过：%s", info_path)
        return None
    return data


def migrate_v2(
    db: Database,
    config: Config,
    src_dir: str | Path,
    dry_run: bool = False,
    move: bool = False,
    exclude: list[str] | None = None,
    uid_map: list[str] | None = None,
    limit_users: int = 0,
) -> MigrateStats:
    src = Path(src_dir).resolve()
    opus_root = src / "opus"
    if not opus_root.is_dir():
        raise SystemExit(f"找不到 v2 数据目录：{opus_root}")

    stats = MigrateStats()
    excluded = {item.strip() for item in (exclude or []) if item.strip()}
    mapping = _build_uid_map(src, _parse_uid_map(uid_map))
    main_users = load_user_list(src / "user_list.txt")
    enabled_uids = {uid for _, uid in main_users} if main_users else None

    folders = []
    for entry in sorted(opus_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        if entry.name in excluded:
            logger.info("排除用户：%s", entry.name)
            continue
        if not (entry / "__info.json").is_file():
            continue
        folders.append(entry)
    if limit_users:
        folders = folders[:limit_users]

    logger.info("开始迁移 %d 个用户目录：%s", len(folders), src)

    for folder in folders:
        name = folder.name
        uid = mapping.get(name) or mapping.get(name.casefold())
        if uid is None:
            stats.skipped_users.append(name)
            logger.warning("找不到昵称对应的 uid，跳过：%s（可用 --uid-map 名称=uid）", name)
            continue

        info = _load_info(folder / "__info.json")
        if info is None:
            stats.errors.append(name)
            continue

        image_dir = str(folder.resolve())
        if not dry_run:
            db.upsert_user(
                uid,
                name=name,
                image_dir=image_dir,
                enabled=(uid in enabled_uids) if enabled_uids is not None else None,
            )

        user_posts = 0
        user_refs = 0
        user_unique = 0
        user_ok = 0
        user_missing = 0
        max_id = 0
        try:
            if not dry_run:
                with db.transaction():
                    for entry in info:
                        if not isinstance(entry, dict):
                            continue
                        post = _v2_entry_to_post(entry, uid, folder)
                        if post is None:
                            continue
                        post_id = db.upsert_post(post)
                        db.upsert_images(post_id, post.images)
                        user_posts += 1
                        user_refs += _v2_ref_count(entry)
                        user_unique += len(post.images)
                        user_ok += sum(1 for image in post.images if image.status == "ok")
                        user_missing += sum(1 for image in post.images if image.status == "missing")
                        max_id = max(max_id, int(post.dynamic_id))
                    if max_id:
                        db.set_last_dynamic_id(uid, max_id, synced_at=None)
            else:
                for entry in info:
                    if not isinstance(entry, dict):
                        continue
                    post = _v2_entry_to_post(entry, uid, folder)
                    if post is None:
                        continue
                    user_posts += 1
                    user_refs += _v2_ref_count(entry)
                    user_unique += len(post.images)
                    user_ok += sum(1 for image in post.images if image.status == "ok")
                    user_missing += sum(1 for image in post.images if image.status == "missing")
                    max_id = max(max_id, int(post.dynamic_id))

            stats.users += 1
            stats.posts += user_posts
            stats.image_refs += user_refs
            stats.images += user_unique
            stats.ok_images += user_ok
            stats.missing_images += user_missing

            if move and not dry_run:
                destination = config.images_dir / f"{uid}_{slugify_name(name)}"
                if destination.exists():
                    logger.warning("目标目录已存在，保留原目录：%s", destination)
                else:
                    try:
                        shutil.move(str(folder), str(destination))
                        try:
                            stored_dir = str(
                                destination.resolve().relative_to(config.data_dir.resolve())
                            )
                        except ValueError:
                            stored_dir = str(destination.resolve())
                        db.set_user_image_dir(uid, stored_dir)
                        stats.moved_users += 1
                    except Exception as exc:
                        logger.error("移动目录失败（保留引用）：%s：%s", folder, exc)

            if config.general.export_json and not dry_run:
                export_user(db, config, uid)

            logger.info(
                "  %s (%s)：%d 条 / %d 图（本地 %d，缺失 %d）",
                name,
                uid,
                user_posts,
                user_unique,
                user_ok,
                user_missing,
            )
        except Exception as exc:
            stats.errors.append(name)
            logger.exception("迁移用户 %s 失败：%s", name, exc)

    logger.info("")
    logger.info(
        "迁移%s：用户 %d，动态 %d，图片引用 %d，去重后 %d，本地存在 %d，缺失 %d",
        "预演" if dry_run else "完成",
        stats.users,
        stats.posts,
        stats.image_refs,
        stats.images,
        stats.ok_images,
        stats.missing_images,
    )
    if stats.skipped_users:
        logger.warning("跳过用户：%s", "、".join(stats.skipped_users))
    if stats.errors:
        logger.warning("失败用户：%s", "、".join(stats.errors))
    if stats.moved_users:
        logger.info("已移动用户目录：%d", stats.moved_users)
    return stats


def _v2_ref_count(entry: dict) -> int:
    item = entry.get("item")
    pictures = item.get("pictures") if isinstance(item, dict) else None
    return len(pictures) if isinstance(pictures, list) else 0


def _v2_entry_to_post(entry: dict, uid: int, folder: Path) -> PostRecord | None:
    raw_id = entry.get("dynamic_id")
    if raw_id in (None, ""):
        return None
    dynamic_id = str(raw_id).strip()
    if not dynamic_id.isdigit() or int(dynamic_id) <= 0:
        return None

    item = entry.get("item")
    item = item if isinstance(item, dict) else {}
    title = str(item.get("title") or "")
    description = str(item.get("description") or "")
    pictures = item.get("pictures")
    pictures = pictures if isinstance(pictures, list) else []

    images: list[ImageRecord] = []
    seen: set[str] = set()
    for url in pictures:
        if not isinstance(url, str) or not url.strip() or url in seen:
            continue
        seen.add(url)
        file_name, size = _find_local_file(folder, url)
        images.append(
            ImageRecord(
                url=url,
                url_clean=normalize_download_url(url),
                file_name=file_name,
                width=0,
                height=0,
                status="ok" if size else "missing",
                local_size=size,
            )
        )

    return PostRecord(
        dynamic_id=dynamic_id,
        uid=uid,
        dynamic_type=str(entry.get("type") or ""),
        major_type="",
        kind="legacy",
        title=title,
        description=description,
        published_at=parse_pub_time(entry.get("time")),
        is_charge=False,
        is_accessible=True,
        images=images,
    )

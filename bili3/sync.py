from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .avatars import download_avatar
from .bilibili import BiliClient
from .config import Config
from .db import Database
from .download import DownloadStats, Downloader
from .errors import JobCancelled
from .export import export_user
from .extract import extract_post, parse_dynamic_id
from .utils import slugify_name

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict], None]
CancelCheck = Callable[[], None]


def _emit(callback: ProgressCallback | None, payload: dict) -> None:
    if not callback:
        return
    try:
        callback(payload)
    except Exception:
        logger.exception("进度回调失败")


@dataclass
class SyncResult:
    uid: int
    name: str = ""
    status: str = "ok"
    pages: int = 0
    new_posts: int = 0
    new_images: int = 0
    skipped_posts: int = 0
    download: DownloadStats = field(default_factory=DownloadStats)
    message: str = ""


def load_user_list(path: Path) -> list[tuple[str, int]]:
    users: list[tuple[str, int]] = []
    seen: set[int] = set()
    if not path.is_file():
        return users
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            name, uid_text = line.split(":", 1)
        else:
            name, uid_text = "", line
        name = name.strip()
        uid_text = uid_text.strip()
        if not uid_text.isdigit():
            logger.warning("忽略无效用户行：%s", raw_line)
            continue
        uid = int(uid_text)
        if uid in seen:
            logger.warning("重复的 uid %s，已忽略：%s", uid, raw_line)
            continue
        seen.add(uid)
        users.append((name or str(uid), uid))
    return users


def _image_dir_for(config: Config, uid: int, name: str) -> str:
    relative = Path("images") / f"{int(uid)}_{slugify_name(name or str(uid))}"
    (config.data_dir / relative).mkdir(parents=True, exist_ok=True)
    return str(relative)


def ensure_user(
    db: Database, config: Config, uid: int, name: str = ""
) -> object:
    user = db.get_user(uid)
    if user is None:
        image_dir = _image_dir_for(config, uid, name)
        db.upsert_user(uid, name=name or str(uid), image_dir=image_dir)
        user = db.get_user(uid)
    elif not user["image_dir"]:
        image_dir = _image_dir_for(config, uid, name or user["name"] or str(uid))
        db.set_user_image_dir(uid, image_dir)
        user = db.get_user(uid)
    elif not Path(user["image_dir"]).is_absolute():
        (config.data_dir / user["image_dir"]).mkdir(parents=True, exist_ok=True)
    if name and name != user["name"]:
        db.upsert_user(uid, name=name, image_dir=user["image_dir"])
        user = db.get_user(uid)
    return user


async def polite_sleep(config: Config) -> None:
    delay = config.general.sleep
    if delay <= 0:
        return
    jitter = delay * config.general.jitter
    await asyncio.sleep(max(0.0, delay + random.uniform(-jitter, jitter)))


async def sync_user(
    db: Database,
    config: Config,
    client: BiliClient,
    downloader: Downloader | None,
    uid: int,
    name: str = "",
    full: bool = False,
    dry_run: bool = False,
    no_download: bool = False,
    max_pages: int | None = None,
    max_images: int | None = None,
    include_forwards: bool = False,
    on_event: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> SyncResult:
    if dry_run:
        user = db.get_user(uid)
        if user is None:
            user = {"uid": uid, "name": name or str(uid), "image_dir": "", "last_dynamic_id": "0"}
    else:
        user = ensure_user(db, config, uid, name)
    image_dir = str(user["image_dir"])
    result = SyncResult(uid=uid, name=str(user["name"] or name or uid))
    stop_id = 0 if full else int(user["last_dynamic_id"] or 0)
    pages_limit = config.general.max_pages if max_pages is None else max_pages
    image_budget = config.general.max_images if max_images is None else max_images
    image_slots = image_budget if image_budget > 0 else None

    run_id = None if dry_run else db.start_sync_run(uid)
    offset = ""
    seen_max = stop_id
    stopped_by_limit = False
    empty_retry = 0
    logger.info(
        "%s (%s) 开始%s同步%s",
        result.name,
        uid,
        "全量" if full else "增量",
        "（dry-run）" if dry_run else "",
    )
    _emit(on_event, {"event": "user_start", "uid": uid, "name": result.name})

    try:
        while True:
            if cancel_check:
                cancel_check()
            page = await client.fetch_page(uid, offset)
            items = page.get("items") if isinstance(page, dict) else None
            items = items if isinstance(items, list) else []
            pages_done = result.pages + 1

            if not items:
                if empty_retry < 1:
                    empty_retry += 1
                    logger.warning("  第 %d 页为空，可能被限流，重试一次", pages_done)
                    await polite_sleep(config)
                    continue
                logger.warning("  第 %d 页仍为空，停止", pages_done)
                break
            empty_retry = 0

            page_max = 0
            candidates = []
            for item in items:
                dynamic_id = parse_dynamic_id(item)
                if dynamic_id is None:
                    continue
                page_max = max(page_max, dynamic_id)
                if full or dynamic_id > stop_id:
                    candidates.append(item)

            if dry_run:
                page_posts = 0
                page_images = 0
                samples = []
                for item in candidates:
                    post = extract_post(item, default_uid=uid, include_forwards=include_forwards)
                    if not post:
                        continue
                    page_posts += 1
                    page_images += len(post.images)
                    if len(samples) < 3:
                        samples.append(
                            f"{post.dynamic_id} {post.kind} {len(post.images)}图 "
                            f"{post.title or post.description[:24]}"
                        )
                result.new_posts += page_posts
                result.new_images += page_images
                logger.info(
                    "  第 %d 页：本页动态 %d，新图文 %d，图片 %d %s",
                    pages_done,
                    len(items),
                    page_posts,
                    page_images,
                    " | 示例: " + " / ".join(samples) if samples else "",
                )
            else:
                post_ids: list[int] = []
                author_name = ""
                author_avatar = ""
                if candidates:
                    with db.transaction():
                        for item in candidates:
                            post = extract_post(
                                item, default_uid=uid, include_forwards=include_forwards
                            )
                            if post is None:
                                result.skipped_posts += 1
                                continue
                            post.uid = uid
                            if post.author_name:
                                author_name = post.author_name
                            if post.author_avatar:
                                author_avatar = post.author_avatar
                            post_id = db.upsert_post(post)
                            db.upsert_images(post_id, post.images)
                            post_ids.append(post_id)
                            result.new_posts += 1
                            result.new_images += len(post.images)
                        if page_max > seen_max:
                            seen_max = page_max
                            if not full:
                                db.set_last_dynamic_id(uid, seen_max, int(time.time()))
                    if author_name or author_avatar:
                        db.upsert_user(
                            uid,
                            name=author_name or result.name,
                            avatar_url=author_avatar,
                            image_dir=image_dir,
                        )
                        if author_name:
                            result.name = author_name
                        if author_avatar:
                            try:
                                await download_avatar(db, config, uid, author_avatar)
                            except Exception:
                                logger.warning("更新头像失败 uid=%s", uid, exc_info=True)

                logger.info(
                    "  第 %d 页：动态 %d，新图文 %d，图片 %d",
                    pages_done,
                    len(items),
                    len(post_ids),
                    result.new_images,
                )

                if post_ids and not no_download and downloader is not None and image_slots != 0:
                    rows = db.get_images_for_posts(post_ids)
                    if image_slots is not None:
                        rows = rows[:image_slots]
                    if rows:
                        def download_progress(done: int, total: int) -> None:
                            _emit(
                                on_event,
                                {
                                    "event": "download",
                                    "uid": uid,
                                    "done": done,
                                    "total": total,
                                },
                            )

                        stats = await downloader.download_rows(
                            rows,
                            on_progress=download_progress,
                            cancel_check=cancel_check,
                        )
                        result.download.merge(stats)
                        if image_slots is not None:
                            image_slots -= len(rows)
                            if image_slots < 0:
                                image_slots = 0

            result.pages = pages_done
            _emit(
                on_event,
                {
                    "event": "page",
                    "uid": uid,
                    "name": result.name,
                    "page": pages_done,
                    "items": len(items),
                    "new_posts": result.new_posts,
                    "new_images": result.new_images,
                    "downloaded": result.download.ok,
                },
            )

            if page_max == 0 and items:
                logger.warning("  第 %d 页没有可用 dynamic_id，停止", pages_done)
                break

            if not full and stop_id > 0 and page_max <= stop_id:
                break
            if not page.get("has_more"):
                break
            if pages_limit and pages_done >= pages_limit:
                stopped_by_limit = True
                logger.info("  达到 --max-pages %s，停止", pages_limit)
                break
            offset = str(page.get("offset") or "")
            if not offset:
                break
            await polite_sleep(config)

        # In full mode the watermark only advances after the archive crawl
        # completes. If --max-pages stopped us early, keep the old watermark so
        # a later full run can continue instead of silently skipping old pages.
        if not dry_run and full and not stopped_by_limit and seen_max > 0:
            db.set_last_dynamic_id(uid, seen_max, int(time.time()))

        result.status = "ok"
        if run_id is not None:
            db.finish_sync_run(
                run_id,
                "ok",
                pages=result.pages,
                new_posts=result.new_posts,
                new_images=result.new_images,
            )
        if config.general.export_json and not dry_run:
            export_user(db, config, uid)
        logger.info(
            "%s (%s) 完成：%d 页，%d 条图文，%d 张图片；下载 %d 成功 / %d 跳过 / %d 失败 / %d 失效",
            result.name,
            uid,
            result.pages,
            result.new_posts,
            result.new_images,
            result.download.ok,
            result.download.skipped,
            result.download.failed,
            result.download.dead,
        )
        return result
    except JobCancelled:
        result.status = "cancelled"
        result.message = "已取消"
        if run_id is not None:
            db.finish_sync_run(
                run_id,
                "cancelled",
                pages=result.pages,
                new_posts=result.new_posts,
                new_images=result.new_images,
                message="cancelled",
            )
        logger.warning("%s (%s) 同步已取消", result.name, uid)
        raise
    except Exception as exc:
        result.status = "failed"
        result.message = str(exc)
        if run_id is not None:
            db.finish_sync_run(
                run_id,
                "failed",
                pages=result.pages,
                new_posts=result.new_posts,
                new_images=result.new_images,
                message=str(exc)[:500],
            )
        logger.exception("%s (%s) 同步失败：%s", result.name, uid, exc)
        raise


async def sync_many(
    db: Database,
    config: Config,
    client: BiliClient,
    targets: list[tuple[str, int]],
    full: bool = False,
    dry_run: bool = False,
    no_download: bool = False,
    max_pages: int | None = None,
    max_images: int | None = None,
    include_forwards: bool = False,
    on_event: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> list[SyncResult]:
    results: list[SyncResult] = []
    total = len(targets)
    _emit(on_event, {"event": "total", "total": total})
    async with Downloader(db, config) as downloader:
        for index, (name, uid) in enumerate(targets, 1):
            if cancel_check:
                cancel_check()
            logger.info("[%d/%d] %s (%s)", index, total, name or uid, uid)
            try:
                result = await sync_user(
                    db,
                    config,
                    client,
                    downloader,
                    uid,
                    name=name,
                    full=full,
                    dry_run=dry_run,
                    no_download=no_download,
                    max_pages=max_pages,
                    max_images=max_images,
                    include_forwards=include_forwards,
                    on_event=on_event,
                    cancel_check=cancel_check,
                )
                results.append(result)
            except JobCancelled:
                raise
            except Exception as exc:
                results.append(
                    SyncResult(uid=uid, name=name, status="failed", message=str(exc))
                )
                logger.error("用户 %s (%s) 失败，继续下一个：%s", name or uid, uid, exc)
            _emit(
                on_event,
                {
                    "event": "user_done",
                    "done": index,
                    "total": total,
                    "uid": uid,
                    "name": name or str(uid),
                    "status": results[-1].status if results else "failed",
                },
            )
            if index < len(targets):
                # The old v2 script also paused between users. Keep this even
                # when each user only needs one page, to avoid burst requests.
                await polite_sleep(config)
    return results


def print_sync_summary(results: list[SyncResult]) -> None:
    if not results:
        return
    ok = sum(1 for r in results if r.status == "ok")
    failed = len(results) - ok
    logger.info("")
    logger.info("同步汇总：成功 %d，失败 %d", ok, failed)
    for result in results:
        marker = "OK " if result.status == "ok" else "ERR"
        logger.info(
            "  [%s] %s (%s) %d 页 / %d 条 / %d 图 %s",
            marker,
            result.name,
            result.uid,
            result.pages,
            result.new_posts,
            result.new_images,
            result.message,
        )

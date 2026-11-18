from __future__ import annotations

import logging

from .config import Config
from .db import Database
from .download import DownloadStats, Downloader
from .utils import resolve_image_path

logger = logging.getLogger(__name__)


def verify_images(
    db: Database,
    config: Config,
    uid: int | None = None,
    dry_run: bool = False,
) -> dict:
    rows = db.get_images_with_user(uid)
    stats = {
        "checked": 0,
        "ok": 0,
        "missing": 0,
        "restored": 0,
        "bytes": 0,
    }
    for row in rows:
        stats["checked"] += 1
        try:
            path = resolve_image_path(config.data_dir, row["image_dir"], row["file_name"])
        except ValueError:
            path = None
        exists = bool(path and path.is_file() and path.stat().st_size > 0)
        size = path.stat().st_size if exists else 0
        if exists:
            stats["ok"] += 1
            stats["bytes"] += size
            if row["status"] != "ok":
                stats["restored"] += 1
                if not dry_run:
                    db.update_image_status(int(row["id"]), "ok", local_size=size, http_status=200)
        elif row["status"] == "ok":
            stats["missing"] += 1
            if not dry_run:
                db.update_image_status(
                    int(row["id"]),
                    "missing",
                    local_size=0,
                    error="local file missing",
                )
        elif row["status"] == "missing":
            stats["missing"] += 1
    return stats


async def repair_images(
    db: Database,
    config: Config,
    uid: int | None = None,
    max_images: int = 0,
    statuses: tuple[str, ...] = ("missing", "failed", "pending"),
    dry_run: bool = False,
) -> DownloadStats:
    rows = db.get_repair_images(uid=uid, limit=max_images, statuses=statuses)
    if dry_run:
        logger.info("待补下载 %d 张（dry-run，不下载）", len(rows))
        return DownloadStats()
    if not rows:
        logger.info("没有需要补下载的图片")
        return DownloadStats()
    logger.info("开始补下载 %d 张图片", len(rows))
    async with Downloader(db, config) as downloader:
        stats = await downloader.download_rows(rows, max_images=max_images)
    logger.info(
        "补下载完成：成功 %d，跳过 %d，失败 %d，失效 %d，受限 %d",
        stats.ok,
        stats.skipped,
        stats.failed,
        stats.dead,
        stats.restricted,
    )
    return stats

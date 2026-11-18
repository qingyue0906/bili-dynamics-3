from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from .config import Config
from .db import Database
from .utils import normalize_download_url, resolve_image_path

logger = logging.getLogger(__name__)

CHUNK = 64 * 1024
IMAGE_TYPES = ("image/", "application/octet-stream", "binary/octet-stream")


@dataclass
class DownloadStats:
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    dead: int = 0
    restricted: int = 0

    @property
    def total(self) -> int:
        return self.ok + self.skipped + self.failed + self.dead + self.restricted

    def merge(self, other: "DownloadStats") -> None:
        self.ok += other.ok
        self.skipped += other.skipped
        self.failed += other.failed
        self.dead += other.dead
        self.restricted += other.restricted


class Downloader:
    def __init__(self, db: Database, config: Config) -> None:
        self.db = db
        self.config = config
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "Downloader":
        timeout = aiohttp.ClientTimeout(
            total=self.config.general.timeout,
            connect=self.config.general.connect_timeout,
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
            ),
            "Referer": "https://www.bilibili.com/",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self.session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.session:
            await self.session.close()

    def _target_path(self, image: dict) -> Path:
        target = resolve_image_path(
            self.config.data_dir, image.get("image_dir") or "", image["file_name"]
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    async def download_rows(
        self,
        rows: list[dict],
        force: bool = False,
        max_images: int = 0,
        progress_every: int = 20,
    ) -> DownloadStats:
        if not rows:
            return DownloadStats()
        if max_images and max_images > 0:
            rows = rows[:max_images]
        semaphore = asyncio.Semaphore(self.config.general.download_concurrency)
        stats = DownloadStats()
        done = 0

        async def worker(row: dict) -> DownloadStats:
            nonlocal done
            async with semaphore:
                result = await self._download_one(row, force=force)
            done += 1
            if progress_every and done % progress_every == 0:
                logger.info("下载进度：%d/%d", done, len(rows))
            return result

        results = await asyncio.gather(*(worker(row) for row in rows), return_exceptions=True)
        for index, result in enumerate(results):
            if isinstance(result, DownloadStats):
                stats.merge(result)
            else:
                stats.failed += 1
                logger.error("下载任务异常：%s", result)
        return stats

    async def _download_one(self, image: dict, force: bool = False) -> DownloadStats:
        stats = DownloadStats()
        image_id = int(image["id"])

        try:
            target = self._target_path(image)
        except Exception as exc:
            logger.error("图片 %s 路径非法：%s", image_id, exc)
            self.db.update_image_status(
                image_id, "failed", error=f"invalid path: {exc}", increment_attempts=1
            )
            stats.failed += 1
            return stats

        if target.is_file() and target.stat().st_size > 0 and not force:
            size = target.stat().st_size
            self.db.update_image_status(image_id, "ok", local_size=size, http_status=200)
            stats.skipped += 1
            return stats

        url = image.get("url_clean") or normalize_download_url(image.get("url") or "")
        if not url:
            self.db.update_image_status(image_id, "failed", error="empty url", increment_attempts=1)
            stats.failed += 1
            return stats

        attempts = int(image.get("attempts") or 0)
        if attempts >= 5:
            stats.failed += 1
            return stats

        retries = self.config.general.retries
        last_error = ""
        tries = 0
        part = target.with_name(target.name + ".part")
        try:
            for attempt in range(retries + 1):
                tries += 1
                try:
                    assert self.session is not None
                    async with self.session.get(url) as response:
                        status = response.status
                        if status == 200:
                            content_type = (
                                response.headers.get("Content-Type", "")
                                .split(";", 1)[0]
                                .strip()
                                .lower()
                            )
                            if content_type and not content_type.startswith(IMAGE_TYPES):
                                raise ValueError(f"非图片响应：{content_type}")

                            size = 0
                            with open(part, "wb") as handle:
                                async for chunk in response.content.iter_chunked(CHUNK):
                                    handle.write(chunk)
                                    size += len(chunk)
                            if size <= 0:
                                raise ValueError("空文件")

                            os.replace(part, target)
                            if image.get("published_at"):
                                stamp = int(image["published_at"])
                                os.utime(target, (stamp, stamp))
                            self.db.update_image_status(
                                image_id,
                                "ok",
                                local_size=size,
                                http_status=200,
                                error=None,
                                downloaded_at=int(time.time()),
                            )
                            stats.ok += 1
                            return stats

                        if status in (404, 410):
                            self.db.update_image_status(
                                image_id,
                                "dead",
                                http_status=status,
                                error=f"HTTP {status}",
                                increment_attempts=1,
                            )
                            stats.dead += 1
                            return stats

                        if status in (401, 403):
                            self.db.update_image_status(
                                image_id,
                                "restricted",
                                http_status=status,
                                error=f"HTTP {status}",
                                increment_attempts=1,
                            )
                            stats.restricted += 1
                            return stats

                        if status == 429 or 500 <= status < 600:
                            last_error = f"HTTP {status}"
                            if attempt < retries:
                                wait = min(60.0, 5.0 * (2 ** attempt))
                                logger.warning(
                                    "图片 %s 返回 %s，%.0fs 后重试", image_id, status, wait
                                )
                                await asyncio.sleep(wait)
                                continue
                            break

                        last_error = f"HTTP {status}"
                        if attempt < retries:
                            await asyncio.sleep(min(30.0, 2.0 * (2 ** attempt)))
                            continue
                        break

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = str(exc)
                    if attempt < retries:
                        wait = min(30.0, 2.0 * (2 ** attempt))
                        logger.warning("图片 %s 下载失败：%s，%.1fs 后重试", image_id, exc, wait)
                        await asyncio.sleep(wait)
                        continue
                    break
        finally:
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass

        self.db.update_image_status(
            image_id,
            "failed",
            error=last_error or "unknown error",
            increment_attempts=tries,
        )
        stats.failed += 1
        logger.warning("图片 %s 最终失败：%s", image_id, last_error)
        return stats

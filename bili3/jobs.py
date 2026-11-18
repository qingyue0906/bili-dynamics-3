from __future__ import annotations

import asyncio
import logging

from .avatars import refresh_avatars
from .bilibili import BiliClient
from .config import load_config
from .db import Database
from .single import download_dynamic
from .sync import sync_many
from .tasks import JobContext
from .verify import repair_images, verify_images

logger = logging.getLogger(__name__)


def _config(ctx: JobContext):
    return load_config(base_dir=ctx.base_dir, data_dir=ctx.data_dir)


def _targets(db: Database, uids: list[int] | None) -> list[tuple[str, int]]:
    if uids:
        rows = [db.get_user(uid) for uid in uids]
        return [(str(row["name"] or row["uid"]), int(row["uid"])) for row in rows if row]
    return [(str(row["name"] or row["uid"]), int(row["uid"])) for row in db.list_users_for_sync()]


def run_sync(
    ctx: JobContext,
    uids: list[int] | None = None,
    full: bool = False,
    no_download: bool = False,
    max_pages: int | None = None,
    max_images: int | None = None,
    include_forwards: bool = False,
) -> None:
    config = _config(ctx)
    config.require_cookie()
    db = Database(config.db_path)
    try:
        targets = _targets(db, uids)
        if not targets:
            raise ValueError("没有可同步的用户。请先在“用户”页面添加或启用用户。")
        ctx.update(total=len(targets), done=0, message=f"准备同步 {len(targets)} 个用户")

        def on_event(event: dict) -> None:
            kind = event.get("event")
            if kind == "total":
                ctx.update(total=int(event.get("total") or 0))
            elif kind == "user_start":
                ctx.update(message=f"同步 {event.get('name')} ({event.get('uid')})")
            elif kind == "page":
                ctx.update(
                    message=(
                        f"{event.get('name')} 第 {event.get('page')} 页："
                        f"本页动态 {event.get('items')}，新图文 {event.get('new_posts')}，"
                        f"图片 {event.get('new_images')}"
                    )
                )
            elif kind == "download":
                ctx.update(
                    message=f"{event.get('name') or event.get('uid')} 下载图片 "
                    f"{event.get('done')}/{event.get('total')}"
                )
            elif kind == "user_done":
                ctx.update(
                    done=int(event.get("done") or 0),
                    message=f"{event.get('name')} 完成（{event.get('status')}）",
                )

        client = BiliClient(config)
        results = asyncio.run(
            sync_many(
                db,
                config,
                client,
                targets,
                full=full,
                no_download=no_download,
                max_pages=max_pages,
                max_images=max_images,
                include_forwards=include_forwards,
                on_event=on_event,
                cancel_check=ctx.check_cancel,
            )
        )
        failed = sum(1 for result in results if result.status != "ok")
        ctx.update(
            done=len(results),
            message=f"同步结束：{len(results)} 个用户，失败 {failed} 个",
        )
    finally:
        db.close()


def run_retry(ctx: JobContext, uids: list[int] | None = None, max_images: int = 0) -> None:
    config = _config(ctx)
    config.require_cookie()
    db = Database(config.db_path)
    try:
        # Count all matching rows for a useful progress bar.
        rows = db.get_repair_images(
            uid=None if uids else None,
            uids=uids,
            limit=0,
            statuses=("missing", "failed", "pending"),
        )
        if max_images:
            rows = rows[:max_images]
        if not rows:
            ctx.update(total=0, done=0, message="没有需要重试的图片")
            return
        ctx.update(total=len(rows), done=0, message=f"准备重试 {len(rows)} 张图片")

        def on_progress(done: int, total: int) -> None:
            ctx.update(done=done, total=total, message=f"重试图片 {done}/{total}")

        asyncio.run(
            repair_images(
                db,
                config,
                uids=uids,
                max_images=max_images,
                on_progress=on_progress,
                cancel_check=ctx.check_cancel,
            )
        )
        ctx.update(message="重试完成")
    finally:
        db.close()


def run_verify(ctx: JobContext, uid: int | None = None, repair: bool = False, max_images: int = 0) -> None:
    config = _config(ctx)
    db = Database(config.db_path)
    try:
        ctx.update(total=0, done=0, message="正在校验本地图片")
        stats = verify_images(db, config, uid=uid, dry_run=False)
        ctx.update(
            message=(
                f"校验完成：检查 {stats['checked']}，存在 {stats['ok']}，"
                f"缺失 {stats['missing']}，状态修正 {stats['restored']}"
            )
        )
        if repair:
            config.require_cookie()
            rows = db.get_repair_images(
                uid=uid, limit=max_images, statuses=("missing", "failed", "pending")
            )
            if rows:
                ctx.update(total=len(rows), done=0, message=f"补下 {len(rows)} 张图片")

                def on_progress(done: int, total: int) -> None:
                    ctx.update(done=done, total=total, message=f"补下图片 {done}/{total}")

                asyncio.run(
                    repair_images(
                        db,
                        config,
                        uid=uid,
                        max_images=max_images,
                        statuses=("missing", "failed", "pending"),
                        on_progress=on_progress,
                        cancel_check=ctx.check_cancel,
                    )
                )
                ctx.update(message="校验与补下完成")
    finally:
        db.close()


def run_avatars(ctx: JobContext, uids: list[int] | None = None) -> None:
    config = _config(ctx)
    config.require_cookie()
    db = Database(config.db_path)
    try:
        users = db.list_users_with_counts()
        if uids:
            wanted = {int(uid) for uid in uids}
            users = [user for user in users if int(user["uid"]) in wanted]
        ctx.update(total=len(users), done=0, message=f"准备补全 {len(users)} 个头像")

        def on_progress(done: int, total: int) -> None:
            ctx.update(done=done, total=total, message=f"补全头像 {done}/{total}")

        stats = asyncio.run(
            refresh_avatars(
                db,
                config,
                uids=uids,
                on_progress=on_progress,
                cancel_check=ctx.check_cancel,
            )
        )
        ctx.update(
            message=(
                f"头像补全完成：成功 {stats['ok']}，跳过 {stats['skipped']}，"
                f"失败 {stats['failed']}"
            )
        )
    finally:
        db.close()


def run_dynamic(ctx: JobContext, text: str, add_user: bool = False) -> None:
    config = _config(ctx)
    config.require_cookie()
    db = Database(config.db_path)
    try:
        ctx.update(total=0, done=0, message="正在获取动态详情")

        def on_progress(done: int, total: int) -> None:
            ctx.update(done=done, total=total, message=f"下载指定动态 {done}/{total}")

        result = asyncio.run(
            download_dynamic(
                db,
                config,
                text,
                add_user=add_user,
                on_progress=on_progress,
                cancel_check=ctx.check_cancel,
            )
        )
        ctx.update(done=result.get("downloaded", 0), message=result.get("message", "完成"))
    finally:
        db.close()

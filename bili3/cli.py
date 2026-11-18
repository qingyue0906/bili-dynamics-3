from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .bilibili import BiliClient
from .config import load_config
from .db import Database
from .export import export_all, export_user, import_exports
from .logging_setup import setup_logging
from .migrate import migrate_v2
from .sync import ensure_user, load_user_list, print_sync_summary, sync_many
from .utils import human_size
from .verify import repair_images, verify_images

logger = logging.getLogger(__name__)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="配置文件路径，默认 config.toml")
    parser.add_argument("--data-dir", help="数据目录，默认 data")
    parser.add_argument("--user-list", help="用户列表路径，默认 user_list.txt")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")


def _add_sync_limits(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sleep", type=float, help="翻页间隔秒数，默认 5")
    parser.add_argument("--max-pages", type=int, help="本次最多抓取页数，0 为不限")
    parser.add_argument("--max-images", type=int, help="本次最多下载图片数，0 为不限")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bili3",
        description="bili-dynamics-3：B 站图文动态增量下载与本地画廊",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="增量/全量同步动态")
    _add_common(sync)
    _add_sync_limits(sync)
    sync.add_argument("--all", action="store_true", help="同步 user_list 中所有用户")
    sync.add_argument("--uid", type=int, help="只同步一个 uid")
    sync.add_argument("--full", action="store_true", help="忽略断点，抓取全部历史")
    sync.add_argument("--dry-run", action="store_true", help="只抓取解析，不写库不下载")
    sync.add_argument("--no-download", action="store_true", help="只写元数据，不下载图片")
    sync.add_argument("--include-forwards", action="store_true", help="解析转发动态的原图")
    sync.add_argument("--limit-users", type=int, default=0, help="仅处理前 N 个用户，便于测试")

    retry = sub.add_parser("retry", help="重试失败/缺失/待下载图片")
    _add_common(retry)
    retry.add_argument("--all", action="store_true", help="处理所有用户（默认行为）")
    retry.add_argument("--uid", type=int)
    retry.add_argument("--max-images", type=int, default=0)
    retry.add_argument("--dry-run", action="store_true")

    verify = sub.add_parser("verify", help="校验本地图片与数据库记录")
    _add_common(verify)
    verify.add_argument("--uid", type=int)
    verify.add_argument("--repair", action="store_true", help="校验后补下载缺失图片")
    verify.add_argument("--max-images", type=int, default=0)
    verify.add_argument("--dry-run", action="store_true")

    migrate = sub.add_parser("migrate", help="把 v2 数据迁移到 v3")
    _add_common(migrate)
    migrate.add_argument("--src", required=True, help="v2 项目根目录（含 opus/）")
    migrate.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    migrate.add_argument("--move", action="store_true", help="迁移后移动图片目录到 data/images")
    migrate.add_argument("--exclude", action="append", default=[], help="排除的用户昵称，可重复")
    migrate.add_argument("--uid-map", action="append", default=[], help="补充昵称=uid，可重复")
    migrate.add_argument("--limit-users", type=int, default=0)
    migrate.add_argument("--force-user-list", action="store_true", help="覆盖 v3 的 user_list.txt")

    serve = sub.add_parser("serve", help="启动本地 WebUI")
    _add_common(serve)
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--debug", action="store_true")

    stats = sub.add_parser("stats", help="查看统计")
    _add_common(stats)

    export = sub.add_parser("export", help="导出每用户 v3 JSON")
    _add_common(export)
    export.add_argument("--uid", type=int)

    import_json = sub.add_parser("import-json", help="从 v3 JSON 导出重建数据库")
    _add_common(import_json)
    import_json.add_argument("--dir", help="导出目录，默认 data/exports")
    import_json.add_argument("--dry-run", action="store_true")

    return parser


def _make_config(args):
    return load_config(
        config_path=args.config,
        data_dir=args.data_dir,
        user_list=args.user_list,
        sleep=getattr(args, "sleep", None),
        max_pages=getattr(args, "max_pages", None),
        max_images=getattr(args, "max_images", None),
        host=getattr(args, "host", None),
        port=getattr(args, "port", None),
        debug=True if getattr(args, "debug", False) else None,
    )


def _cmd_sync(args, config) -> int:
    config.require_cookie()
    db = Database(config.db_path)
    try:
        client = BiliClient(config)
        if args.uid:
            if not args.dry_run and db.get_user(args.uid) is None:
                info = asyncio.run(client.fetch_user_info(args.uid))
                ensure_user(db, config, args.uid, str(info.get("name") or ""))
            targets = [("", args.uid)]
        else:
            targets = load_user_list(config.general.user_list)
        if args.limit_users:
            targets = targets[: args.limit_users]
        if not targets:
            logger.error(
                "没有可同步的用户。请检查 %s 或使用 --uid。", config.general.user_list
            )
            return 2
        if args.full and len(targets) > 1:
            logger.warning("全量同步 %d 个用户可能耗时很久，并触发 B 站风控。", len(targets))
        results = asyncio.run(
            sync_many(
                db,
                config,
                client,
                targets,
                full=args.full,
                dry_run=args.dry_run,
                no_download=args.no_download,
                max_pages=args.max_pages,
                max_images=args.max_images,
                include_forwards=args.include_forwards,
            )
        )
        print_sync_summary(results)
        return 1 if any(result.status != "ok" for result in results) else 0
    finally:
        db.close()


def _cmd_retry(args, config) -> int:
    if not args.dry_run:
        config.require_cookie()
    db = Database(config.db_path)
    try:
        asyncio.run(
            repair_images(
                db,
                config,
                uid=args.uid,
                max_images=args.max_images,
                statuses=("pending", "failed", "missing"),
                dry_run=args.dry_run,
            )
        )
        return 0
    finally:
        db.close()


def _cmd_verify(args, config) -> int:
    db = Database(config.db_path)
    try:
        stats = verify_images(db, config, uid=args.uid, dry_run=args.dry_run)
        logger.info(
            "校验完成：检查 %d，存在 %d，缺失 %d，状态修正 %d，合计 %s",
            stats["checked"],
            stats["ok"],
            stats["missing"],
            stats["restored"],
            human_size(stats["bytes"]),
        )
        if args.repair:
            if not args.dry_run:
                config.require_cookie()
            asyncio.run(
                repair_images(
                    db,
                    config,
                    uid=args.uid,
                    max_images=args.max_images,
                    statuses=("missing", "failed", "pending"),
                    dry_run=args.dry_run,
                )
            )
        return 0
    finally:
        db.close()


def _cmd_migrate(args, config) -> int:
    db = Database(config.db_path)
    try:
        stats = migrate_v2(
            db,
            config,
            args.src,
            dry_run=args.dry_run,
            move=args.move,
            exclude=args.exclude,
            uid_map=args.uid_map,
            limit_users=args.limit_users,
            force_user_list=args.force_user_list,
        )
        return 1 if stats.errors else 0
    finally:
        db.close()


def _cmd_serve(args, config) -> int:
    from .web.app import create_app

    app = create_app(config)
    logger.info("WebUI: http://%s:%s/", config.web.host, config.web.port)
    app.run(host=config.web.host, port=config.web.port, debug=config.web.debug, use_reloader=False)
    return 0


def _cmd_stats(args, config) -> int:
    db = Database(config.db_path)
    try:
        stats = db.stats()
        print(f"用户：{stats['users']}")
        print(f"动态：{stats['posts']}（有图 {stats['image_posts']}，充电占位 {stats['charge_posts']}）")
        print(
            f"图片：{stats['images']}（本地 {stats['ok_images']}，缺失/失败 {stats['missing_images']}，失效 {stats['dead_images']}）"
        )
        print(f"本地图片占用：{human_size(stats['bytes'])}")
        print("")
        for user in db.list_users_with_counts():
            print(
                f"  {user['name']} ({user['uid']})：{user['image_post_count']} 条 / {user['image_count']} 图"
            )
        return 0
    finally:
        db.close()


def _cmd_export(args, config) -> int:
    db = Database(config.db_path)
    try:
        if args.uid:
            path = export_user(db, config, args.uid)
            if not path:
                logger.error("用户不存在：%s", args.uid)
                return 2
            logger.info("已导出 %s", path)
        else:
            paths = export_all(db, config)
            logger.info("已导出 %d 个用户到 %s", len(paths), config.exports_dir)
        return 0
    finally:
        db.close()


def _cmd_import_json(args, config) -> int:
    db = Database(config.db_path)
    try:
        stats = import_exports(
            db,
            config,
            directory=Path(args.dir) if args.dir else None,
            dry_run=args.dry_run,
        )
        return 1 if stats.skipped else 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _make_config(args)
    setup_logging(config.logs_dir, args.command, verbose=getattr(args, "verbose", False))

    handlers = {
        "sync": _cmd_sync,
        "retry": _cmd_retry,
        "verify": _cmd_verify,
        "migrate": _cmd_migrate,
        "serve": _cmd_serve,
        "stats": _cmd_stats,
        "export": _cmd_export,
        "import-json": _cmd_import_json,
    }
    try:
        return handlers[args.command](args, config)
    except KeyboardInterrupt:
        logger.warning("已中断。已提交的数据不会丢失，下次可继续。")
        return 130


if __name__ == "__main__":
    sys.exit(main())

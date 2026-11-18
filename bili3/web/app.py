from __future__ import annotations

import asyncio
import html
import logging
import os
import threading
import warnings
from pathlib import Path

from flask import Flask, abort, g, jsonify, render_template, request, send_file, url_for
from PIL import Image, ImageOps

from ..avatars import download_avatar
from ..bilibili import BiliClient, check_credential
from ..config import Config, load_config
from ..db import Database
from ..jobs import run_avatars, run_dynamic, run_retry, run_sync, run_verify
from ..settings import SettingsStore
from ..single import parse_dynamic_input, parse_uid_input, preview_dynamic
from ..tasks import TaskManager, read_log_tail
from ..userdata import prepare_runtime
from ..utils import format_ts, human_size, resolve_image_path
from ..sync import ensure_user

logger = logging.getLogger(__name__)

PLACEHOLDER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" viewBox="0 0 400 400">
<rect width="400" height="400" fill="#f1f2f3"/>
<circle cx="200" cy="176" r="42" fill="#d9dce0"/>
<path d="M130 300l58-72 44 50 34-36 54 58z" fill="#c7cbd0"/>
<text x="200" y="352" text-anchor="middle" font-family="sans-serif" font-size="18" fill="#9499a0">图片缺失</text>
</svg>"""

_thumb_locks: dict[int, threading.Lock] = {}
_thumb_guard = threading.Lock()


def _lock_for(image_id: int) -> threading.Lock:
    with _thumb_guard:
        if image_id not in _thumb_locks:
            _thumb_locks[image_id] = threading.Lock()
        return _thumb_locks[image_id]


class AppState:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.settings: SettingsStore = config.settings_store
        self.tasks = TaskManager(config)

    def reload(self) -> None:
        self.config = load_config(
            base_dir=self.config.base_dir, data_dir=self.config.data_dir
        )
        self.settings = self.config.settings_store


def create_app(config: Config) -> Flask:
    prepare_runtime(config)
    state = AppState(config)
    package_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(package_dir / "templates"),
        static_folder=str(package_dir / "static"),
        static_url_path="/static",
    )
    app.config["BILI3_STATE"] = state

    def get_db() -> Database:
        if "bili3_db" not in g:
            g.bili3_db = Database(state.config.db_path, init=False)
        return g.bili3_db

    @app.teardown_appcontext
    def close_db(_exc=None):
        db = g.pop("bili3_db", None)
        if db is not None:
            db.close()

    @app.template_filter("timestamp_time")
    def _timestamp_time(value):
        return format_ts(value)

    @app.context_processor
    def inject_globals():
        db = get_db()
        return {
            "users": db.list_users_with_counts(),
            "stats": db.stats(),
            "web_settings": state.settings.web(),
            "active_job": state.tasks.active_job(),
            "settings_store": state.settings,
            "cookie_status": state.settings.masked_cookies(),
            "human_size": human_size,
        }

    @app.before_request
    def _csrf_guard():
        if (
            request.method in ("POST", "PUT", "PATCH", "DELETE")
            and request.path.startswith("/api/")
            and request.headers.get("X-Bili3") != "1"
        ):
            abort(403)

    @app.after_request
    def add_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    def _page_args():
        try:
            offset = max(0, int(request.args.get("offset", "0")))
        except ValueError:
            offset = 0
        try:
            limit = int(request.args.get("limit", str(state.config.web.page_size)))
        except ValueError:
            limit = state.config.web.page_size
        limit = max(1, min(60, limit))
        return offset, limit

    def _filters():
        uid = request.args.get("uid")
        try:
            uid_value = int(uid) if uid else None
        except ValueError:
            uid_value = None
        q = (request.args.get("q") or "").strip()
        sort = "asc" if (request.args.get("sort") or "").lower() == "asc" else "desc"
        include_charge = request.args.get("charge") == "1"
        return uid_value, q, sort, include_charge

    def _feed_state():
        web = state.config.web
        mode = request.args.get("mode") or web.default_view
        if mode not in ("dynamic", "images"):
            mode = "dynamic"
        sort = request.args.get("sort") or web.default_sort
        if sort not in ("desc", "asc"):
            sort = "desc"
        load_mode = request.args.get("load") or web.default_load
        if load_mode not in ("infinite", "pages"):
            load_mode = "infinite"
        try:
            page = int(request.args.get("page", "1"))
        except ValueError:
            page = 1
        page = max(1, page)
        if load_mode == "infinite":
            page = 1
        return mode, sort, load_mode, page

    def _feed_url(endpoint: str, path_args: dict | None = None, include_uid: bool = False, **overrides):
        keys = ("q", "uid", "sort", "mode", "load", "charge")
        args = {
            key: request.args.get(key)
            for key in keys
            if request.args.get(key) not in (None, "")
        }
        if not include_uid:
            args.pop("uid", None)
        args.update({key: value for key, value in overrides.items() if value is not None})
        if "page" not in overrides:
            args.pop("page", None)
        return url_for(endpoint, **(path_args or {}), **args)

    def _pagination(endpoint: str, path_args: dict | None, include_uid: bool, page: int, total: int, limit: int):
        total_pages = max(1, (total + limit - 1) // limit) if total else 1
        page = min(max(1, page), total_pages)
        pages = []
        start = max(1, page - 3)
        end = min(total_pages, start + 6)
        start = max(1, end - 6)
        for number in range(start, end + 1):
            pages.append(
                (
                    number,
                    _feed_url(
                        endpoint,
                        path_args=path_args,
                        include_uid=include_uid,
                        page=number,
                    ),
                    number == page,
                )
            )
        prev_url = (
            _feed_url(
                endpoint, path_args=path_args, include_uid=include_uid, page=page - 1
            )
            if page > 1
            else None
        )
        next_url = (
            _feed_url(
                endpoint, path_args=path_args, include_uid=include_uid, page=page + 1
            )
            if page < total_pages
            else None
        )
        return {
            "page": page,
            "total_pages": total_pages,
            "page_links": pages,
            "prev_url": prev_url,
            "next_url": next_url,
        }

    def _render_feed(
        template: str,
        endpoint: str,
        path_args: dict | None,
        include_uid: bool,
        uid: int | None,
        q: str,
        include_charge: bool,
        page_title: str,
        **extra,
    ):
        db = get_db()
        mode, sort, load_mode, page = _feed_state()
        limit = (
            state.config.web.image_page_size
            if mode == "images"
            else state.config.web.page_size
        )
        show_empty_posts = bool(state.config.web.show_empty_posts)
        show_missing_images = bool(state.config.web.show_missing_images)
        if mode == "images":
            total = db.count_images_with_posts(
                uid=uid,
                q=q or None,
                include_missing=show_missing_images,
            )
        else:
            total = db.count_posts(
                uid=uid,
                q=q or None,
                include_charge=include_charge,
                include_empty=show_empty_posts,
            )
        if load_mode == "infinite":
            page = 1
        else:
            total_pages = max(1, (total + limit - 1) // limit) if total else 1
            page = min(page, total_pages)
        offset = (page - 1) * limit
        if mode == "images":
            items, has_more = db.list_images_with_posts(
                limit=limit,
                offset=offset,
                uid=uid,
                q=q or None,
                sort=sort,
                include_missing=show_missing_images,
            )
        else:
            items, has_more = db.list_posts_with_images(
                limit=limit,
                offset=offset,
                uid=uid,
                q=q or None,
                sort=sort,
                include_charge=include_charge,
                include_empty=show_empty_posts,
            )
        page_data = _pagination(endpoint, path_args, include_uid, page, total, limit)
        page = page_data["page"]
        if mode == "images" and page_title == "全部动态":
            page_title = "全部图片"
        return render_template(
            template,
            posts=items if mode == "dynamic" else [],
            album_items=items if mode == "images" else [],
            mode=mode,
            sort=sort,
            load_mode=load_mode,
            has_more=has_more,
            next_offset=(offset + len(items)) if has_more else 0,
            limit=limit,
            total=total,
            current_uid=uid,
            query=q,
            include_charge=include_charge,
            page_title=page_title,
            feed_url=lambda **kwargs: _feed_url(
                endpoint,
                path_args=path_args,
                include_uid=include_uid,
                **kwargs,
            ),
            **page_data,
            **extra,
        )

    @app.route("/")
    def index():
        return _render_feed(
            "index.html",
            "index",
            None,
            False,
            None,
            "",
            False,
            "全部动态",
            active_page="home",
        )

    @app.route("/user/<int:uid>")
    def user_page(uid: int):
        db = get_db()
        user = db.get_user(uid)
        if not user:
            abort(404)
        return _render_feed(
            "user.html",
            "user_page",
            {"uid": uid},
            False,
            uid,
            "",
            False,
            str(user["name"] or uid),
            active_page="home",
            user=user,
        )

    @app.route("/search")
    def search():
        uid, q, sort, include_charge = _filters()
        return _render_feed(
            "search.html",
            "search",
            None,
            True,
            uid,
            q,
            include_charge,
            f"搜索：{q}" if q else "搜索",
            active_page="home",
        )

    @app.route("/api/posts")
    def api_posts():
        db = get_db()
        offset, limit = _page_args()
        uid, q, sort, include_charge = _filters()
        posts, has_more = db.list_posts_with_images(
            limit=limit,
            offset=offset,
            uid=uid,
            q=q or None,
            sort=sort,
            include_charge=include_charge,
            include_empty=bool(state.config.web.show_empty_posts),
        )
        html_fragment = render_template("partials/post_list.html", posts=posts)
        return jsonify(
            {
                "html": html_fragment,
                "has_more": has_more,
                "next_offset": (offset + len(posts)) if has_more else 0,
            }
        )

    @app.route("/api/images")
    def api_images():
        db = get_db()
        offset, _ = _page_args()
        try:
            limit = int(
                request.args.get("limit", str(state.config.web.image_page_size))
            )
        except ValueError:
            limit = state.config.web.image_page_size
        limit = max(1, min(120, limit))
        uid, q, sort, _ = _filters()
        items, has_more = db.list_images_with_posts(
            limit=limit,
            offset=offset,
            uid=uid,
            q=q or None,
            sort=sort,
            include_missing=bool(state.config.web.show_missing_images),
        )
        html_fragment = render_template("partials/album_list.html", album_items=items)
        return jsonify(
            {
                "html": html_fragment,
                "has_more": has_more,
                "next_offset": (offset + len(items)) if has_more else 0,
            }
        )

    @app.route("/media/<int:image_id>")
    def media(image_id: int):
        row = get_db().get_image_with_user(image_id)
        if not row:
            abort(404)
        try:
            path = resolve_image_path(
                state.config.data_dir, row["image_dir"], row["file_name"]
            )
        except ValueError:
            abort(404)
        if not path.is_file():
            abort(404)
        return send_file(
            path,
            conditional=True,
            max_age=86400,
            as_attachment=request.args.get("download") == "1",
            download_name=path.name,
        )

    @app.route("/thumb/<int:image_id>")
    def thumb(image_id: int):
        row = get_db().get_image_with_user(image_id)
        if not row:
            return _placeholder()
        try:
            source = resolve_image_path(
                state.config.data_dir, row["image_dir"], row["file_name"]
            )
        except ValueError:
            return _placeholder()
        if not source.is_file() or source.stat().st_size <= 0:
            return _placeholder()

        try:
            width = int(request.args.get("w", str(state.config.web.thumbnail_width)))
        except ValueError:
            width = state.config.web.thumbnail_width
        width = max(160, min(1200, width))
        try:
            mtime = int(source.stat().st_mtime)
        except OSError:
            return _placeholder()
        cache_path = state.config.thumbnails_dir / f"{image_id}_{mtime}_{width}.webp"
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            return send_file(
                cache_path, mimetype="image/webp", conditional=True, max_age=31536000
            )

        lock = _lock_for(image_id)
        with lock:
            if cache_path.is_file() and cache_path.stat().st_size > 0:
                return send_file(
                    cache_path, mimetype="image/webp", conditional=True, max_age=31536000
                )
            try:
                _make_thumbnail(source, cache_path, width)
            except Exception as exc:
                logger.warning("生成缩略图失败 %s：%s", source, exc)
                return _placeholder()
        return send_file(
            cache_path, mimetype="image/webp", conditional=True, max_age=31536000
        )

    @app.route("/avatar/<int:uid>")
    def avatar(uid: int):
        db = get_db()
        user = db.get_user(uid)
        if user and user["avatar_file"]:
            path = state.config.avatars_dir / Path(user["avatar_file"]).name
            if path.is_file() and path.stat().st_size > 0:
                return send_file(
                    path,
                    mimetype="image/webp",
                    conditional=True,
                    max_age=31536000,
                )
        name = str(user["name"] if user else "") or "?"
        initial = name[:1] or "?"
        hue = int(uid) % 360
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160">
<rect width="160" height="160" rx="80" fill="hsl({hue} 58% 60%)"/>
<text x="80" y="101" text-anchor="middle" font-family="sans-serif" font-size="64" font-weight="600" fill="#fff">{html.escape(initial)}</text>
</svg>"""
        return svg, 200, {"Content-Type": "image/svg+xml; charset=utf-8", "Cache-Control": "public, max-age=3600"}

    # ---------------- pages ----------------

    @app.route("/users")
    def users_page():
        db = get_db()
        return render_template(
            "users.html",
            user_rows=db.list_users_with_counts(),
            retry_counts=db.get_retry_counts(),
            page_title="用户",
            active_page="users",
            current_uid=None,
            query="",
        )

    @app.route("/jobs")
    def jobs_page():
        db = get_db()
        return render_template(
            "jobs.html",
            jobs=db.list_jobs(60),
            page_title="任务",
            active_page="jobs",
            current_uid=None,
            query="",
        )

    @app.route("/settings")
    def settings_page():
        return render_template(
            "settings.html",
            sync=state.settings.sync(),
            web=state.settings.web(),
            cookie=state.settings.masked_cookies(),
            data_dir=str(state.config.data_dir),
            page_title="设置",
            active_page="settings",
            current_uid=None,
            query="",
        )

    @app.route("/dynamic")
    def dynamic_page():
        return render_template(
            "dynamic.html",
            page_title="下载指定动态",
            active_page="dynamic",
            current_uid=None,
            query="",
        )

    # ---------------- user APIs ----------------

    @app.post("/api/users/add")
    def api_users_add():
        payload = request.get_json(silent=True) or {}
        uid = parse_uid_input(str(payload.get("input") or ""))
        if uid is None:
            return jsonify(ok=False, error="请输入 UID 或 space.bilibili.com 链接"), 400
        config = state.config
        db = get_db()
        existing = db.get_user(uid)
        info = {}
        if config.has_cookie:
            try:
                info = asyncio.run(BiliClient(config).fetch_user_info(uid))
            except SystemExit:
                info = {}
            except Exception as exc:
                logger.warning("添加用户时获取信息失败 %s：%s", uid, exc)
        name = str(info.get("name") or (existing["name"] if existing else "") or uid)
        avatar_url = str(info.get("face") or "")
        user = ensure_user(db, config, uid, name)
        db.upsert_user(
            uid,
            name=name,
            avatar_url=avatar_url,
            image_dir=user["image_dir"],
            enabled=True,
        )
        if avatar_url:
            try:
                asyncio.run(download_avatar(db, config, uid, avatar_url))
            except Exception:
                logger.warning("添加用户时下载头像失败 uid=%s", uid, exc_info=True)
        return jsonify(ok=True, uid=uid, name=name)

    @app.post("/api/users/<int:uid>/toggle")
    def api_user_toggle(uid: int):
        payload = request.get_json(silent=True) or {}
        enabled = bool(payload.get("enabled"))
        db = get_db()
        if not db.get_user(uid):
            return jsonify(ok=False, error="用户不存在"), 404
        db.set_user_enabled(uid, enabled)
        return jsonify(ok=True, uid=uid, enabled=enabled)

    # ---------------- job helpers ----------------

    def _submit(kind: str, target: str, func) -> int:
        return state.tasks.submit(kind, target, func)

    @app.post("/api/sync")
    def api_sync():
        payload = request.get_json(silent=True) or {}
        raw_uids = payload.get("uids")
        uids = [int(value) for value in raw_uids] if isinstance(raw_uids, list) and raw_uids else None
        full = bool(payload.get("full"))
        no_download = bool(payload.get("no_download"))
        include_forwards = bool(payload.get("include_forwards"))
        max_pages = payload.get("max_pages")
        max_images = payload.get("max_images")
        max_pages = int(max_pages) if max_pages not in (None, "") else None
        max_images = int(max_images) if max_images not in (None, "") else None
        target = "selected" if uids else "all"
        job_id = _submit(
            "sync",
            target,
            lambda ctx: run_sync(
                ctx,
                uids=uids,
                full=full,
                no_download=no_download,
                max_pages=max_pages,
                max_images=max_images,
                include_forwards=include_forwards,
            ),
        )
        return jsonify(ok=True, job_id=job_id)

    @app.post("/api/retry")
    def api_retry():
        payload = request.get_json(silent=True) or {}
        raw_uids = payload.get("uids")
        uids = [int(value) for value in raw_uids] if isinstance(raw_uids, list) and raw_uids else None
        max_images = int(payload.get("max_images") or 0)
        job_id = _submit(
            "retry",
            "selected" if uids else "all",
            lambda ctx: run_retry(ctx, uids=uids, max_images=max_images),
        )
        return jsonify(ok=True, job_id=job_id)

    @app.post("/api/verify")
    def api_verify():
        payload = request.get_json(silent=True) or {}
        uid = payload.get("uid")
        uid = int(uid) if uid else None
        repair = bool(payload.get("repair"))
        max_images = int(payload.get("max_images") or 0)
        job_id = _submit(
            "verify",
            str(uid or "all"),
            lambda ctx: run_verify(ctx, uid=uid, repair=repair, max_images=max_images),
        )
        return jsonify(ok=True, job_id=job_id)

    @app.post("/api/avatars/refresh")
    def api_avatars_refresh():
        payload = request.get_json(silent=True) or {}
        raw_uids = payload.get("uids")
        uids = [int(value) for value in raw_uids] if isinstance(raw_uids, list) and raw_uids else None
        job_id = _submit(
            "avatars",
            "selected" if uids else "all",
            lambda ctx: run_avatars(ctx, uids=uids),
        )
        return jsonify(ok=True, job_id=job_id)

    # ---------------- dynamic APIs ----------------

    @app.post("/api/dynamic/preview")
    def api_dynamic_preview():
        payload = request.get_json(silent=True) or {}
        text = str(payload.get("input") or "")
        try:
            result = asyncio.run(preview_dynamic(state.config, text))
        except SystemExit as exc:
            return jsonify(ok=False, error=str(exc)), 400
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True, post=result)

    @app.post("/api/dynamic/download")
    def api_dynamic_download():
        payload = request.get_json(silent=True) or {}
        text = str(payload.get("input") or "")
        if parse_dynamic_input(text) is None:
            return jsonify(ok=False, error="无法识别动态 ID"), 400
        add_user = bool(payload.get("add_user"))
        job_id = _submit(
            "dynamic",
            text,
            lambda ctx: run_dynamic(ctx, text, add_user=add_user),
        )
        return jsonify(ok=True, job_id=job_id)

    # ---------------- job APIs ----------------

    def _job_dict(row) -> dict:
        item = dict(row)
        total = int(item.get("total") or 0)
        done = int(item.get("done") or 0)
        item["percent"] = int(done * 100 / total) if total else (100 if item.get("status") == "succeeded" else 0)
        item["active"] = item.get("status") in ("queued", "running", "cancelling")
        return item

    @app.get("/api/jobs")
    def api_jobs():
        db = get_db()
        return jsonify(ok=True, jobs=[_job_dict(row) for row in db.list_jobs(60)])

    @app.get("/api/jobs/<int:job_id>")
    def api_job(job_id: int):
        db = get_db()
        row = db.get_job(job_id)
        if not row:
            return jsonify(ok=False, error="任务不存在"), 404
        item = _job_dict(row)
        item["log"] = read_log_tail(Path(row["log_path"]), limit=100) if row["log_path"] else []
        return jsonify(ok=True, job=item)

    @app.post("/api/jobs/<int:job_id>/cancel")
    def api_job_cancel(job_id: int):
        ok = state.tasks.cancel(job_id)
        return jsonify(ok=ok, error="" if ok else "任务无法取消"), (200 if ok else 400)

    # ---------------- settings APIs ----------------

    @app.get("/api/settings")
    def api_settings_get():
        return jsonify(
            ok=True,
            sync=state.settings.sync(),
            web=state.settings.web(),
            cookies=state.settings.masked_cookies(),
            data_dir=str(state.config.data_dir),
        )

    @app.post("/api/settings/options")
    def api_settings_options():
        payload = request.get_json(silent=True) or {}
        try:
            state.settings.save_options(
                sync=payload.get("sync") if isinstance(payload.get("sync"), dict) else None,
                web=payload.get("web") if isinstance(payload.get("web"), dict) else None,
            )
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)), 400
        state.reload()
        return jsonify(ok=True, sync=state.settings.sync(), web=state.settings.web())

    @app.post("/api/settings/cookies")
    def api_settings_cookies():
        payload = request.get_json(silent=True) or {}
        current = state.settings.cookies()
        sessdata = str(payload.get("sessdata") or "").strip() or current["sessdata"]
        bili_jct = str(payload.get("bili_jct") or "").strip() or current["bili_jct"]
        buvid3 = str(payload.get("buvid3") or "").strip() or current["buvid3"]
        if not sessdata or not buvid3:
            return jsonify(ok=False, error="SESSDATA 和 buvid3 不能为空"), 400
        try:
            valid, message = asyncio.run(check_credential(sessdata, bili_jct, buvid3))
        except Exception as exc:
            valid, message = False, str(exc)
        if not valid:
            return jsonify(ok=False, error=message), 400
        state.settings.save_cookies(sessdata, bili_jct, buvid3)
        state.reload()
        return jsonify(ok=True, message=message, cookies=state.settings.masked_cookies())

    @app.post("/api/settings/cookies/clear")
    def api_settings_cookies_clear():
        state.settings.clear_cookies()
        state.reload()
        return jsonify(ok=True, cookies=state.settings.masked_cookies())

    @app.post("/api/settings/cookies/test")
    def api_settings_cookies_test():
        cookies = state.settings.cookies()
        if not cookies["sessdata"] or not cookies["buvid3"]:
            return jsonify(ok=False, error="还没有配置 Cookie"), 400
        try:
            valid, message = asyncio.run(
                check_credential(cookies["sessdata"], cookies["bili_jct"], cookies["buvid3"])
            )
        except Exception as exc:
            valid, message = False, str(exc)
        return jsonify(ok=valid, message=message), (200 if valid else 400)

    @app.route("/health")
    def health():
        stats = get_db().stats()
        return jsonify({"ok": True, **stats})

    @app.errorhandler(404)
    def not_found(_exc):
        return (
            render_template(
                "error.html",
                code=404,
                message="页面不存在",
                current_uid=None,
                query="",
                page_title="404",
                active_page="",
            ),
            404,
        )

    return app


def _placeholder():
    return (
        PLACEHOLDER_SVG,
        200,
        {
            "Content-Type": "image/svg+xml; charset=utf-8",
            "Cache-Control": "no-store",
        },
    )


def _make_thumbnail(source: Path, destination: Path, width: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".tmp")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image)
                if getattr(image, "is_animated", False):
                    try:
                        image.seek(0)
                    except Exception:
                        pass
                image.thumbnail((width, width * 2), Image.Resampling.LANCZOS)
                if image.mode in ("RGBA", "LA") or (
                    image.mode == "P" and "transparency" in image.info
                ):
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, (255, 255, 255))
                    background.paste(rgba, mask=rgba.split()[-1])
                    image = background
                else:
                    image = image.convert("RGB")
                image.save(tmp, "WEBP", quality=80, method=4)
        os.replace(tmp, destination)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

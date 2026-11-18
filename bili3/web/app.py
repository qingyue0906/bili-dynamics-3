from __future__ import annotations

import logging
import os
import threading
import warnings
from pathlib import Path

from flask import Flask, abort, g, jsonify, render_template, request, send_file
from PIL import Image, ImageOps

from ..config import Config
from ..db import Database
from ..utils import format_ts, human_size, resolve_image_path

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


def create_app(config: Config) -> Flask:
    package_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(package_dir / "templates"),
        static_folder=str(package_dir / "static"),
        static_url_path="/static",
    )
    app.config["BILI3_CONFIG"] = config

    @app.template_filter("timestamp_time")
    def _timestamp_time(value):
        return format_ts(value)

    # Make sure the schema exists before the first request.
    Database(config.db_path).close()

    def get_db() -> Database:
        if "bili3_db" not in g:
            g.bili3_db = Database(config.db_path, init=False)
        return g.bili3_db

    @app.teardown_appcontext
    def close_db(_exc=None):
        db = g.pop("bili3_db", None)
        if db is not None:
            db.close()

    @app.context_processor
    def inject_globals():
        db = get_db()
        return {
            "users": db.list_users_with_counts(),
            "stats": db.stats(),
            "human_size": human_size,
        }

    def _page_args():
        try:
            offset = max(0, int(request.args.get("offset", "0")))
        except ValueError:
            offset = 0
        try:
            limit = int(request.args.get("limit", str(config.web.page_size)))
        except ValueError:
            limit = config.web.page_size
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

    @app.after_request
    def add_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.route("/")
    def index():
        _, limit = _page_args()
        posts, has_more = get_db().list_posts_with_images(limit=limit, offset=0)
        return render_template(
            "index.html",
            posts=posts,
            has_more=has_more,
            next_offset=limit if has_more else 0,
            current_uid=None,
            query="",
            sort="desc",
            include_charge=False,
            page_title="全部动态",
        )

    @app.route("/user/<int:uid>")
    def user_page(uid: int):
        db = get_db()
        user = db.get_user(uid)
        if not user:
            abort(404)
        _, limit = _page_args()
        posts, has_more = db.list_posts_with_images(limit=limit, offset=0, uid=uid)
        total = db.count_posts(uid=uid)
        return render_template(
            "user.html",
            user=user,
            posts=posts,
            has_more=has_more,
            next_offset=limit if has_more else 0,
            current_uid=uid,
            query="",
            sort="desc",
            include_charge=False,
            total=total,
            page_title=str(user["name"] or uid),
        )

    @app.route("/search")
    def search():
        db = get_db()
        uid, q, sort, include_charge = _filters()
        _, limit = _page_args()
        posts, has_more = db.list_posts_with_images(
            limit=limit,
            offset=0,
            uid=uid,
            q=q or None,
            sort=sort,
            include_charge=include_charge,
        )
        total = db.count_posts(uid=uid, q=q or None, include_charge=include_charge)
        return render_template(
            "search.html",
            posts=posts,
            has_more=has_more,
            next_offset=limit if has_more else 0,
            current_uid=uid,
            query=q,
            sort=sort,
            include_charge=include_charge,
            total=total,
            page_title=f"搜索：{q}" if q else "搜索",
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
        )
        html = render_template("partials/post_list.html", posts=posts)
        return jsonify(
            {
                "html": html,
                "has_more": has_more,
                "next_offset": (offset + len(posts)) if has_more else 0,
            }
        )

    @app.route("/media/<int:image_id>")
    def media(image_id: int):
        row = get_db().get_image_with_user(image_id)
        if not row:
            abort(404)
        try:
            path = resolve_image_path(config.data_dir, row["image_dir"], row["file_name"])
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
            source = resolve_image_path(config.data_dir, row["image_dir"], row["file_name"])
        except ValueError:
            return _placeholder()
        if not source.is_file() or source.stat().st_size <= 0:
            return _placeholder()

        try:
            width = int(request.args.get("w", str(config.web.thumbnail_width)))
        except ValueError:
            width = config.web.thumbnail_width
        width = max(160, min(1200, width))
        try:
            mtime = int(source.stat().st_mtime)
        except OSError:
            return _placeholder()
        cache_path = config.thumbnails_dir / f"{image_id}_{mtime}_{width}.webp"
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            return send_file(
                cache_path,
                mimetype="image/webp",
                conditional=True,
                max_age=31536000,
            )

        lock = _lock_for(image_id)
        with lock:
            if cache_path.is_file() and cache_path.stat().st_size > 0:
                return send_file(
                    cache_path,
                    mimetype="image/webp",
                    conditional=True,
                    max_age=31536000,
                )
            try:
                _make_thumbnail(source, cache_path, width)
            except Exception as exc:
                logger.warning("生成缩略图失败 %s：%s", source, exc)
                return _placeholder()
        return send_file(
            cache_path,
            mimetype="image/webp",
            conditional=True,
            max_age=31536000,
        )

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

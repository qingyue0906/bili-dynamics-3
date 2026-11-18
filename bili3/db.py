from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

from .models import ImageRecord, PostRecord
from .utils import format_ts


class Database:
    """Small SQLite wrapper.

    Connections use autocommit mode; multi-statement writes use the
    `transaction()` context manager so a crash cannot leave a half page.
    """

    def __init__(self, path: str | Path, init: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(self.path), timeout=30, isolation_level=None, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        if init:
            self.init_schema()

    def init_schema(self) -> None:
        has_users = bool(
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
            ).fetchone()
        )
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if has_users and version < 4:
            self._backup_before_upgrade(version)
        schema = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
        self.conn.executescript(schema)
        self._ensure_user_columns()
        self.conn.execute("PRAGMA user_version = 4")

    def _backup_before_upgrade(self, version: int) -> None:
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"bili-before-v4-{time.strftime('%Y%m%d-%H%M%S')}.db"
        try:
            destination = sqlite3.connect(str(target))
            try:
                self.conn.backup(destination)
            finally:
                destination.close()
            logger.warning("数据库升级前已备份：%s（旧版本 %s）", target, version)
        except Exception as exc:
            logger.error("数据库备份失败：%s", exc)

    def _ensure_user_columns(self) -> None:
        columns = {
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "avatar_file": "TEXT NOT NULL DEFAULT ''",
            "avatar_source": "TEXT NOT NULL DEFAULT ''",
            "avatar_updated_at": "INTEGER",
            "sort_order": "INTEGER NOT NULL DEFAULT 0",
        }
        existing = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(users)").fetchall()
        }
        for name, ddl in columns.items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
        if "enabled" not in existing:
            self.conn.execute("UPDATE users SET enabled = 1")

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    # ---------------- users ----------------

    def upsert_user(
        self,
        uid: int,
        name: str = "",
        avatar_url: str = "",
        image_dir: str = "",
        enabled: bool | None = None,
    ) -> None:
        now = int(time.time())
        enabled_value = None if enabled is None else (1 if enabled else 0)
        self.conn.execute(
            """
            INSERT INTO users(uid, name, avatar_url, image_dir, enabled, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                name = CASE WHEN excluded.name <> '' THEN excluded.name ELSE users.name END,
                avatar_url = CASE WHEN excluded.avatar_url <> '' THEN excluded.avatar_url ELSE users.avatar_url END,
                image_dir = CASE WHEN excluded.image_dir <> '' THEN excluded.image_dir ELSE users.image_dir END,
                enabled = COALESCE(?, users.enabled),
                updated_at = excluded.updated_at
            """,
            (
                uid,
                name or "",
                avatar_url or "",
                image_dir or "",
                1 if enabled_value is None else enabled_value,
                now,
                now,
                enabled_value,
            ),
        )

    def get_user(self, uid: int):
        return self.conn.execute("SELECT * FROM users WHERE uid = ?", (uid,)).fetchone()

    def get_user_by_name(self, name: str):
        return self.conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()

    def set_user_image_dir(self, uid: int, image_dir: str) -> None:
        self.conn.execute(
            "UPDATE users SET image_dir = ?, updated_at = ? WHERE uid = ?",
            (image_dir, int(time.time()), uid),
        )

    def set_user_enabled(self, uid: int, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE users SET enabled = ?, updated_at = ? WHERE uid = ?",
            (1 if enabled else 0, int(time.time()), uid),
        )

    def set_user_avatar(
        self, uid: int, avatar_file: str, avatar_source: str, updated_at: int | None = None
    ) -> None:
        self.conn.execute(
            """
            UPDATE users SET avatar_file = ?, avatar_source = ?,
                avatar_updated_at = ?, updated_at = ?
            WHERE uid = ?
            """,
            (
                avatar_file or "",
                avatar_source or "",
                int(updated_at or time.time()),
                int(time.time()),
                uid,
            ),
        )

    def list_users_for_sync(self) -> list:
        return self.conn.execute(
            """
            SELECT * FROM users
            WHERE enabled = 1
            ORDER BY sort_order, name COLLATE NOCASE, uid
            """
        ).fetchall()

    def set_last_dynamic_id(self, uid: int, dynamic_id: int | str, synced_at: int | None = None) -> None:
        value = str(int(dynamic_id))
        ts = int(synced_at) if synced_at is not None else None
        self.conn.execute(
            """
            UPDATE users SET
                last_dynamic_id = CASE
                    WHEN CAST(? AS INTEGER) > CAST(last_dynamic_id AS INTEGER) THEN ?
                    ELSE last_dynamic_id
                END,
                last_sync_at = CASE WHEN ? IS NOT NULL THEN ? ELSE last_sync_at END,
                updated_at = ?
            WHERE uid = ?
            """,
            (value, value, ts, ts, int(time.time()), uid),
        )

    def list_users_with_counts(self):
        return self.conn.execute(
            """
            SELECT u.uid, u.name, u.avatar_url, u.avatar_file, u.avatar_updated_at,
                   u.image_dir, u.last_sync_at, u.last_dynamic_id, u.enabled,
                   COALESCE(SUM(CASE WHEN p.image_count > 0 THEN 1 ELSE 0 END), 0) AS image_post_count,
                   COALESCE(SUM(p.image_count), 0) AS image_count
            FROM users u
            LEFT JOIN posts p ON p.uid = u.uid
            GROUP BY u.uid
            ORDER BY u.sort_order, u.name COLLATE NOCASE, u.uid
            """
        ).fetchall()

    # ---------------- posts ----------------

    def upsert_post(self, post: PostRecord) -> int:
        now = int(time.time())
        search_text = f"{post.title}\n{post.description}".lower()
        row = self.conn.execute(
            """
            INSERT INTO posts(
                dynamic_id, uid, dynamic_type, major_type, kind, title, description,
                published_at, collected_at, is_charge, is_accessible, image_count, search_text
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dynamic_id) DO UPDATE SET
                uid = excluded.uid,
                dynamic_type = excluded.dynamic_type,
                major_type = excluded.major_type,
                kind = excluded.kind,
                title = excluded.title,
                description = excluded.description,
                published_at = excluded.published_at,
                is_charge = CASE WHEN posts.image_count > 0 THEN posts.is_charge ELSE excluded.is_charge END,
                is_accessible = CASE WHEN posts.image_count > 0 THEN posts.is_accessible ELSE excluded.is_accessible END,
                image_count = MAX(posts.image_count, excluded.image_count),
                search_text = excluded.search_text
            RETURNING id
            """,
            (
                post.dynamic_id,
                post.uid,
                post.dynamic_type,
                post.major_type,
                post.kind,
                post.title,
                post.description,
                int(post.published_at or 0),
                now,
                1 if post.is_charge else 0,
                1 if post.is_accessible else 0,
                len(post.images),
                search_text,
            ),
        ).fetchone()
        return int(row["id"])

    def upsert_images(self, post_id: int, images: list[ImageRecord]) -> list[int]:
        ids: list[int] = []
        for image in images:
            row = self.conn.execute(
                """
                INSERT INTO images(
                    post_id, url, url_clean, file_name, width, height,
                    remote_size, local_size, status
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id, url) DO UPDATE SET
                    width = excluded.width,
                    height = excluded.height,
                    remote_size = excluded.remote_size,
                    status = CASE
                        WHEN images.status = 'ok' THEN 'ok'
                        WHEN images.status IN ('dead', 'restricted') THEN images.status
                        WHEN excluded.status = 'ok' THEN 'ok'
                        ELSE images.status
                    END,
                    local_size = CASE
                        WHEN images.local_size IS NOT NULL AND images.local_size > 0 THEN images.local_size
                        ELSE excluded.local_size
                    END
                RETURNING id
                """,
                (
                    post_id,
                    image.url,
                    image.url_clean,
                    image.file_name,
                    int(image.width or 0),
                    int(image.height or 0),
                    image.remote_size,
                    image.local_size,
                    image.status,
                ),
            ).fetchone()
            ids.append(int(row["id"]))
        if images:
            self.conn.execute(
                """
                UPDATE posts SET image_count = MAX(
                    image_count,
                    (SELECT COUNT(*) FROM images WHERE post_id = ?)
                ) WHERE id = ?
                """,
                (int(post_id), int(post_id)),
            )
        return ids

    @staticmethod
    def _post_where(
        uid: int | None = None,
        q: str | None = None,
        include_charge: bool = False,
    ) -> tuple[str, list]:
        where = ["(p.image_count > 0 OR (p.kind = 'charge' AND ? = 1))"]
        params: list = [1 if include_charge else 0]
        if uid:
            where.append("p.uid = ?")
            params.append(int(uid))
        if q:
            where.append("p.search_text LIKE ?")
            params.append(f"%{q.strip().lower()}%")
        return " AND ".join(where), params

    def list_posts_with_images(
        self,
        limit: int = 24,
        offset: int = 0,
        uid: int | None = None,
        q: str | None = None,
        sort: str = "desc",
        include_charge: bool = False,
    ) -> tuple[list[dict], bool]:
        where, params = self._post_where(uid=uid, q=q, include_charge=include_charge)
        direction = "ASC" if str(sort).lower() == "asc" else "DESC"
        rows = self.conn.execute(
            f"""
            SELECT p.*, u.name, u.avatar_url, u.avatar_file, u.avatar_updated_at, u.image_dir
            FROM posts p JOIN users u ON u.uid = p.uid
            WHERE {where}
            ORDER BY p.published_at {direction}, p.id {direction}
            LIMIT ? OFFSET ?
            """,
            (*params, int(limit) + 1, int(offset)),
        ).fetchall()
        has_more = len(rows) > int(limit)
        rows = rows[: int(limit)]
        if not rows:
            return [], False

        post_ids = [int(r["id"]) for r in rows]
        placeholders = ",".join("?" for _ in post_ids)
        image_rows = self.conn.execute(
            f"SELECT * FROM images WHERE post_id IN ({placeholders}) ORDER BY id",
            post_ids,
        ).fetchall()
        images_by_post: dict[int, list[dict]] = {}
        for image in image_rows:
            images_by_post.setdefault(int(image["post_id"]), []).append(dict(image))

        result = []
        for row in rows:
            item = dict(row)
            images = images_by_post.get(int(row["id"]), [])
            for image in images:
                image["ok"] = image.get("status") == "ok"
            item["images"] = images
            item["time_str"] = format_ts(item.get("published_at"))
            item["bili_url"] = f"https://t.bilibili.com/{item['dynamic_id']}"
            name = item.get("name") or str(item.get("uid") or "")
            item["initial"] = name[:1] if name else "?"
            item["ok_images"] = sum(1 for image in images if image.get("ok"))
            result.append(item)
        return result, has_more

    @staticmethod
    def _image_where(uid: int | None = None, q: str | None = None) -> tuple[str, list]:
        where = ["i.status = 'ok'"]
        params: list = []
        if uid:
            where.append("p.uid = ?")
            params.append(int(uid))
        if q:
            where.append("p.search_text LIKE ?")
            params.append(f"%{q.strip().lower()}%")
        return " AND ".join(where), params

    def list_images_with_posts(
        self,
        limit: int = 48,
        offset: int = 0,
        uid: int | None = None,
        q: str | None = None,
        sort: str = "desc",
    ) -> tuple[list[dict], bool]:
        where, params = self._image_where(uid=uid, q=q)
        direction = "ASC" if str(sort).lower() == "asc" else "DESC"
        rows = self.conn.execute(
            f"""
            SELECT i.id AS image_id, i.file_name, i.width, i.height, i.url,
                   p.id AS post_id, p.dynamic_id, p.title AS post_title,
                   p.description AS post_description, p.published_at, p.is_charge,
                   u.uid, u.name, u.avatar_updated_at
            FROM images i
            JOIN posts p ON p.id = i.post_id
            JOIN users u ON u.uid = p.uid
            WHERE {where}
            ORDER BY p.published_at {direction}, p.id {direction}, i.id ASC
            LIMIT ? OFFSET ?
            """,
            (*params, int(limit) + 1, int(offset)),
        ).fetchall()
        has_more = len(rows) > int(limit)
        rows = rows[: int(limit)]
        items = []
        for row in rows:
            item = dict(row)
            item["time_str"] = format_ts(item.get("published_at"))
            item["bili_url"] = f"https://t.bilibili.com/{item['dynamic_id']}"
            items.append(item)
        return items, has_more

    def count_images_with_posts(
        self,
        uid: int | None = None,
        q: str | None = None,
    ) -> int:
        where, params = self._image_where(uid=uid, q=q)
        row = self.conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM images i
            JOIN posts p ON p.id = i.post_id
            JOIN users u ON u.uid = p.uid
            WHERE {where}
            """,
            params,
        ).fetchone()
        return int(row["n"])

    def count_posts(
        self,
        uid: int | None = None,
        q: str | None = None,
        include_charge: bool = False,
    ) -> int:
        where, params = self._post_where(uid=uid, q=q, include_charge=include_charge)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM posts p WHERE {where}", params
        ).fetchone()
        return int(row["n"])

    # ---------------- images / downloads ----------------

    def update_image_status(
        self,
        image_id: int,
        status: str,
        local_size: int | None = None,
        http_status: int | None = None,
        error: str | None = None,
        increment_attempts: int = 0,
        downloaded_at: int | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE images SET
                status = ?,
                local_size = COALESCE(?, local_size),
                http_status = COALESCE(?, http_status),
                last_error = ?,
                attempts = attempts + ?,
                downloaded_at = COALESCE(?, downloaded_at)
            WHERE id = ?
            """,
            (
                status,
                local_size,
                http_status,
                error,
                int(increment_attempts),
                downloaded_at,
                int(image_id),
            ),
        )

    def get_images_for_post(
        self, post_id: int, statuses: tuple[str, ...] = ("pending", "failed", "missing")
    ) -> list[dict]:
        placeholders = ",".join("?" for _ in statuses)
        rows = self.conn.execute(
            f"""
            SELECT i.*, u.uid, u.image_dir, p.published_at, p.dynamic_id
            FROM images i
            JOIN posts p ON p.id = i.post_id
            JOIN users u ON u.uid = p.uid
            WHERE i.post_id = ? AND i.status IN ({placeholders})
            ORDER BY i.id
            """,
            (int(post_id), *statuses),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_retry_counts(self, uid: int | None = None) -> dict[str, int]:
        sql = """
            SELECT i.status, COUNT(*) AS n
            FROM images i JOIN posts p ON p.id = i.post_id
            WHERE 1=1
        """
        params: list = []
        if uid:
            sql += " AND p.uid = ?"
            params.append(int(uid))
        sql += " GROUP BY i.status"
        return {row["status"]: int(row["n"]) for row in self.conn.execute(sql, params)}

    def get_images_for_posts(self, post_ids: list[int]) -> list[dict]:
        if not post_ids:
            return []
        placeholders = ",".join("?" for _ in post_ids)
        rows = self.conn.execute(
            f"""
            SELECT i.*, u.uid, u.image_dir, p.published_at, p.dynamic_id
            FROM images i
            JOIN posts p ON p.id = i.post_id
            JOIN users u ON u.uid = p.uid
            WHERE i.post_id IN ({placeholders})
              AND i.status = 'pending'
            ORDER BY i.id
            """,
            post_ids,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_repair_images(
        self,
        uid: int | None = None,
        limit: int = 0,
        statuses: tuple[str, ...] = ("pending", "failed", "missing"),
        max_attempts: int = 5,
        uids: list[int] | None = None,
    ) -> list[dict]:
        placeholders = ",".join("?" for _ in statuses)
        sql = f"""
            SELECT i.*, u.uid, u.image_dir, p.published_at, p.dynamic_id
            FROM images i
            JOIN posts p ON p.id = i.post_id
            JOIN users u ON u.uid = p.uid
            WHERE i.status IN ({placeholders}) AND i.attempts < ?
        """
        params: list = [*statuses, int(max_attempts)]
        if uids:
            marks = ",".join("?" for _ in uids)
            sql += f" AND p.uid IN ({marks})"
            params.extend(int(value) for value in uids)
        elif uid:
            sql += " AND p.uid = ?"
            params.append(int(uid))
        sql += " ORDER BY i.id"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_image_with_user(self, image_id: int):
        return self.conn.execute(
            """
            SELECT i.*, u.uid, u.image_dir, u.name, p.published_at, p.dynamic_id
            FROM images i
            JOIN posts p ON p.id = i.post_id
            JOIN users u ON u.uid = p.uid
            WHERE i.id = ?
            """,
            (int(image_id),),
        ).fetchone()

    def get_images_with_user(self, uid: int | None = None) -> list[dict]:
        sql = """
            SELECT i.id, i.post_id, i.file_name, i.status, i.local_size, i.url,
                   i.attempts, u.uid, u.image_dir, u.name, p.published_at, p.dynamic_id
            FROM images i
            JOIN posts p ON p.id = i.post_id
            JOIN users u ON u.uid = p.uid
        """
        params: list = []
        if uid:
            sql += " WHERE u.uid = ?"
            params.append(int(uid))
        sql += " ORDER BY i.id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # ---------------- jobs ----------------

    def create_job(self, kind: str, target: str = "") -> int:
        row = self.conn.execute(
            "INSERT INTO jobs(kind, target, status, created_at) VALUES(?, ?, 'queued', ?) RETURNING id",
            (kind, target or "", int(time.time())),
        ).fetchone()
        return int(row["id"])

    def update_job(self, job_id: int, **fields) -> None:
        allowed = {
            "status", "started_at", "finished_at", "total", "done",
            "message", "log_path", "error",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        self.conn.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            (*updates.values(), int(job_id)),
        )

    def get_job(self, job_id: int):
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()

    def list_jobs(self, limit: int = 50) -> list:
        return self.conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()

    def active_job(self):
        return self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('queued', 'running', 'cancelling')
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()

    def mark_interrupted_jobs(self) -> int:
        row = self.conn.execute(
            """
            UPDATE jobs SET status = 'interrupted', finished_at = ?,
                message = '服务重启，任务被中断'
            WHERE status IN ('queued', 'running', 'cancelling')
            RETURNING id
            """,
            (int(time.time()),),
        ).fetchall()
        return len(row)

    # ---------------- stats / runs ----------------

    def stats(self) -> dict:
        row = self.conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM users) AS users,
                (SELECT COUNT(*) FROM users WHERE enabled = 1) AS enabled_users,
                (SELECT COUNT(*) FROM users WHERE avatar_file <> '') AS avatars,
                (SELECT COUNT(*) FROM posts) AS posts,
                (SELECT COUNT(*) FROM posts WHERE image_count > 0) AS image_posts,
                (SELECT COUNT(*) FROM posts WHERE kind = 'charge' AND image_count = 0) AS charge_posts,
                (SELECT COUNT(*) FROM images) AS images,
                (SELECT COUNT(*) FROM images WHERE status = 'ok') AS ok_images,
                (SELECT COUNT(*) FROM images WHERE status IN ('missing', 'failed')) AS missing_images,
                (SELECT COUNT(*) FROM images WHERE status = 'dead') AS dead_images,
                (SELECT COUNT(*) FROM images WHERE status = 'pending') AS pending_images,
                (SELECT COALESCE(SUM(local_size), 0) FROM images) AS bytes
            """
        ).fetchone()
        return dict(row)

    def start_sync_run(self, uid: int) -> int:
        now = int(time.time())
        row = self.conn.execute(
            "INSERT INTO sync_runs(uid, started_at) VALUES(?, ?) RETURNING id",
            (int(uid), now),
        ).fetchone()
        return int(row["id"])

    def finish_sync_run(
        self,
        run_id: int,
        status: str,
        pages: int = 0,
        new_posts: int = 0,
        new_images: int = 0,
        message: str = "",
    ) -> None:
        self.conn.execute(
            """
            UPDATE sync_runs SET finished_at = ?, status = ?, pages = ?,
                new_posts = ?, new_images = ?, message = ?
            WHERE id = ?
            """,
            (int(time.time()), status, int(pages), int(new_posts), int(new_images), message, int(run_id)),
        )

    def export_user_data(self, uid: int) -> dict | None:
        user = self.get_user(uid)
        if not user:
            return None
        posts = self.conn.execute(
            "SELECT * FROM posts WHERE uid = ? ORDER BY published_at DESC, id DESC",
            (int(uid),),
        ).fetchall()
        images = self.conn.execute(
            """
            SELECT i.*, p.dynamic_id
            FROM images i JOIN posts p ON p.id = i.post_id
            WHERE p.uid = ? ORDER BY i.id
            """,
            (int(uid),),
        ).fetchall()
        images_by_post: dict[str, list[dict]] = {}
        for image in images:
            images_by_post.setdefault(str(image["dynamic_id"]), []).append(dict(image))

        post_list = []
        for post in posts:
            item = dict(post)
            item.pop("id", None)
            item["images"] = images_by_post.get(str(post["dynamic_id"]), [])
            post_list.append(item)
        return {"user": dict(user), "posts": post_list}

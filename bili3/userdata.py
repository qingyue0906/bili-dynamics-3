from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from .config import Config
from .db import Database
from .sync import load_user_list
from .utils import slugify_name

logger = logging.getLogger(__name__)


def _move_to_legacy(path: Path, config: Config, name: str) -> Path | None:
    target_dir = config.legacy_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    if target.exists():
        target = target_dir / f"{target.stem}-{int(time.time())}{target.suffix}"
    try:
        shutil.move(str(path), str(target))
        logger.warning("旧用户列表已移动到：%s", target)
        return target
    except OSError as exc:
        logger.error("移动旧用户列表失败 %s：%s", path, exc)
        return None


def prepare_runtime(config: Config) -> None:
    """Upgrade the database and import the legacy root user_list.txt once."""
    db = Database(config.db_path)
    try:
        interrupted = db.mark_interrupted_jobs()
        if interrupted:
            logger.warning("发现 %d 个未完成后台任务，已标记为 interrupted", interrupted)

        legacy_list = config.base_dir / "user_list.txt"
        if legacy_list.is_file():
            pairs = load_user_list(legacy_list)
            if pairs:
                keep_uids = {uid for _, uid in pairs}
                for name, uid in pairs:
                    user = db.get_user(uid)
                    if user is None:
                        image_dir = config.images_dir / f"{uid}_{slugify_name(name or str(uid))}"
                        image_dir.mkdir(parents=True, exist_ok=True)
                        db.upsert_user(
                            uid,
                            name=name or str(uid),
                            image_dir=str(image_dir.relative_to(config.data_dir)),
                            enabled=True,
                        )
                    else:
                        db.upsert_user(
                            uid,
                            name=name or user["name"],
                            image_dir=user["image_dir"],
                            enabled=True,
                        )
                for user in db.list_users_with_counts():
                    if int(user["uid"]) not in keep_uids:
                        db.set_user_enabled(int(user["uid"]), False)
                logger.info("已从 user_list.txt 导入 %d 个启用用户", len(pairs))
            _move_to_legacy(legacy_list, config, "user_list.migrated.txt")
    finally:
        db.close()

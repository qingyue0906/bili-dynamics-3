from __future__ import annotations

from pathlib import Path

from bili3.config import load_config
from bili3.db import Database
from bili3.userdata import prepare_runtime


def test_legacy_user_list_sets_enabled(tmp_path):
    config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data")
    db = Database(config.db_path)
    db.upsert_user(1, name="Alice", image_dir="images/1_alice")
    db.upsert_user(2, name="Bob", image_dir="images/2_bob")
    db.close()
    (tmp_path / "user_list.txt").write_text("Alice: 1\n", encoding="utf-8")

    prepare_runtime(config)

    db = Database(config.db_path)
    try:
        assert db.get_user(1)["enabled"] == 1
        assert db.get_user(2)["enabled"] == 0
        assert len(db.list_users_for_sync()) == 1
    finally:
        db.close()
    assert not (tmp_path / "user_list.txt").exists()
    assert (config.legacy_dir / "user_list.migrated.txt").is_file()

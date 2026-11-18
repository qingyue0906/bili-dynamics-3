from __future__ import annotations

import json
from pathlib import Path

from bili3.config import load_config
from bili3.db import Database
from bili3.migrate import migrate_v2


def test_migrate_v2_and_idempotent(tmp_path):
    src = tmp_path / "v2"
    opus = src / "opus" / "Alice"
    opus.mkdir(parents=True)
    (opus / "abc.jpg").write_bytes(b"fake-image")
    info = [
        {
            "dynamic_id": 100,
            "time": "2026-01-01 12:00:00",
            "type": "DYNAMIC_TYPE_DRAW",
            "item": {
                "title": "第一条",
                "description": "描述",
                "pictures": [
                    "https://i0.hdslb.com/bfs/new_dyn/abc.jpg?x=1",
                    "https://i0.hdslb.com/bfs/new_dyn/missing.jpg",
                ],
            },
        },
        {
            "dynamic_id": 101,
            "time": "2026-01-02 12:00:00",
            "type": "DYNAMIC_TYPE_DRAW",
            "item": {"title": "第二条", "description": "", "pictures": []},
        },
    ]
    (opus / "__info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    (src / "user_list.txt").write_text("Alice: 1\n", encoding="utf-8")

    config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data")
    db = Database(config.db_path)
    try:
        stats = migrate_v2(db, config, src)
        assert stats.users == 1
        assert stats.posts == 2
        assert stats.image_refs == 2
        assert stats.images == 2
        assert stats.ok_images == 1
        assert stats.missing_images == 1
        assert db.get_user(1)["last_dynamic_id"] == "101"

        # run again: no duplicate rows, no changed counts
        stats2 = migrate_v2(db, config, src)
        assert stats2.posts == 2
        assert db.stats()["posts"] == 2
        assert db.stats()["image_posts"] == 1
        assert db.stats()["images"] == 2
    finally:
        db.close()


def test_migrate_move_option(tmp_path):
    src = tmp_path / "v2"
    opus = src / "opus" / "Alice"
    opus.mkdir(parents=True)
    (opus / "a.jpg").write_bytes(b"x")
    info = [
        {
            "dynamic_id": 200,
            "time": "2026-01-01 00:00:00",
            "type": "DYNAMIC_TYPE_DRAW",
            "item": {
                "title": "",
                "description": "",
                "pictures": ["https://i0.hdslb.com/bfs/new_dyn/a.jpg"],
            },
        }
    ]
    (opus / "__info.json").write_text(json.dumps(info), encoding="utf-8")
    (src / "user_list.txt").write_text("Alice: 2\n", encoding="utf-8")

    config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data")
    db = Database(config.db_path)
    try:
        stats = migrate_v2(db, config, src, move=True)
        assert stats.moved_users == 1
        assert not opus.exists()
        new_dir = config.images_dir / "2_Alice"
        assert (new_dir / "a.jpg").is_file()
        assert Path(db.get_user(2)["image_dir"]) == new_dir.resolve()
    finally:
        db.close()

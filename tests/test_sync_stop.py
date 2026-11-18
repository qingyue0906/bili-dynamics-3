from __future__ import annotations

import asyncio

from bili3.config import load_config
from bili3.db import Database
from bili3.sync import sync_user


def page(items, has_more, offset=""):
    return {"items": items, "has_more": has_more, "offset": offset}


def item(dynamic_id):
    return {
        "id_str": str(dynamic_id),
        "type": "DYNAMIC_TYPE_DRAW",
        "modules": {
            "module_author": {"mid": 1, "name": "Alice", "pub_ts": 1700000000},
            "module_dynamic": {
                "major": {
                    "type": "MAJOR_TYPE_OPUS",
                    "opus": {
                        "pics": [
                            {"url": f"https://i0.hdslb.com/bfs/new_dyn/{dynamic_id}.jpg"}
                        ]
                    },
                }
            },
        },
    }


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def fetch_page(self, uid, offset=""):
        self.calls.append(offset)
        return self.pages[offset]


def test_incremental_stops_when_page_is_old(tmp_path):
    config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data", sleep=0)
    db = Database(config.db_path)
    db.upsert_user(1, name="Alice", image_dir=str(tmp_path / "images"))
    db.set_last_dynamic_id(1, 100)
    client = FakeClient(
        {
            "": page([item(102), item(101)], True, "p2"),
            "p2": page([item(99), item(98)], True, "p3"),
            "p3": page([item(97)], False),
        }
    )
    try:
        result = asyncio.run(
            sync_user(
                db,
                config,
                client,
                None,
                1,
                dry_run=True,
                no_download=True,
                max_pages=10,
            )
        )
        assert result.pages == 2
        assert client.calls == ["", "p2"]
        assert result.new_posts == 2
    finally:
        db.close()


def test_full_mode_does_not_advance_watermark_when_limited(tmp_path):
    config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data", sleep=0)
    db = Database(config.db_path)
    db.upsert_user(1, name="Alice", image_dir=str(tmp_path / "images"))
    db.set_last_dynamic_id(1, 100)
    client = FakeClient(
        {
            "": page([item(102)], True, "p2"),
            "p2": page([item(101)], False),
        }
    )
    try:
        asyncio.run(
            sync_user(
                db,
                config,
                client,
                None,
                1,
                full=True,
                no_download=True,
                max_pages=1,
            )
        )
        # page 2 was not crawled, so the old watermark must be preserved
        assert db.get_user(1)["last_dynamic_id"] == "100"

        asyncio.run(
            sync_user(
                db,
                config,
                client,
                None,
                1,
                full=True,
                no_download=True,
            )
        )
        assert db.get_user(1)["last_dynamic_id"] == "102"
    finally:
        db.close()


def test_full_mode_keeps_paging(tmp_path):
    config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data", sleep=0)
    db = Database(config.db_path)
    db.upsert_user(1, name="Alice", image_dir=str(tmp_path / "images"))
    db.set_last_dynamic_id(1, 100)
    client = FakeClient(
        {
            "": page([item(102)], True, "p2"),
            "p2": page([item(99)], True, "p3"),
            "p3": page([item(98)], False),
        }
    )
    try:
        result = asyncio.run(
            sync_user(
                db,
                config,
                client,
                None,
                1,
                full=True,
                dry_run=True,
                no_download=True,
            )
        )
        assert result.pages == 3
        assert len(client.calls) == 3
    finally:
        db.close()

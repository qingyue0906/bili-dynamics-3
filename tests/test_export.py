from __future__ import annotations

from bili3.config import load_config
from bili3.db import Database
from bili3.export import export_user, import_exports
from bili3.models import ImageRecord, PostRecord


def test_export_and_import(tmp_path):
    config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data")
    db = Database(config.db_path)
    db.upsert_user(1, name="Alice", image_dir="images/1_alice")
    post = PostRecord(
        dynamic_id="123",
        uid=1,
        title="标题",
        description="描述",
        published_at=1700000000,
        images=[
            ImageRecord(
                url="https://x/a.jpg",
                url_clean="https://x/a.jpg",
                file_name="a.jpg",
                status="ok",
                local_size=10,
            )
        ],
    )
    with db.transaction():
        post_id = db.upsert_post(post)
        db.upsert_images(post_id, post.images)
    path = export_user(db, config, 1)
    assert path is not None and path.is_file()
    db.close()

    config2 = load_config(base_dir=tmp_path, data_dir=tmp_path / "restored")
    db2 = Database(config2.db_path)
    try:
        stats = import_exports(db2, config2, directory=config.exports_dir)
        assert stats.users == 1
        assert stats.posts == 1
        assert stats.images == 1
        assert db2.stats()["posts"] == 1
        assert db2.stats()["ok_images"] == 1
        # idempotent
        stats2 = import_exports(db2, config2, directory=config.exports_dir)
        assert stats2.posts == 1
        assert db2.stats()["posts"] == 1
    finally:
        db2.close()

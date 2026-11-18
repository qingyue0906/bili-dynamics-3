from __future__ import annotations

from bili3.db import Database
from bili3.models import ImageRecord, PostRecord


def make_post(dynamic_id="100", uid=1, title="Hello 世界", images=None):
    return PostRecord(
        dynamic_id=dynamic_id,
        uid=uid,
        title=title,
        description="描述文字",
        published_at=1700000000,
        images=images or [],
    )


def test_upsert_is_idempotent(tmp_path):
    db = Database(tmp_path / "test.db")
    db.upsert_user(1, name="Alice", image_dir=str(tmp_path / "images"))
    images = [
        ImageRecord(
            url="https://i0.hdslb.com/bfs/new_dyn/a.jpg",
            url_clean="https://i0.hdslb.com/bfs/new_dyn/a.jpg",
            file_name="a.jpg",
            status="ok",
            local_size=10,
        )
    ]
    post = make_post(images=images)
    with db.transaction():
        post_id = db.upsert_post(post)
        db.upsert_images(post_id, images)
    with db.transaction():
        post_id = db.upsert_post(post)
        db.upsert_images(post_id, images)
    assert db.count_posts() == 1
    assert db.stats()["images"] == 1
    assert db.stats()["ok_images"] == 1
    db.close()


def test_search_and_pagination(tmp_path):
    db = Database(tmp_path / "test.db")
    db.upsert_user(1, name="Alice", image_dir=str(tmp_path / "images"))
    for index in range(3):
        post = make_post(dynamic_id=str(100 + index), title=f"标题 {index}")
        post.published_at = 1700000000 + index
        with db.transaction():
            post_id = db.upsert_post(post)
            db.upsert_images(
                post_id,
                [
                    ImageRecord(
                        url=f"https://x/{index}.jpg",
                        url_clean=f"https://x/{index}.jpg",
                        file_name=f"{index}.jpg",
                        status="ok",
                    )
                ],
            )
    posts, has_more = db.list_posts_with_images(limit=2)
    assert len(posts) == 2
    assert has_more is True
    posts2, has_more2 = db.list_posts_with_images(limit=2, offset=2)
    assert len(posts2) == 1
    assert has_more2 is False
    found = db.count_posts(q="标题")
    assert found == 3
    db.close()


def test_last_dynamic_id_never_goes_backwards(tmp_path):
    db = Database(tmp_path / "test.db")
    db.upsert_user(1, name="Alice", image_dir=str(tmp_path / "images"))
    db.set_last_dynamic_id(1, 500)
    db.set_last_dynamic_id(1, 400)
    user = db.get_user(1)
    assert user["last_dynamic_id"] == "500"
    db.set_last_dynamic_id(1, 600)
    user = db.get_user(1)
    assert user["last_dynamic_id"] == "600"
    db.close()

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


def test_user_enabled_avatar_and_jobs(tmp_path):
    db = Database(tmp_path / "test.db")
    db.upsert_user(1, name="Alice", image_dir=str(tmp_path / "images"), enabled=True)
    db.upsert_user(2, name="Bob", image_dir=str(tmp_path / "images2"), enabled=False)
    assert [row["uid"] for row in db.list_users_for_sync()] == [1]
    db.set_user_enabled(2, True)
    assert {row["uid"] for row in db.list_users_for_sync()} == {1, 2}
    db.set_user_avatar(2, "2_abc.webp", "https://example.com/face.jpg", 123)
    user = db.get_user(2)
    assert user["avatar_file"] == "2_abc.webp"
    assert user["avatar_updated_at"] == 123

    job_id = db.create_job("test", "target")
    db.update_job(job_id, status="running", total=10, done=2, message="working")
    job = db.get_job(job_id)
    assert job["status"] == "running"
    assert job["done"] == 2
    assert db.active_job()["id"] == job_id
    db.update_job(job_id, status="succeeded", finished_at=1, message="done")
    assert db.active_job() is None
    assert db.list_jobs()[0]["id"] == job_id
    db.close()


def test_retry_counts(tmp_path):
    db = Database(tmp_path / "test.db")
    db.upsert_user(1, name="Alice", image_dir=str(tmp_path / "images"))
    post = make_post(
        images=[
            ImageRecord(
                url="https://x/ok.jpg",
                url_clean="https://x/ok.jpg",
                file_name="ok.jpg",
                status="ok",
            ),
            ImageRecord(
                url="https://x/missing.jpg",
                url_clean="https://x/missing.jpg",
                file_name="missing.jpg",
                status="missing",
            ),
        ]
    )
    with db.transaction():
        post_id = db.upsert_post(post)
        db.upsert_images(post_id, post.images)
    counts = db.get_retry_counts(1)
    assert counts.get("ok") == 1
    assert counts.get("missing") == 1
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

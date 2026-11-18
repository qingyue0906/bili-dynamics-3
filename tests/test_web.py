from __future__ import annotations

from PIL import Image

from bili3.config import load_config
from bili3.db import Database
from bili3.models import ImageRecord, PostRecord
from bili3.web.app import create_app


def _seed(tmp_path):
    config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data")
    db = Database(config.db_path)
    user_dir = config.images_dir / "1_alice"
    user_dir.mkdir(parents=True)
    Image.new("RGB", (40, 20), (251, 114, 153)).save(user_dir / "pic.png")

    db.upsert_user(1, name="Alice", image_dir="images/1_alice")
    post = PostRecord(
        dynamic_id="123456789",
        uid=1,
        title="Hello 世界",
        description="一条测试动态",
        published_at=1700000000,
        images=[
            ImageRecord(
                url="https://i0.hdslb.com/bfs/new_dyn/pic.png",
                url_clean="https://i0.hdslb.com/bfs/new_dyn/pic.png",
                file_name="pic.png",
                width=40,
                height=20,
                status="ok",
                local_size=100,
            )
        ],
    )
    with db.transaction():
        post_id = db.upsert_post(post)
        db.upsert_images(post_id, post.images)
    db.close()
    return config, post_id


def test_pages_and_api(tmp_path):
    config, _ = _seed(tmp_path)
    app = create_app(config)
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 200
    assert "Alice".encode() in response.data
    assert "Hello 世界".encode() in response.data

    response = client.get("/user/1")
    assert response.status_code == 200
    assert "UID 1".encode() in response.data

    response = client.get("/search?q=hello")
    assert response.status_code == 200
    assert "Alice".encode() in response.data

    response = client.get("/api/posts?offset=0")
    assert response.status_code == 200
    payload = response.get_json()
    assert "Alice" in payload["html"]
    assert payload["next_offset"] == 0


def test_media_and_thumbnail(tmp_path):
    config, _ = _seed(tmp_path)
    app = create_app(config)
    client = app.test_client()

    response = client.get("/media/1")
    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG")

    response = client.get("/thumb/1")
    assert response.status_code == 200
    assert response.mimetype == "image/webp"
    assert len(response.data) > 20

    assert client.get("/media/9999").status_code == 404


def test_charge_placeholder(tmp_path):
    config, _ = _seed(tmp_path)
    db = Database(config.db_path)
    with db.transaction():
        db.upsert_post(
            PostRecord(
                dynamic_id="987654321",
                uid=1,
                kind="charge",
                title="充电专属动态",
                published_at=1700000100,
                is_charge=True,
                is_accessible=False,
                images=[],
            )
        )
    db.close()
    app = create_app(config)
    client = app.test_client()
    response = client.get("/search?q=充电&charge=1")
    assert response.status_code == 200
    assert "充电专属内容".encode() in response.data
    # hidden by default
    response = client.get("/search?q=充电")
    assert "充电专属内容".encode() not in response.data


def test_album_mode_and_pagination(tmp_path):
    config, _ = _seed(tmp_path)
    db = Database(config.db_path)
    for index in (2, 3):
        post = PostRecord(
            dynamic_id=str(123456780 + index),
            uid=1,
            title=f"相册 {index}",
            description="",
            published_at=1700000000 + index,
            images=[
                ImageRecord(
                    url=f"https://x/{index}.jpg",
                    url_clean=f"https://x/{index}.jpg",
                    file_name=f"{index}.jpg",
                    status="ok",
                )
            ],
        )
        with db.transaction():
            post_id = db.upsert_post(post)
            db.upsert_images(post_id, post.images)
    db.close()
    config.settings_store.save_options(
        web={"page_size": 1, "image_page_size": 1, "default_load": "pages"}
    )
    config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data")
    app = create_app(config)
    client = app.test_client()

    response = client.get("/?mode=images&load=pages&page=2")
    assert response.status_code == 200
    assert "album-item".encode() in response.data
    assert "第 2 / 3 页".encode() in response.data

    response = client.get("/api/images?offset=0&limit=2")
    payload = response.get_json()
    assert "album-item" in payload["html"]
    assert payload["next_offset"] == 2


def test_new_pages_and_csrf(tmp_path):
    config, _ = _seed(tmp_path)
    app = create_app(config)
    client = app.test_client()
    for path in ("/users", "/jobs", "/settings", "/dynamic"):
        response = client.get(path)
        assert response.status_code == 200
    assert client.post("/api/settings/options", json={}).status_code == 403
    response = client.post(
        "/api/settings/options", json={}, headers={"X-Bili3": "1"}
    )
    assert response.status_code == 200
    response = client.get("/api/settings")
    payload = response.get_json()
    assert payload["cookies"]["configured"] is False
    assert "sessdata" not in payload["cookies"] or isinstance(payload["cookies"]["sessdata"], bool)


def test_avatar_fallback_and_local_avatar(tmp_path):
    config, _ = _seed(tmp_path)
    db = Database(config.db_path)
    avatar_path = config.avatars_dir / "1_test.webp"
    Image.new("RGB", (160, 160), (0, 174, 236)).save(avatar_path, "WEBP")
    db.set_user_avatar(1, avatar_path.name, "https://example.com/face.jpg")
    db.close()

    app = create_app(config)
    client = app.test_client()
    response = client.get("/avatar/1")
    assert response.status_code == 200
    assert response.mimetype == "image/webp"
    response = client.get("/avatar/999999")
    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"


def test_user_toggle(tmp_path):
    config, _ = _seed(tmp_path)
    app = create_app(config)
    client = app.test_client()
    response = client.post(
        "/api/users/1/toggle",
        json={"enabled": False},
        headers={"X-Bili3": "1"},
    )
    assert response.status_code == 200
    db = Database(config.db_path)
    try:
        assert db.get_user(1)["enabled"] == 0
    finally:
        db.close()


def test_path_traversal_is_blocked(tmp_path):
    config, post_id = _seed(tmp_path)
    db = Database(config.db_path)
    with db.transaction():
        ids = db.upsert_images(
            post_id,
            [
                ImageRecord(
                    url="https://evil/outside.png",
                    url_clean="https://evil/outside.png",
                    file_name="../outside.png",
                    status="ok",
                )
            ],
        )
    db.close()
    app = create_app(config)
    client = app.test_client()
    assert client.get(f"/media/{ids[0]}").status_code == 404

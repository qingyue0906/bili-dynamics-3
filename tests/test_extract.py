from __future__ import annotations

from bili3.extract import extract_post


def make_item(major, dynamic_type="DYNAMIC_TYPE_DRAW", author=None, desc=None, id_str="123456789"):
    return {
        "id_str": id_str,
        "type": dynamic_type,
        "modules": {
            "module_author": author
            or {
                "mid": 42,
                "name": "测试UP",
                "face": "https://example.com/face.jpg",
                "pub_ts": 1700000000,
            },
            "module_dynamic": {
                "major": major,
                "desc": desc or {"text": ""},
            },
        },
    }


def test_new_opus():
    item = make_item(
        {
            "type": "MAJOR_TYPE_OPUS",
            "opus": {
                "title": "标题",
                "summary": {"text": "描述"},
                "pics": [
                    {
                        "url": "https://i0.hdslb.com/bfs/new_dyn/abc.jpg?x=1",
                        "width": 1200,
                        "height": 800,
                        "size": 123,
                    }
                ],
            },
        }
    )
    post = extract_post(item)
    assert post is not None
    assert post.dynamic_id == "123456789"
    assert post.kind == "opus"
    assert post.title == "标题"
    assert post.description == "描述"
    assert post.published_at == 1700000000
    assert len(post.images) == 1
    assert post.images[0].file_name == "abc.jpg"
    assert post.images[0].url_clean == "https://i0.hdslb.com/bfs/new_dyn/abc.jpg"
    assert post.images[0].width == 1200


def test_old_draw_with_opus_null():
    item = make_item(
        {
            "type": "MAJOR_TYPE_DRAW",
            "opus": None,
            "draw": {
                "items": [
                    {"src": "http://i0.hdslb.com/bfs/album/def.png", "width": 10, "height": 20}
                ]
            },
        }
    )
    post = extract_post(item)
    assert post is not None
    assert post.kind == "draw"
    assert len(post.images) == 1
    assert post.images[0].file_name == "def.png"
    assert post.images[0].url_clean.startswith("https://")


def test_charge_locked_does_not_crash():
    item = make_item(
        {
            "type": "MAJOR_TYPE_UPOWER_COMMON",
            "opus": None,
            "upower_common": {"title": "充电专属动态"},
        }
    )
    post = extract_post(item)
    assert post is not None
    assert post.is_charge is True
    assert post.is_accessible is False
    assert post.kind == "charge"
    assert post.images == []
    assert post.title == "充电专属动态"


def test_charge_with_null_pics_does_not_crash():
    item = make_item(
        {
            "type": "MAJOR_TYPE_OPUS",
            "opus": {"pics": None, "summary": None, "title": None},
        }
    )
    assert extract_post(item) is None


def test_forward_skipped_by_default_and_optional():
    orig = make_item(
        {
            "type": "MAJOR_TYPE_DRAW",
            "draw": {"items": [{"src": "https://i0.hdslb.com/bfs/album/orig.jpg"}]},
        },
        id_str="111",
        author={"mid": 99, "name": "原作者", "pub_ts": 1600000000},
    )
    item = {
        "id_str": "222",
        "type": "DYNAMIC_TYPE_FORWARD",
        "modules": {
            "module_author": {"mid": 42, "name": "转发者", "pub_ts": 1700000000},
            "module_dynamic": {"major": None, "desc": None},
        },
        "orig": orig,
    }
    assert extract_post(item) is None
    post = extract_post(item, include_forwards=True)
    assert post is not None
    assert post.dynamic_id == "222"
    assert post.kind == "forward"
    assert post.is_forward is True
    assert len(post.images) == 1


def test_missing_fields_and_pub_time_fallback():
    item = {
        "id_str": "333",
        "type": "DYNAMIC_TYPE_DRAW",
        "modules": {
            "module_author": {"uid": 42, "name": "无时间", "pub_time": "2022-07-19 12:31:51"},
            "module_dynamic": {
                "major": {
                    "type": "MAJOR_TYPE_OPUS",
                    "opus": {"pics": [{"url": "https://i0.hdslb.com/bfs/new_dyn/x.jpg"}]},
                },
                "desc": None,
            },
        },
    }
    post = extract_post(item)
    assert post is not None
    assert post.published_at > 0
    assert post.description == ""


def test_invalid_id_returns_none():
    assert extract_post({"id_str": "abc"}) is None
    assert extract_post(None) is None


def test_text_only_is_not_a_gallery_post():
    item = make_item(
        {"type": "MAJOR_TYPE_OPUS", "opus": {"summary": {"text": "只有文字"}}}
    )
    assert extract_post(item) is None


def test_duplicate_images_are_deduped():
    item = make_item(
        {
            "type": "MAJOR_TYPE_OPUS",
            "opus": {
                "pics": [
                    {"url": "https://i0.hdslb.com/bfs/new_dyn/a.jpg"},
                    {"url": "https://i0.hdslb.com/bfs/new_dyn/a.jpg"},
                ]
            },
        }
    )
    post = extract_post(item)
    assert post is not None
    assert len(post.images) == 1

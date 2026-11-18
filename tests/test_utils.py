from __future__ import annotations

from bili3.utils import (
    filename_from_url,
    normalize_download_url,
    parse_pub_time,
    slugify_name,
    strip_query,
)


def test_url_helpers():
    assert strip_query("https://x/a.jpg?foo=1#bar") == "https://x/a.jpg"
    assert filename_from_url("https://i0.hdslb.com/bfs/new_dyn/a.jpg?x=1") == "a.jpg"
    assert normalize_download_url("http://i0.hdslb.com/a.jpg?x=1") == "https://i0.hdslb.com/a.jpg"
    assert normalize_download_url("//i0.hdslb.com/a.jpg") == "https://i0.hdslb.com/a.jpg"
    assert filename_from_url("https://i0.hdslb.com/bfs/new_dyn/noext") == "noext.jpg"


def test_time_helpers():
    assert parse_pub_time("2022-07-19 12:31:51") > 0
    assert parse_pub_time("2022-07-19") > 0
    assert parse_pub_time("not a time") == 0
    assert parse_pub_time("1700000000") == 1700000000


def test_slugify_name():
    assert slugify_name('a<b>c:"d/e') == "a_b_c__d_e"
    assert slugify_name("") == "user"

from __future__ import annotations

from bili3.single import parse_dynamic_input, parse_uid_input


def test_parse_dynamic_input():
    assert parse_dynamic_input("https://t.bilibili.com/123456789012345678") == 123456789012345678
    assert parse_dynamic_input("https://www.bilibili.com/opus/123456789012345678") == 123456789012345678
    assert parse_dynamic_input("https://m.bilibili.com/dynamic/123456789012345678") == 123456789012345678
    assert parse_dynamic_input("123456789012345678") == 123456789012345678
    assert parse_dynamic_input("不是链接") is None


def test_parse_uid_input():
    assert parse_uid_input("123456789") == 123456789
    assert parse_uid_input("123456") == 123456
    assert parse_uid_input("https://space.bilibili.com/123456789") == 123456789
    assert parse_uid_input("nope") is None

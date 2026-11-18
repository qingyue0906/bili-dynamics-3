from __future__ import annotations

from pathlib import Path

from bili3.config import load_config
from bili3.settings import SettingsStore


def test_settings_store_defaults_and_mask(tmp_path):
    store = SettingsStore(tmp_path / "data")
    sync = store.sync()
    web = store.web()
    assert sync["sleep"] == 5.0
    assert web["host"] == "127.0.0.1"
    assert web["show_empty_posts"] is True
    assert web["show_missing_images"] is True
    assert store.masked_cookies()["configured"] is False

    store.save_cookies("sess", "jct", "buvid")
    masked = store.masked_cookies()
    assert masked["sessdata"] is True
    assert masked["configured"] is True
    assert store.cookies()["sessdata"] == "sess"

    saved = store.save_options(sync={"sleep": 2.5, "download_concurrency": 2}, web={"page_size": 12})
    assert saved["sync"]["sleep"] == 2.5
    assert saved["web"]["page_size"] == 12

    store.clear_cookies()
    assert store.masked_cookies()["configured"] is False


def test_legacy_env_is_imported_and_moved(tmp_path):
    root = tmp_path
    data = root / "data"
    (root / ".env").write_text(
        "BILI_SESSDATA=sess\nBILI_JCT=jct\nBILI_BUVID3=buvid\n", encoding="utf-8"
    )
    config = load_config(base_dir=root, data_dir=data)
    assert config.sessdata == "sess"
    assert not (root / ".env").exists()
    assert (data / "legacy" / "env.migrated").is_file()
    assert (data / "config" / "cookies.json").is_file()

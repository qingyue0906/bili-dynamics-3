from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

from PIL import Image

from bili3.avatars import download_avatar
from bili3.config import load_config
from bili3.db import Database


class _Handler(BaseHTTPRequestHandler):
    payload = b""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, fmt, *args):
        pass


def test_download_avatar_local_server(tmp_path):
    buffer = BytesIO()
    Image.new("RGB", (320, 240), (251, 114, 153)).save(buffer, "PNG")
    _Handler.payload = buffer.getvalue()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data")
        db = Database(config.db_path)
        db.upsert_user(1, name="Alice", image_dir="images/1_alice")
        url = f"http://127.0.0.1:{server.server_port}/face.png"
        path = asyncio.run(download_avatar(db, config, 1, url))
        assert path is not None and path.is_file()
        assert path.suffix == ".webp"
        with Image.open(path) as image:
            assert image.size == (160, 160)
        assert db.get_user(1)["avatar_file"] == path.name
        db.close()
    finally:
        server.shutdown()
        server.server_close()

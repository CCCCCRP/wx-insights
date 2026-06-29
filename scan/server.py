from __future__ import annotations

import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple

from worker.config import LOGIN_PUBLIC_URL, LOGIN_SERVER_HOST, LOGIN_SERVER_PORT

logger = logging.getLogger(__name__)

LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>微信扫码登录</title>
  <style>
    body { font-family: sans-serif; max-width: 420px; margin: 40px auto; padding: 0 16px; text-align: center; }
    h1 { font-size: 20px; }
    .tip { color: #666; font-size: 14px; line-height: 1.6; text-align: left; background: #f7f7f7; padding: 12px; border-radius: 8px; }
    img { width: 260px; height: 260px; margin: 16px 0; border: 1px solid #eee; }
  </style>
</head>
<body>
  <h1>微信公众号扫码登录</h1>
  <div class="tip">
    <b>推荐：</b>在<b>电脑浏览器</b>打开本页，再用<b>手机微信</b>扫下方二维码。
  </div>
  <img id="qr" src="/qrcode" alt="登录二维码">
  <script>
    setInterval(function() {
      document.getElementById('qr').src = '/qrcode?t=' + Date.now();
    }, 60000);
  </script>
</body>
</html>
"""


class _QRHolder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: bytes = b""
        self._media_type = "image/jpeg"

    def set(self, data: bytes, ext: str = "jpg") -> None:
        with self._lock:
            self._data = data
            self._media_type = "image/png" if ext == "png" else "image/jpeg"

    def get(self) -> Tuple[bytes, str]:
        with self._lock:
            return self._data, self._media_type


class LoginPageServer:
    """可选：本地/公网临时登录页（需 LOGIN_PUBLIC_URL 才写入邮件）。"""

    def __init__(self) -> None:
        self._holder = _QRHolder()
        self._http: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def public_login_url() -> Optional[str]:
        if not LOGIN_PUBLIC_URL:
            return None
        return f"{LOGIN_PUBLIC_URL.rstrip('/')}/login"

    def start(self, qrcode_bytes: bytes, ext: str = "jpg") -> str:
        self.stop()
        self._holder.set(qrcode_bytes, ext)
        handler = self._make_handler()
        self._http = ThreadingHTTPServer((LOGIN_SERVER_HOST, LOGIN_SERVER_PORT), handler)
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._thread.start()
        url = self.public_login_url() or f"http://{self._lan_ip()}:{LOGIN_SERVER_PORT}/login"
        logger.info("登录页已启动: %s", url)
        return url

    def update_qrcode(self, qrcode_bytes: bytes, ext: str = "jpg") -> None:
        self._holder.set(qrcode_bytes, ext)

    def stop(self) -> None:
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            self._http = None
        self._thread = None

    def _make_handler(self):
        holder = self._holder

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                logger.debug("login_page " + fmt, *args)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path in ("/", "/login"):
                    body = LOGIN_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/qrcode":
                    data, media = holder.get()
                    if not data:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", media)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_error(404)

        return Handler

    @staticmethod
    def _lan_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"

"""HTTP server: static dashboard plus JSON API."""

import json
import mimetypes
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import collectors, config, db

WEB_DIR = Path(__file__).resolve().parent / "web"

#: Time windows the dashboard offers, in seconds.
RANGES = {
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
    "7d": 604800,
    "30d": 2592000,
}
DEFAULT_RANGE = "1h"

_host_info: dict = {}
_host_info_lock = threading.Lock()


def host_info() -> dict:
    """Static host facts, computed once and reused."""
    global _host_info
    with _host_info_lock:
        if not _host_info:
            _host_info = collectors.host_info()
        return _host_info


class Handler(BaseHTTPRequestHandler):
    server_version = "ResourceMonitor"
    protocol_version = "HTTP/1.1"
    #: Set once a response goes out, so a late failure cannot emit a second one
    #: and desynchronise a keep-alive connection.
    _responded = False

    def log_message(self, fmt, *args):  # silence the per-request log
        pass

    def do_GET(self):
        self._responded = False
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        try:
            if route == "/":
                self._send_file(WEB_DIR / "index.html")
            elif route == "/api/current":
                self._send_json(self._current())
            elif route == "/api/series":
                self._send_json(self._series(parse_qs(parsed.query)))
            elif route == "/api/health":
                self._send_json({"ok": True, "ts": int(time.time())})
            else:
                self._send_static(route)
        except BrokenPipeError:
            pass
        except Exception as exc:  # one bad request must never take the server down
            if not self._responded:
                self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self.close_connection = True

    def _current(self) -> dict:
        sample = db.latest()
        info = host_info()
        return {
            "host": {**info, "uptime": int(time.time()) - info["boot_time"]},
            "sample": sample,
            "stale": sample is None or (time.time() - sample["ts"]) > config.INTERVAL * 3,
        }

    def _series(self, query: dict) -> dict:
        name = (query.get("range") or [DEFAULT_RANGE])[0]
        span = RANGES.get(name, RANGES[DEFAULT_RANGE])
        return {"range": name if name in RANGES else DEFAULT_RANGE, **db.series(span)}

    def _send_static(self, route: str) -> None:
        # Resolve inside WEB_DIR so ".." cannot escape the directory.
        candidate = (WEB_DIR / route.lstrip("/")).resolve()
        if candidate.is_file() and candidate.is_relative_to(WEB_DIR.resolve()):
            self._send_file(candidate)
        else:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _send_file(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith(("text/", "application/javascript")):
            ctype += "; charset=utf-8"
        self._respond(HTTPStatus.OK, ctype, body)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, allow_nan=False, default=str).encode("utf-8")
        self._respond(status, "application/json; charset=utf-8", body)

    def _respond(self, status: HTTPStatus, ctype: str, body: bytes) -> None:
        self._responded = True
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve() -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    httpd.daemon_threads = True
    return httpd

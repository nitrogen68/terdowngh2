import json
from http.server import BaseHTTPRequestHandler
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from terabox_browseruse import get_terabox_dlink


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, path):
        try:
            with open(path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return True
        except Exception:
            return False

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            if self._html(os.path.join(os.path.dirname(__file__), "index.html")):
                return
            if self._html(os.path.join(os.path.dirname(os.path.dirname(__file__)), "index.html")):
                return
        if path in ("/health", "/api/health"):
            self._json({"status": "ok"})
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/api/terabox/direct", "/api/terabox/direct/"):
            self._json({"error": "not found"}, 404)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"error": "invalid json"}, 400)
            return

        url = req.get("url")
        if not isinstance(url, str) or not url.strip():
            self._json({"error": "url required"}, 400)
            return

        if not url.lower().startswith(("http://", "https://")):
            self._json({"error": "invalid url"}, 400)
            return

        try:
            result = asyncio.run(get_terabox_dlink(url.strip()))
        except Exception as exc:
            self._json({"error": f"failed: {exc}"}, 500)
            return

        if not result.get("success"):
            self._json({"error": result.get("error", "Gagal mendapatkan dlink")}, 400)
            return

        self._json({"dlink": result["dlink"], "ok": True})

    def log_message(self, fmt, *args):
        pass


app = Handler

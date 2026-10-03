import json
import base64
from http.server import BaseHTTPRequestHandler
from urllib import request as urlrequest, error as urlerror
from urllib.parse import urlparse, parse_qs
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from terabox_browseruse import get_terabox_dlink


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
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
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return True
        except Exception:
            return False

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
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
        if path == "/api/debug":
            ndus = (os.environ.get("TERABOX_NDUS") or "").strip()
            if ndus.lower().startswith("ndus="):
                ndus = ndus.split("=", 1)[1].strip()
            self._json({"has_ndus": bool(ndus)})
            return
        if path == "/api/hls-chunk":
            self._proxy_hls_chunk()
            return
        self._json({"error": "not found"}, 404)

    def _proxy_hls_chunk(self):
        """Proxy TS chunk dari CDN Terabox dengan cookie NDUS server-side."""
        try:
            qs = parse_qs(urlparse(self.path).query)
            u_enc = (qs.get("u") or [None])[0]
            if not u_enc:
                self._json({"error": "missing u"}, 400)
                return
            # base64url decode (tambah padding bila perlu)
            pad = "=" * (-len(u_enc) % 4)
            chunk_url = base64.urlsafe_b64decode(u_enc + pad).decode("utf-8", "ignore")
            if not chunk_url.startswith("https://"):
                self._json({"error": "invalid url"}, 400)
                return
        except Exception:
            self._json({"error": "bad u"}, 400)
            return
        ndus = (os.environ.get("TERABOX_NDUS") or "").strip()
        if ndus.lower().startswith("ndus="):
            ndus = ndus.split("=", 1)[1].strip()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.terabox.com/",
            "Accept": "*/*",
        }
        if ndus:
            headers["Cookie"] = f"NDUS={ndus}"
        range_h = self.headers.get("Range")
        if range_h:
            headers["Range"] = range_h
        try:
            req = urlrequest.Request(chunk_url, headers=headers, method="GET")
            upstream = urlrequest.urlopen(req, timeout=30)
            try:
                ctype = upstream.headers.get("Content-Type", "video/mp2t")
                crange = upstream.headers.get("Content-Range")
                clen = upstream.headers.get("Content-Length")
                status = upstream.getcode()
            except Exception:
                upstream.close()
                raise
        except urlerror.HTTPError as exc:
            try:
                data = exc.read()
            except Exception:
                data = b""
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            return
        except Exception as exc:
            self._json({"error": f"proxy failed: {exc}"}, 502)
            return
        # Stream respons (jangan buffer seluruh chunk di memori)
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            if clen:
                self.send_header("Content-Length", clen)
            self.send_header("Accept-Ranges", "bytes")
            if crange:
                self.send_header("Content-Range", crange)
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            while True:
                buf = upstream.read(65536)
                if not buf:
                    break
                self.wfile.write(buf)
        finally:
            try:
                upstream.close()
            except Exception:
                pass
        return

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
            fid = req.get("fid")
            if fid is not None:
                fid = str(fid).strip() or None
            result = asyncio.run(get_terabox_dlink(url.strip(), fid=fid))
        except Exception as exc:
            self._json({"error": f"failed: {exc}"}, 500)
            return

        if not result.get("success"):
            self._json({"error": result.get("error", "Gagal mendapatkan dlink")}, 400)
            return

        payload = {
            "ok": True,
            "dlink": result["dlink"],
        }
        if result.get("filename"):
            payload["filename"] = result["filename"]
        if result.get("files"):
            payload["files"] = result["files"]
        if "ndus_cookie_ok" in result:
            payload["ndus_cookie_ok"] = result["ndus_cookie_ok"]
        if "dlink_probe" in result:
            payload["dlink_probe"] = result["dlink_probe"]
        if "dlink_probe_clean" in result:
            payload["dlink_probe_clean"] = result["dlink_probe_clean"]
        if "hls" in result:
            payload["hls"] = result["hls"]

        self._json(payload)

    def log_message(self, fmt, *args):
        pass


app = Handler

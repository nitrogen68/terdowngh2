import json
import base64
from http.server import BaseHTTPRequestHandler
from urllib import request as urlrequest, error as urlerror
from urllib.parse import urlparse, parse_qs
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from terabox_browseruse import get_terabox_dlink


# --- Messenger webhook (Terastream) ---
MESSENGER_VERIFY_TOKEN = "TsVrf_jjEAyMqaUK7BxsWb1Wl5S9mEFtjFrrzw"

FB_GRAPH_URL = "https://graph.facebook.com/v18.0/me/messages"

# Diagnostik: catat hit webhook masuk (metadata saja, tanpa isi pesan/token).
# In-memory: hilang saat cold start, tapi cukup untuk verifikasi real-time.
_webhook_hits = []


def _record_webhook_hit(body):
    try:
        import time
        info = {"ts": int(time.time())}
        if isinstance(body, dict):
            info["object"] = body.get("object")
            entries = body.get("entry") or []
            info["entries"] = len(entries)
            n_msg = 0
            for e in entries:
                for ev in (e.get("messaging") or []):
                    if isinstance(ev, dict) and ev.get("message"):
                        n_msg += 1
            info["messages"] = n_msg
        else:
            info["object"] = None
        _webhook_hits.append(info)
        del _webhook_hits[:-20]
    except Exception:
        pass


def _send_messenger(psid, text):
    """Kirim pesan teks via Messenger Send API. Token dari env MESSENGER_PAGE_TOKEN."""
    token = (os.environ.get("MESSENGER_PAGE_TOKEN") or "").strip()
    if not token or not psid or not text:
        return False
    payload = json.dumps({
        "recipient": {"id": str(psid)},
        "messaging_type": "RESPONSE",
        "message": {"text": text[:2000]},
    }).encode("utf-8")
    try:
        req = urlrequest.Request(
            FB_GRAPH_URL + "?access_token=" + token,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


def _extract_terabox_url(text):
    """Cari URL Terabox/1024terabox/mirrobox di teks pesan."""
    m = re.search(r"https?://[^\s]*?(?:terabox|1024terabox|mirrobox|teraboxapp)[^\s]*", text or "", re.I)
    return m.group(0) if m else None


def _handle_messenger_event(body):
    """Proses event webhook Messenger: balas link Terabox dengan hasil."""
    if not isinstance(body, dict) or body.get("object") != "page":
        return
    for entry in body.get("entry", []) or []:
        for ev in entry.get("messaging", []) or []:
            psid = (ev.get("sender") or {}).get("id")
            msg = ev.get("message") or {}
            text = msg.get("text", "") or ""
            if not psid or not text or msg.get("is_echo"):
                continue
            url = _extract_terabox_url(text)
            if not url:
                _send_messenger(psid,
                    "Halo! 👋 Kirim link Terabox (mis. https://1024terabox.com/s/xxxx) "
                    "dan aku ambilkan daftar file-nya. 🚀")
                continue
            _send_messenger(psid, "Siap! Lagi ambil info link-nya, tunggu sebentar ya... ⏳")
            try:
                result = asyncio.run(get_terabox_dlink(url.strip()))
            except Exception as exc:
                _send_messenger(psid,
                    f"Yah, gagal proses link-nya ({type(exc).__name__}). Coba lagi nanti ya. 🙏")
                continue
            if not result.get("success"):
                _send_messenger(psid,
                    f"Gagal ambil info: {result.get('error', 'unknown')}. "
                    "Pastikan link-nya benar & publik ya.")
                continue
            lines = ["✅ Link berhasil diproses!"]
            files = result.get("files") or []
            if files:
                lines.append(f"📁 {len(files)} item ditemukan:")
                for f in files[:10]:
                    nm = f.get("name") or "file"
                    sz = f.get("size") or ""
                    isdir = f.get("isdir")
                    icon = "📁" if isdir else "🎬"
                    lines.append(f"{icon} {nm} {sz}".strip())
                if len(files) > 10:
                    lines.append(f"...dan {len(files) - 10} item lainnya.")
            elif result.get("filename"):
                lines.append(f"📄 {result['filename']}")
            lines.append("")
            lines.append("Streaming & download di web:")
            lines.append("https://terdowngh.vercel.app/")
            _send_messenger(psid, "\n".join(lines))


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
        if path == "/api/webhook-debug":
            # Diagnostik webhook Messenger: hit masuk terakhir + status token (tanpa nilai).
            self._json({
                "hits": _webhook_hits[-20:],
                "has_page_token": bool((os.environ.get("MESSENGER_PAGE_TOKEN") or "").strip()),
            })
            return
        if path == "/api/hls-chunk":
            self._proxy_hls_chunk()
            return
        if path in ("/webhook", "/api/webhook"):
            # Verifikasi webhook Messenger (Meta kirim hub.challenge)
            qs = parse_qs(urlparse(self.path).query)
            mode = (qs.get("hub.mode") or [None])[0]
            token = (qs.get("hub.verify_token") or [None])[0]
            challenge = (qs.get("hub.challenge") or [None])[0]
            if mode == "subscribe" and token == MESSENGER_VERIFY_TOKEN and challenge:
                data = challenge.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._json({"error": "verification failed"}, 403)
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
        if path in ("/webhook", "/api/webhook"):
            # Terima event Messenger: 200 dulu (biar Meta tidak retry), lalu proses & balas
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
            except Exception:
                body = {}
            _record_webhook_hit(body)
            self._json({"status": "EVENT_RECEIVED"})
            try:
                _handle_messenger_event(body)
            except Exception:
                pass
            return
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
            path = req.get("path")
            if path is not None:
                path = str(path).strip() or None
            result = asyncio.run(get_terabox_dlink(url.strip(), fid=fid, path=path))
        except Exception as exc:
            import traceback
            err_detail = f"{type(exc).__name__}: {exc}"
            self._json({"error": "Gagal mendapatkan link download.", "detail": err_detail, "traceback": traceback.format_exc()[-500:]}, 500)
            return

        if not result.get("success"):
            self._json({"error": "Gagal mendapatkan link download.", "detail": result.get("error", "Unknown backend error")}, 400)
            return

        payload = {
            "ok": True,
            "dlink": result["dlink"],
        }
        if result.get("filename"):
            payload["filename"] = result["filename"]
        if result.get("files"):
            payload["files"] = result["files"]
        if "hls" in result:
            payload["hls"] = result["hls"]

        self._json(payload)

    def log_message(self, fmt, *args):
        pass


app = Handler

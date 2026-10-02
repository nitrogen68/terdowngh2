import json
import os
import re
from urllib import request, error
from urllib.parse import quote, urlparse, parse_qs

BROWSERLESS_TOKEN = os.environ.get("BROWSERLESS_TOKEN")
BROWSERLESS_ENDPOINT = "https://production-sfo.browserless.io/function"

# Public fallbacks (best-effort, may break when Terabox tightens anti-bot)
PUBLIC_FALLBACKS = [
    "https://terabox-worker.robinkumarshakya103.workers.dev/api?url={url}",
    "https://tbx-proxy.shakir-ansarii075.workers.dev/?mode=resolve&surl={surl}",
]


class BrowserlessClient:
    def _post_function(self, code: str, timeout_ms: int = 90000):
        if not BROWSERLESS_TOKEN:
            raise RuntimeError("BROWSERLESS_TOKEN belum dikonfigurasi")

        endpoint = (
            f"{BROWSERLESS_ENDPOINT}?token={quote(BROWSERLESS_TOKEN, safe='')}"
            f"&timeout={timeout_ms}"
        )
        req = request.Request(
            endpoint,
            data=code.encode("utf-8"),
            headers={
                "Content-Type": "application/javascript",
                "Cache-Control": "no-cache",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=(timeout_ms / 1000) + 20) as resp:
                raw = resp.read().decode("utf-8", "ignore")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return {"data": raw}
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "ignore")
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw or f"HTTP {exc.code}: {exc.reason}"
            return {"error": detail, "http_status": exc.code}
        except Exception as exc:
            return {"error": str(exc)}


def _extract_surl(url: str) -> str | None:
    """Extract shorturl code from any Terabox share URL variant."""
    try:
        parsed = urlparse(url)
        # /s/XXXX or /sharing/link?surl=XXXX
        m = re.search(r"/s/([A-Za-z0-9_-]+)", parsed.path)
        if m:
            surl = m.group(1)
            # Leading "1" is often present but not part of the real code
            return surl[1:] if surl.startswith("1") and len(surl) > 15 else surl
        qs = parse_qs(parsed.query)
        if "surl" in qs and qs["surl"]:
            surl = qs["surl"][0]
            return surl[1:] if surl.startswith("1") and len(surl) > 15 else surl
    except Exception:
        pass
    return None


def _extract_dlink(value):
    """Recursively find a direct download URL."""
    if isinstance(value, dict):
        for key in (
            "dlink",
            "downloadUrl",
            "download_url",
            "original_download_url",
            "direct_link",
            "location",
        ):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                return candidate
            found = _extract_dlink(candidate)
            if found:
                return found
        for child in value.values():
            found = _extract_dlink(child)
            if found:
                return found
        return None

    if isinstance(value, list):
        for child in value:
            found = _extract_dlink(child)
            if found:
                return found
    return None


def _extract_files(value) -> list:
    """Try to pull a list of {filename, size, dlink} from nested structures."""
    files = []

    def walk(obj):
        if isinstance(obj, dict):
            # Single-file style responses
            name = (
                obj.get("server_filename")
                or obj.get("file_name")
                or obj.get("filename")
                or obj.get("name")
                or obj.get("title")
            )
            dlink = (
                obj.get("dlink")
                or obj.get("download_url")
                or obj.get("original_download_url")
                or obj.get("direct_link")
            )
            size = obj.get("size") or obj.get("formatted_size")
            if name and isinstance(dlink, str) and dlink.startswith("http"):
                files.append(
                    {
                        "filename": name,
                        "size": size,
                        "dlink": dlink,
                    }
                )
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(value)
    # dedupe by dlink
    seen = set()
    out = []
    for f in files:
        if f["dlink"] not in seen:
            seen.add(f["dlink"])
            out.append(f)
    return out


def _http_get_json(url: str, timeout: int = 25) -> dict | None:
    try:
        req = request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
            },
            method="GET",
        )
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            return json.loads(raw)
    except Exception:
        return None


def _try_public_fallbacks(share_url: str) -> dict | None:
    """Best-effort fallbacks when Browserless path fails."""
    surl = _extract_surl(share_url) or ""
    for template in PUBLIC_FALLBACKS:
        try:
            api_url = template.format(url=quote(share_url, safe=""), surl=surl)
            data = _http_get_json(api_url)
            if not data:
                continue

            # robin worker format
            if data.get("success") and data.get("files"):
                files = []
                for f in data["files"]:
                    dlink = (
                        f.get("original_download_url")
                        or f.get("download_url")
                        or f.get("dlink")
                    )
                    if dlink:
                        files.append(
                            {
                                "filename": f.get("file_name") or f.get("filename") or "file",
                                "size": f.get("size"),
                                "dlink": dlink,
                            }
                        )
                if files:
                    return {"success": True, "dlink": files[0]["dlink"], "files": files}

            # tbx-proxy format
            if data.get("data") and not data.get("error"):
                d = data["data"]
                dlink = d.get("dlink") or d.get("download_url")
                if dlink:
                    return {
                        "success": True,
                        "dlink": dlink,
                        "files": [
                            {
                                "filename": d.get("name") or d.get("title") or "file",
                                "size": d.get("size"),
                                "dlink": dlink,
                            }
                        ],
                    }
        except Exception:
            continue
    return None


async def get_terabox_dlink(share_url: str) -> dict:
    """
    Resolve a public Terabox share URL to direct CDN download link(s).

    Strategy:
    1. Browserless Puppeteer script that intercepts network + calls share APIs
       with better fallbacks (share/list, shorturlinfo, mobile endpoints).
    2. Public worker fallbacks if Browserless fails.
    """
    client = BrowserlessClient()

    # IMPORTANT: plain string template + replace — never f-string for JS braces.
    safe_url = json.dumps(share_url)
    code = r'''export default async ({ page }) => {
      const shareUrl = __SHARE_URL__;
      const captured = {
        jsToken: null,
        dpLogId: null,
        shorturlinfo: null,
        shareList: null,
        downloadResponses: [],
        resourceUrls: [],
        pageErrors: []
      };

      const rememberRequest = (url) => {
        try {
          const parsed = new URL(url);
          const token = parsed.searchParams.get("jsToken");
          const logid = parsed.searchParams.get("dp-logid");
          if (token) captured.jsToken = token;
          if (logid) captured.dpLogId = logid;
          captured.resourceUrls.push(url);
        } catch (_) {}
      };

      page.on("request", request => rememberRequest(request.url()));

      page.on("response", async (response) => {
        try {
          const url = response.url();
          rememberRequest(url);
          const ct = (response.headers()["content-type"] || "").toLowerCase();
          if (!ct.includes("json") && !ct.includes("javascript") && !ct.includes("text")) {
            return;
          }
          // Capture interesting API payloads
          if (
            url.includes("shorturlinfo") ||
            url.includes("/share/list") ||
            url.includes("/share/download") ||
            url.includes("/api/sharedownload") ||
            url.includes("filemetas")
          ) {
            try {
              const text = await response.text();
              let parsed = null;
              try { parsed = JSON.parse(text); } catch (_) { parsed = { _raw: text.slice(0, 500) }; }
              if (url.includes("shorturlinfo")) captured.shorturlinfo = parsed;
              if (url.includes("/share/list")) captured.shareList = parsed;
              if (url.includes("download") || url.includes("filemetas")) {
                captured.downloadResponses.push({ url, data: parsed });
              }
            } catch (_) {}
          }
        } catch (_) {}
      });

      // Use a realistic viewport + UA
      await page.setUserAgent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
      );
      await page.setViewport({ width: 1366, height: 768 });

      await page.goto(shareUrl, {
        waitUntil: "networkidle2",
        timeout: 45000
      }).catch(() => page.goto(shareUrl, { waitUntil: "domcontentloaded", timeout: 30000 }));

      // Give SPA time to fire API calls
      await new Promise(r => setTimeout(r, 3500));

      // Try dismiss common overlays / continue buttons if present
      try {
        const selectors = [
          "button",
          "[class*='continue']",
          "[class*='download']",
          "a[href*='download']"
        ];
        for (const sel of selectors) {
          const els = await page.$$(sel);
          for (const el of els.slice(0, 5)) {
            const text = (await page.evaluate(e => (e.innerText || e.textContent || "").toLowerCase(), el)).trim();
            if (text.includes("continue") || text.includes("download") || text.includes("unduh") || text.includes("lanjut")) {
              await el.click().catch(() => {});
              await new Promise(r => setTimeout(r, 800));
            }
          }
        }
      } catch (_) {}

      await new Promise(r => setTimeout(r, 1500));

      const html = await page.content();
      const currentUrl = page.url();

      // Extra jsToken patterns (including URL-encoded / fn() style)
      if (!captured.jsToken) {
        const tokenPatterns = [
          /[?&]jsToken=([A-Za-z0-9_-]+)/i,
          /["']jsToken["']\s*[:=]\s*["']([^"']+)["']/i,
          /jsToken\s*=\s*["']([^"']+)["']/i,
          /fn%28%22([A-Za-z0-9_-]+)%22%29/i,
          /fn\("([A-Za-z0-9_-]+)"\)/i,
          /"token"\s*:\s*"([A-Za-z0-9_-]{20,})"/i
        ];
        for (const pattern of tokenPatterns) {
          const match = html.match(pattern);
          if (match) {
            captured.jsToken = match[1];
            break;
          }
        }
      }

      if (!captured.jsToken) {
        try {
          const resources = await page.evaluate(() =>
            performance.getEntriesByType("resource").map(e => e.name)
          );
          for (const resource of resources) {
            rememberRequest(resource);
            if (captured.jsToken) break;
          }
        } catch (_) {}
      }

      const getSurl = (urlStr) => {
        try {
          const u = new URL(urlStr);
          const parts = u.pathname.split("/").filter(Boolean);
          const idx = parts.findIndex(p => p.toLowerCase() === "s");
          if (idx >= 0 && parts[idx + 1]) {
            let s = parts[idx + 1];
            if (s.startsWith("1") && s.length > 15) s = s.slice(1);
            return s;
          }
          let s = u.searchParams.get("surl");
          if (s && s.startsWith("1") && s.length > 15) s = s.slice(1);
          return s;
        } catch (_) {
          return null;
        }
      };

      let surl = getSurl(currentUrl) || getSurl(shareUrl);
      if (!surl) {
        // last-chance regex on HTML
        const m = html.match(/surl[=:]["']?([A-Za-z0-9_-]{8,})/i);
        if (m) {
          surl = m[1];
          if (surl.startsWith("1") && surl.length > 15) surl = surl.slice(1);
        }
      }

      if (!captured.dpLogId) {
        captured.dpLogId = String(Date.now()) + String(Math.floor(Math.random() * 9000 + 1000));
      }

      const origin = (() => {
        try { return new URL(currentUrl).origin; } catch (_) {
          try { return new URL(shareUrl).origin; } catch (__) { return "https://www.terabox.com"; }
        }
      })();

      const commonParams = () => {
        const p = new URLSearchParams({
          app_id: "250528",
          web: "1",
          channel: "dubox",
          clienttype: "0",
          "dp-logid": captured.dpLogId
        });
        if (captured.jsToken) p.set("jsToken", captured.jsToken);
        return p;
      };

      // --- Helper: safe JSON fetch inside the page context ---
      const pageFetchJson = async (url, options = {}) => {
        return await page.evaluate(async (u, opts) => {
          try {
            const resp = await fetch(u, {
              credentials: "include",
              headers: {
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                ...(opts.headers || {})
              },
              method: opts.method || "GET",
              body: opts.body || undefined
            });
            const text = await resp.text();
            let data = null;
            try { data = JSON.parse(text); } catch (_) {
              return { ok: false, status: resp.status, nonJson: true, preview: text.slice(0, 300) };
            }
            return { ok: resp.ok, status: resp.status, data };
          } catch (e) {
            return { ok: false, error: String(e) };
          }
        }, url, options);
      };

      let info = captured.shorturlinfo;
      let listData = captured.shareList;

      // Strategy A: /api/shorturlinfo
      if ((!info || Number(info.errno) !== 0) && captured.jsToken && surl) {
        const infoUrl = new URL("/api/shorturlinfo", origin);
        const params = commonParams();
        params.set("shorturl", surl);
        params.set("root", "1");
        params.set("scene", "");
        for (const [k, v] of params.entries()) infoUrl.searchParams.set(k, v);

        const res = await pageFetchJson(infoUrl.toString(), {
          headers: { "Referer": currentUrl || shareUrl }
        });
        if (res && res.data && !res.nonJson) {
          info = res.data;
        }
      }

      // Strategy B: /share/list (often works when shorturlinfo is blocked)
      if ((!info || Number(info?.errno) !== 0) && captured.jsToken && surl) {
        const listUrl = new URL("/share/list", origin);
        const params = commonParams();
        params.set("shorturl", surl);
        params.set("root", "1");
        params.set("page", "1");
        params.set("num", "100");
        params.set("order", "name");
        for (const [k, v] of params.entries()) listUrl.searchParams.set(k, v);

        const res = await pageFetchJson(listUrl.toString(), {
          headers: { "Referer": currentUrl || shareUrl }
        });
        if (res && res.data && !res.nonJson) {
          listData = res.data;
          // Normalize into shorturlinfo-like shape
          if (Number(listData.errno) === 0 && Array.isArray(listData.list)) {
            info = {
              errno: 0,
              list: listData.list,
              shareid: listData.share_id || listData.shareid,
              uk: listData.uk,
              sign: listData.sign,
              timestamp: listData.timestamp
            };
          }
        }
      }

      // Strategy C: try alternate origins if still failing
      if ((!info || Number(info?.errno) !== 0) && captured.jsToken && surl) {
        const altOrigins = [
          "https://www.terabox.com",
          "https://www.terabox.app",
          "https://dm.terabox.app",
          "https://www.1024tera.com",
          "https://1024terabox.com"
        ];
        for (const alt of altOrigins) {
          if (alt === origin) continue;
          try {
            const listUrl = new URL("/share/list", alt);
            const params = commonParams();
            params.set("shorturl", surl);
            params.set("root", "1");
            params.set("page", "1");
            params.set("num", "100");
            for (const [k, v] of params.entries()) listUrl.searchParams.set(k, v);
            const res = await pageFetchJson(listUrl.toString(), {
              headers: { "Referer": alt + "/" }
            });
            if (res && res.data && !res.nonJson && Number(res.data.errno) === 0) {
              listData = res.data;
              info = {
                errno: 0,
                list: listData.list || [],
                shareid: listData.share_id || listData.shareid,
                uk: listData.uk,
                sign: listData.sign,
                timestamp: listData.timestamp
              };
              break;
            }
          } catch (_) {}
        }
      }

      if (!captured.jsToken) {
        throw new Error("jsToken Terabox tidak ditemukan (halaman mungkin meminta verifikasi)");
      }
      if (!surl) {
        throw new Error("Kode share (surl) Terabox tidak ditemukan");
      }
      if (!info || Number(info.errno) !== 0) {
        const errno = info?.errno ?? "?";
        const msg = info?.show_msg || info?.errmsg || "unknown";
        // Common: 400141 need verify
        throw new Error(
          "Terabox metadata gagal (errno " + errno + "): " + msg +
          ". Share mungkin dilindungi verifikasi/password atau diblok anti-bot."
        );
      }

      const fileList = Array.isArray(info.list) ? info.list : [];
      const files = fileList.filter(item => Number(item?.isdir || 0) === 0);
      if (!files.length) {
        // Maybe only folders — take first entry anyway
        if (fileList.length) files.push(fileList[0]);
      }
      if (!files.length) {
        throw new Error("Tidak ada file di share Terabox ini");
      }

      const shareId = info.shareid ?? info.share_id;
      const uk = info.uk ?? info.share_uk;
      const sign = info.sign;
      const timestamp = info.timestamp;

      if (!shareId || !uk || !sign || !timestamp) {
        // Sometimes list endpoint doesn't return sign — try to get dlink from
        // already-captured download responses or from file.dlink if present
        const results = [];
        for (const f of files) {
          if (f.dlink) {
            results.push({
              filename: f.server_filename || f.filename || "file",
              size: f.size,
              dlink: f.dlink
            });
          }
        }
        for (const dr of captured.downloadResponses) {
          const lst = Array.isArray(dr.data?.list) ? dr.data.list : [];
          for (const item of lst) {
            if (item.dlink) {
              results.push({
                filename: item.server_filename || item.filename || "file",
                size: item.size,
                dlink: item.dlink
              });
            }
          }
          if (dr.data?.dlink) {
            results.push({
              filename: "file",
              size: null,
              dlink: dr.data.dlink
            });
          }
        }
        if (results.length) {
          return {
            data: {
              dlink: results[0].dlink,
              files: results,
              filename: results[0].filename
            },
            type: "application/json"
          };
        }
        throw new Error(
          "Metadata download tidak lengkap (shareid/uk/sign/timestamp). " +
          "Terabox mungkin mengubah API atau memerlukan login."
        );
      }

      // Request dlink for each file (or at least the first few)
      const results = [];
      const maxFiles = Math.min(files.length, 10);

      for (let i = 0; i < maxFiles; i++) {
        const file = files[i];
        const fsId = file.fs_id;
        if (!fsId) continue;

        const downloadUrl = new URL("/share/download", origin);
        const params = commonParams();
        params.set("shareid", String(shareId));
        params.set("sign", String(sign));
        params.set("timestamp", String(timestamp));
        for (const [k, v] of params.entries()) downloadUrl.searchParams.set(k, v);

        const body = new URLSearchParams({
          product: "share",
          nozip: "0",
          fid_list: JSON.stringify([Number(fsId)]),
          uk: String(uk),
          primaryid: String(shareId)
        });

        const res = await pageFetchJson(downloadUrl.toString(), {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": currentUrl || shareUrl
          },
          body: body.toString()
        });

        if (res && res.data && !res.nonJson && Number(res.data.errno) === 0) {
          const returned = Array.isArray(res.data.list) ? res.data.list : [];
          const returnedFile =
            returned.find(item => String(item.fs_id) === String(fsId)) || returned[0];
          const dlink = returnedFile?.dlink || res.data.dlink || file.dlink;
          if (dlink) {
            results.push({
              filename: file.server_filename || file.filename || returnedFile?.server_filename || "file",
              size: file.size || returnedFile?.size,
              dlink: dlink
            });
          }
        } else if (file.dlink) {
          results.push({
            filename: file.server_filename || file.filename || "file",
            size: file.size,
            dlink: file.dlink
          });
        }
      }

      if (!results.length) {
        throw new Error("Terabox API selesai tetapi dlink kosong untuk semua file");
      }

      return {
        data: {
          dlink: results[0].dlink,
          filename: results[0].filename,
          files: results
        },
        type: "application/json"
      };
    };'''

    code = code.replace("__SHARE_URL__", safe_url)

    result = client._post_function(code, timeout_ms=90000)

    if result.get("error"):
        # Browserless failed — try public fallbacks
        fb = _try_public_fallbacks(share_url)
        if fb and fb.get("success"):
            return fb
        return {
            "success": False,
            "error": f"Browserless error: {result.get('error')}",
        }

    # Success path from Browserless
    files = _extract_files(result)
    dlink = _extract_dlink(result)

    if files:
        return {
            "success": True,
            "dlink": files[0]["dlink"],
            "files": files,
            "filename": files[0].get("filename"),
        }

    if dlink:
        return {"success": True, "dlink": dlink, "files": [{"filename": "file", "dlink": dlink}]}

    # Last resort fallbacks
    fb = _try_public_fallbacks(share_url)
    if fb and fb.get("success"):
        return fb

    return {
        "success": False,
        "error": "Browserless selesai tetapi direct download link tidak ditemukan.",
        "debug": result,
    }

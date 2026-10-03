import json
import os
import re
from urllib import request, error
from urllib.parse import quote, urlparse, parse_qs

BROWSERLESS_TOKEN = os.environ.get("BROWSERLESS_TOKEN", "").strip()
BROWSERLESS_ENDPOINT = "https://production-sfo.browserless.io/function"

PUBLIC_FALLBACKS = [
    "https://terabox-worker.robinkumarshakya103.workers.dev/api?url={url}",
    "https://tbx-proxy.shakir-ansarii075.workers.dev/?mode=resolve&surl={surl}",
]


class BrowserlessClient:
    def _post_function(self, code: str, timeout_ms: int = 50000):
        if not BROWSERLESS_TOKEN:
            raise RuntimeError("BROWSERLESS_TOKEN belum dikonfigurasi")
        endpoint = (
            f"{BROWSERLESS_ENDPOINT}?token={quote(BROWSERLESS_TOKEN, safe='')}"
            f"&timeout={timeout_ms}"
        )
        req = request.Request(
            endpoint,
            data=code.encode("utf-8"),
            headers={"Content-Type": "application/javascript", "Cache-Control": "no-cache"},
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


def _extract_surl(url: str):
    try:
        parsed = urlparse(url)
        m = re.search(r"/s/([A-Za-z0-9_-]+)", parsed.path)
        if m:
            surl = m.group(1)
            return surl[1:] if surl.startswith("1") and len(surl) > 15 else surl
        qs = parse_qs(parsed.query)
        if "surl" in qs and qs["surl"]:
            surl = qs["surl"][0]
            return surl[1:] if surl.startswith("1") and len(surl) > 15 else surl
    except Exception:
        pass
    return None


def _extract_dlink(value):
    if isinstance(value, dict):
        for key in ("dlink", "downloadUrl", "download_url", "original_download_url", "direct_link", "location"):
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


def _extract_files(value):
    files = []
    def walk(obj):
        if isinstance(obj, dict):
            name = obj.get("server_filename") or obj.get("file_name") or obj.get("filename") or obj.get("name") or obj.get("title")
            dlink = obj.get("dlink") or obj.get("download_url") or obj.get("original_download_url") or obj.get("direct_link")
            size = obj.get("size") or obj.get("formatted_size")
            if name and isinstance(dlink, str) and dlink.startswith("http"):
                thumb = obj.get("thumb")
                if not isinstance(thumb, str):
                    th = obj.get("thumbs")
                    if isinstance(th, dict):
                        cand = th.get("url3") or th.get("url2") or th.get("url1")
                        thumb = cand if isinstance(cand, str) else None
                ld = obj.get("downloadDlink") or obj.get("listDlink")
                files.append({"filename": name, "size": size, "dlink": dlink,
                              "thumb": thumb if isinstance(thumb, str) else None,
                              "list_dlink": ld if isinstance(ld, str) else None})
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
    walk(value)
    seen, out = set(), []
    for f in files:
        if f["dlink"] not in seen:
            seen.add(f["dlink"])
            out.append(f)
    return out


def _http_get_json(url: str, timeout: int = 25):
    try:
        req = request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, method="GET")
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def _try_public_fallbacks(share_url: str):
    surl = _extract_surl(share_url) or ""
    for template in PUBLIC_FALLBACKS:
        try:
            api_url = template.format(url=quote(share_url, safe=""), surl=surl)
            data = _http_get_json(api_url)
            if not data:
                continue
            if data.get("success") and data.get("files"):
                files = []
                for f in data["files"]:
                    dlink = f.get("original_download_url") or f.get("download_url") or f.get("dlink")
                    if dlink:
                        files.append({"filename": f.get("file_name") or f.get("filename") or "file", "size": f.get("size"), "dlink": dlink})
                if files:
                    return {"success": True, "dlink": files[0]["dlink"], "files": files}
            if data.get("data") and not data.get("error"):
                d = data["data"]
                dlink = d.get("dlink") or d.get("download_url")
                if dlink:
                    return {"success": True, "dlink": dlink, "files": [{"filename": d.get("name") or "file", "size": d.get("size"), "dlink": dlink}]}
        except Exception:
            continue
    return None



def _clean_browserless_error(msg):
    """Rapikan pesan error Browserless (buang embel-embel stack & requestId)."""
    msg = str(msg or "")
    msg = re.sub(r"\s*\(requestId:[^)]*\)\s*$", "", msg)
    msg = re.sub(r"\s+default\s*\(https?://[^)]*\)", "", msg)
    return msg.strip() or "browserless gagal tanpa pesan"


async def get_terabox_dlink(share_url: str, fid: str = None) -> dict:
    client = BrowserlessClient()
    ndus = (os.environ.get("TERABOX_NDUS") or "").strip()
    if ndus.lower().startswith("ndus="):
        ndus = ndus.split("=", 1)[1].strip()
    safe_url = json.dumps(share_url)
    safe_ndus = json.dumps(ndus)
    code = r'''export default async ({ page }) => {
      const shareUrl = __SHARE_URL__;
      const injectedNdus = __NDUS__;
      const captured = { jsToken: null, shorturlinfo: null, shareList: null, downloadResponses: [], resourceUrls: [] };
      const pickThumb = (f) => {
        try {
          const t = f && f.thumbs;
          if (t && typeof t === "object") return t.url3 || t.url2 || t.url1 || null;
        } catch (_) {}
        return null;
      };
      const rememberRequest = (url) => {
        try {
          const parsed = new URL(url);
          const token = parsed.searchParams.get("jsToken");
          if (token) captured.jsToken = token;
        } catch (_) {}
      };
      page.on("request", r => rememberRequest(r.url()));
      page.on("response", async (response) => {
        try {
          const url = response.url();
          rememberRequest(url);
          const ct = (response.headers()["content-type"] || "").toLowerCase();
          if (!ct.includes("json") && !ct.includes("text")) return;
          if (url.includes("shorturlinfo") || url.includes("/share/list") || url.includes("download") || url.includes("filemetas") || url.includes("dlink")) {
            try {
              const text = await response.text();
              let parsed = null;
              try { parsed = JSON.parse(text); } catch (_) { parsed = { _raw: text.slice(0, 400) }; }
              if (url.includes("shorturlinfo")) captured.shorturlinfo = parsed;
              if (url.includes("/share/list")) captured.shareList = parsed;
              if (url.includes("download") || url.includes("filemetas") || url.includes("dlink"))
                captured.downloadResponses.push({ url, data: parsed });
            } catch (_) {}
          }
        } catch (_) {}
      });
      await page.setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
      await page.setViewport({ width: 1366, height: 768 });
      if (injectedNdus) {
        for (const domain of [".terabox.com", ".terabox.app", ".1024terabox.com", ".1024tera.com", "www.terabox.com", "www.terabox.app", "www.1024tera.com", "1024terabox.com"]) {
          for (const cookieName of ["NDUS", "ndus"]) {
            try { await page.setCookie({ name: cookieName, value: injectedNdus, domain, path: "/", secure: true }); } catch (_) {}
          }
        }
      }
      await page.goto(shareUrl, { waitUntil: "networkidle2", timeout: 45000 }).catch(() => page.goto(shareUrl, { waitUntil: "domcontentloaded", timeout: 30000 }));
      await new Promise(r => setTimeout(r, 3500));
      captured.ndusCookieOk = false;
      try {
        const ck1 = await page.evaluate(() => document.cookie || "");
        captured.ndusCookieOk = /(?:^|;\s*)NDUS=/.test(ck1);
        if (injectedNdus && !captured.ndusCookieOk) {
          const curOrigin = (() => { try { return new URL(page.url()).origin; } catch (_) { return null; } })();
          if (curOrigin) {
            for (const cn of ["NDUS", "ndus"]) {
              try { await page.setCookie({ name: cn, value: injectedNdus, url: curOrigin, path: "/" }); } catch (_) {}
            }
            const ck2 = await page.evaluate(() => document.cookie || "");
            captured.ndusCookieOk = /(?:^|;\s*)NDUS=/.test(ck2);
          }
        }
      } catch (_) {}
      try {
        for (const sel of ["button", "[class*='download']", "a[href*='download']"]) {
          const els = await page.$$(sel);
          for (const el of els.slice(0, 5)) {
            const text = (await page.evaluate(e => (e.innerText || "").toLowerCase(), el)).trim();
            if (text.includes("download") || text.includes("unduh") || text.includes("continue")) {
              await el.click().catch(() => {});
              await new Promise(r => setTimeout(r, 800));
            }
          }
        }
      } catch (_) {}
      await new Promise(r => setTimeout(r, 1500));
      const html = await page.content();
      const currentUrl = page.url();
      if (!captured.jsToken) {
        for (const pattern of [/[?&]jsToken=([A-Za-z0-9_-]+)/i, /["']jsToken["']\s*[:=]\s*["']([^"']+)["']/i, /fn%28%22([A-Za-z0-9_-]+)%22%29/i, /fn\("([A-Za-z0-9_-]+)"\)/i]) {
          const match = html.match(pattern);
          if (match) { captured.jsToken = match[1]; break; }
        }
      }
      if (!captured.jsToken) {
        try {
          for (const resource of await page.evaluate(() => performance.getEntriesByType("resource").map(e => e.name))) {
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
        } catch (_) { return null; }
      };
      let surl = getSurl(currentUrl) || getSurl(shareUrl);
      if (!surl) {
        const m = html.match(/surl[=:]["']?([A-Za-z0-9_-]{8,})/i);
        if (m) { surl = m[1]; if (surl.startsWith("1") && surl.length > 15) surl = surl.slice(1); }
      }
      const origin = (() => { try { return new URL(currentUrl).origin; } catch (_) { try { return new URL(shareUrl).origin; } catch (__) { return "https://www.terabox.com"; } } })();
      try {
        const ckA = await page.evaluate(() => document.cookie || "");
        captured.ndusCookieOk = /(?:^|;\s*)NDUS=/.test(ckA);
        captured.fetchOrigin = origin;
        if (injectedNdus && !captured.ndusCookieOk) {
          for (const cn of ["NDUS", "ndus"]) {
            try { await page.setCookie({ name: cn, value: injectedNdus, url: origin, path: "/" }); } catch (_) {}
          }
          const ckB = await page.evaluate(() => document.cookie || "");
          captured.ndusCookieOk = /(?:^|;\s*)NDUS=/.test(ckB);
        }
      } catch (_) {}
      const commonParams = () => {
        const p = new URLSearchParams({ app_id: "250528", web: "1", channel: "dubox", clienttype: "0" });
        if (captured.jsToken) p.set("jsToken", captured.jsToken);
        return p;
      };
      const pageFetchJson = async (url, options = {}) => page.evaluate(async (u, opts) => {
        try {
          const resp = await fetch(u, { credentials: "include", cache: opts.cache || "default", headers: { "Accept": "application/json, text/plain, */*", "X-Requested-With": "XMLHttpRequest", ...(opts.headers || {}) }, method: opts.method || "GET", body: opts.body || undefined });
          const text = await resp.text();
          let data = null;
          try { data = JSON.parse(text); } catch (_) { return { ok: false, status: resp.status, nonJson: true, preview: text.slice(0, 300) }; }
          return { ok: resp.ok, status: resp.status, data };
        } catch (e) { return { ok: false, error: String(e) }; }
      }, url, options);
      let info = captured.shorturlinfo;
      let listData = captured.shareList;
      if ((!info || Number(info.errno) !== 0) && captured.jsToken && surl) {
        const infoUrl = new URL("/api/shorturlinfo", origin);
        const params = commonParams(); params.set("shorturl", surl); params.set("root", "1"); params.set("scene", "");
        for (const [k, v] of params.entries()) infoUrl.searchParams.set(k, v);
        const res = await pageFetchJson(infoUrl.toString(), { headers: { "Referer": currentUrl || shareUrl } });
        if (res && res.data && !res.nonJson) info = res.data;
      }
      if ((!info || Number(info?.errno) !== 0) && captured.jsToken && surl) {
        const listUrl = new URL("/share/list", origin);
        const params = commonParams(); params.set("shorturl", surl); params.set("root", "1"); params.set("page", "1"); params.set("num", "100");
        for (const [k, v] of params.entries()) listUrl.searchParams.set(k, v);
        const res = await pageFetchJson(listUrl.toString(), { headers: { "Referer": currentUrl || shareUrl } });
        if (res && res.data && !res.nonJson && Number(res.data.errno) === 0 && Array.isArray(res.data.list)) {
          listData = res.data;
          info = { errno: 0, list: listData.list, shareid: listData.share_id || listData.shareid, uk: listData.uk, sign: listData.sign, timestamp: listData.timestamp, randsk: listData.randsk };
        }
      }
      if (!captured.jsToken) throw new Error("jsToken Terabox tidak ditemukan");
      if (!surl) throw new Error("Kode share (surl) tidak ditemukan");
      if (!info || Number(info.errno) !== 0) throw new Error("Terabox metadata gagal (errno " + (info?.errno ?? "?") + "): " + (info?.show_msg || info?.errmsg || "unknown"));
      const fileList = Array.isArray(info.list) ? info.list : [];
      const files = fileList.filter(item => Number(item?.isdir || 0) === 0);
      if (!files.length && fileList.length) files.push(fileList[0]);
      if (!files.length) throw new Error("Tidak ada file di share");
      // Pastikan timestamp FRESH: server Terabox kadang mengembalikan timestamp
      // basi (~1 jam). Sign terikat EXACT pada nilai timestamp tersebut, jadi
      // ulangi fetch hingga dapat yang fresh, lalu pakai nilai persis itu.
      const tsIsFresh = (ts) => {
        const n = Number(ts);
        return !!n && Math.abs(n - Math.floor(Date.now() / 1000)) < 300;
      };
      if (captured.jsToken && surl && !tsIsFresh(info && info.timestamp)) {
        for (let attempt = 0; attempt < 3 && !tsIsFresh(info && info.timestamp); attempt++) {
          try {
            const freshUrl = new URL("/api/shorturlinfo", origin);
            const fp = commonParams();
            fp.set("shorturl", surl); fp.set("root", "1"); fp.set("scene", "");
            fp.set("_t", String(Date.now()) + "_" + attempt);
            for (const [k, v] of fp.entries()) freshUrl.searchParams.set(k, v);
            const fres = await pageFetchJson(freshUrl.toString(), { headers: { "Referer": currentUrl || shareUrl }, cache: "no-store" });
            if (fres && fres.data && !fres.nonJson && Number(fres.data.errno) === 0) {
              info = fres.data;
            }
          } catch (_) {}
          if (!tsIsFresh(info && info.timestamp) && attempt < 2) {
            await new Promise((r) => setTimeout(r, 1500));
          }
        }
      }
      const shareId = info.shareid ?? info.share_id;
      const uk = info.uk ?? info.share_uk;
      const sign = info.sign;
      const timestamp = info.timestamp;
      if (!shareId || !uk || !sign || !timestamp) {
        const results = [];
        for (const f of files) if (f.dlink) results.push({ filename: f.server_filename || "file", size: f.size, dlink: f.dlink, thumb: pickThumb(f) });
        for (const dr of captured.downloadResponses) {
          const lst = Array.isArray(dr.data?.list) ? dr.data.list : [];
          for (const item of lst) if (item.dlink) results.push({ filename: item.server_filename || "file", size: item.size, dlink: item.dlink, thumb: pickThumb(item) });
          if (typeof dr.data?.dlink === "string") results.push({ filename: "file", size: null, dlink: dr.data.dlink, thumb: null });
        }
        if (results.length) return { data: { dlink: results[0].dlink, files: results, filename: results[0].filename }, type: "application/json" };
        throw new Error("Metadata download tidak lengkap (shareid/uk/sign/timestamp)");
      }
      let sekey = null;
      try {
        const cookieStr = await page.evaluate(() => document.cookie || "");
        const cookieMap = {};
        cookieStr.split(";").forEach(part => { const idx = part.indexOf("="); if (idx > 0) cookieMap[part.slice(0, idx).trim()] = part.slice(idx + 1).trim(); });
        sekey = cookieMap["randsk"] || cookieMap["BOXCLND"] || cookieMap["sekey"] || null;
        if (sekey) try { sekey = decodeURIComponent(sekey); } catch (_) {}
      } catch (_) {}
      if (!sekey && info?.randsk) { sekey = info.randsk; try { sekey = decodeURIComponent(String(sekey)); } catch (_) {} }
      if (!sekey && listData?.randsk) { sekey = listData.randsk; try { sekey = decodeURIComponent(String(sekey)); } catch (_) {} }
      const results = [];
      const debugLog = [{ step: "sekey", hasSekey: !!sekey, sekeyLen: sekey ? String(sekey).length : 0, hasNdus: !!injectedNdus, ndusCookieOk: !!captured.ndusCookieOk },
        { step: "tsinfo", infoTs: info && info.timestamp, browserISO: new Date().toISOString() }];
      const extractDlinkFromPayload = (payload, fsId) => {
        if (!payload || typeof payload !== "object") return null;
        if (typeof payload.dlink === "string" && payload.dlink.startsWith("http")) return payload.dlink;
        if (Array.isArray(payload.list)) {
          const match = payload.list.find(item => String(item.fs_id) === String(fsId)) || payload.list[0];
          if (match?.dlink?.startsWith?.("http")) return match.dlink;
        }
        if (Array.isArray(payload.dlink)) {
          const match = payload.dlink.find(item => String(item.fs_id) === String(fsId)) || payload.dlink[0];
          if (match?.dlink?.startsWith?.("http")) return match.dlink;
        }
        return null;
      };
      // PENTING: pakai EXACT info.timestamp — sign terikat pada nilai ini.
      // Mengganti dengan Date.now() membuat signature tidak valid (errno 2).
      const tryShareDownload = async (fsId) => {
        for (const ep of ["/share/download", "/api/sharedownload"]) {
          const downloadUrl = new URL(ep, origin);
          const params = commonParams();
          params.set("shareid", String(shareId)); params.set("sign", String(sign)); params.set("timestamp", String(timestamp));
          params.set("uk", String(uk)); params.set("primaryid", String(shareId));
          for (const [k, v] of params.entries()) downloadUrl.searchParams.set(k, v);
          const bases = [
            { product: "share", nozip: "0", fid_list: "[" + String(fsId) + "]", uk: String(uk), primaryid: String(shareId), type: "nolimit" },
            { product: "share", nozip: "0", fid_list: JSON.stringify([Number(fsId)]), uk: String(uk), primaryid: String(shareId), type: "nolimit" }
          ];
          const bodyVariants = [];
          for (const b of bases) {
            bodyVariants.push({ ...b });
            if (sekey) {
              bodyVariants.push({ ...b, extra: JSON.stringify({ sekey }) });
              bodyVariants.push({ ...b, sekey });
              bodyVariants.push({ ...b, extra: JSON.stringify({ sekey }), sekey });
            }
          }
          for (const bodyObj of bodyVariants) {
            const res = await pageFetchJson(downloadUrl.toString(), {
              method: "POST",
              headers: { "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "Referer": currentUrl || shareUrl, "Origin": origin },
              body: new URLSearchParams(bodyObj).toString()
            });
            if (!res?.data || res.nonJson) { debugLog.push({ ep, errno: res?.data?.errno, nonJson: !!res?.nonJson, err: res?.error }); continue; }
            const dlink = extractDlinkFromPayload(res.data, fsId);
            debugLog.push({ ep, errno: res.data.errno, hasDlink: !!dlink, hasSekey: !!(bodyObj.extra || bodyObj.sekey) });
            if (dlink && (res.data.errno === 0 || res.data.errno == null)) return dlink;
          }
        }
        return null;
      };
      for (const file of files.slice(0, 10)) {
        const fsId = file.fs_id;
        if (!fsId) continue;
        const listDlink = (typeof file.dlink === "string" && file.dlink.startsWith("http")) ? file.dlink : null;
        let dlDlink = await tryShareDownload(fsId);
        if (!dlDlink) for (const dr of captured.downloadResponses) { dlDlink = extractDlinkFromPayload(dr.data, fsId); if (dlDlink) break; }
        // Prioritas: dlink langsung dari /share/list (pola tools yang terbukti jalan);
        // /share/download hanya fallback.
        const dlink = listDlink || dlDlink;
        if (!dlink) continue;
        const entry = { filename: file.server_filename || file.filename || "file", size: file.size, dlink, thumb: pickThumb(file) };
        if (results.length === 0 && dlDlink && listDlink && dlDlink !== listDlink) entry.downloadDlink = dlDlink;
        results.push(entry);
      }
      if (!results.length) throw new Error("Terabox API selesai tetapi dlink kosong. debug=" + JSON.stringify(debugLog).slice(0, 900));
      let dlinkProbe = null;
      try {
        const probeUrl = results[0] && results[0].dlink;
        if (probeUrl) {
          try { await page.setExtraHTTPHeaders({ "Range": "bytes=0-1023" }); } catch (_) {}
          const resp = await page.goto(probeUrl, { waitUntil: "domcontentloaded", timeout: 25000 }).catch(e => null);
          try { await page.setExtraHTTPHeaders({}); } catch (_) {}
          if (resp && typeof resp.status === "function") {
            let bHead = "";
            try { bHead = (await resp.text().catch(() => "")).slice(0, 120); } catch (_) {}
            dlinkProbe = { status: resp.status(), finalUrl: (resp.url() || "").slice(0, 90), bodyHead: bHead };
          } else {
            dlinkProbe = { error: "goto_no_response" };
          }
        }
      } catch (e) { dlinkProbe = { error: String(e && e.message || e).slice(0, 100) }; }
      debugLog.push({ step: "dlink_probe", probe: dlinkProbe });
      // Probe pembanding: sesi BERSIH (incognito context) berisi HANYA cookie NDUS,
      // tanpa cookie lain (browserid/csrfToken/lang). Pola videoextractbot: dlink
      // harus diakses dengan sesi bersih + UA yang sama.
      let dlinkProbeClean = null;
      try {
        const probeUrl2 = results[0] && results[0].dlink;
        if (probeUrl2 && injectedNdus) {
          const bctx = await page.browser().createBrowserContext();
          const cp = await bctx.newPage();
          try {
            await cp.setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
            const dOrigin = new URL(probeUrl2).origin;
            await cp.setCookie({ name: "NDUS", value: injectedNdus, url: dOrigin, path: "/" });
            await cp.setExtraHTTPHeaders({ "Range": "bytes=0-1023" });
            const r2 = await cp.goto(probeUrl2, { waitUntil: "domcontentloaded", timeout: 25000 }).catch(e => null);
            if (r2 && typeof r2.status === "function") {
              let b2 = "";
              try { b2 = (await r2.text().catch(() => "")).slice(0, 120); } catch (_) {}
              dlinkProbeClean = { status: r2.status(), finalUrl: (r2.url() || "").slice(0, 90), bodyHead: b2 };
            } else {
              dlinkProbeClean = { error: "goto_no_response" };
            }
          } finally {
            try { await bctx.close(); } catch (_) {}
          }
        }
      } catch (e) { dlinkProbeClean = { error: String(e && e.message || e).slice(0, 100) }; }
      debugLog.push({ step: "dlink_probe_clean", probe: dlinkProbeClean });
      // === HLS full: /share/streaming adalah jalur RESMI klien untuk video.
      // Kumpulkan SEMUA chunks (tiap request memberi subset acak) dengan
      // sequence number yang benar dari #EXT-X-MEDIA-SEQUENCE + #EXTINF.
      let hlsInfo = null;
      try {
        const targetFid = __FID__;
        const vfile = (targetFid && files.find(f => String(f.fs_id) === String(targetFid)))
          || files.find(f => /\.(mp4|mkv|avi|mov|webm|m4v)$/i.test(String(f.server_filename || f.filename || ""))) || files[0];
        if (vfile && vfile.fs_id && uk && shareId && sign && timestamp) {
          const chunkMap = new Map();
          let hlsErr = null;
          let hlsType = null;
          let noNew = 0;
          for (const st of ["M3U8_AUTO_360", "M3U8_FLV_264_480"]) {
            noNew = 0;
            for (let att = 0; att < 10 && noNew < 3; att++) {
              const su = new URL("/share/streaming", origin);
              const sp = commonParams();
              sp.set("uk", String(uk)); sp.set("shareid", String(shareId));
              sp.set("type", st); sp.set("fid", String(vfile.fs_id));
              sp.set("sign", String(sign)); sp.set("timestamp", String(timestamp));
              sp.set("esl", "1"); sp.set("isplayer", "1"); sp.set("ehps", "1");
              for (const [k, v] of sp.entries()) su.searchParams.set(k, v);
              const sr = await page.evaluate(async (u, refUrl) => {
                try {
                  const resp = await fetch(u, { credentials: "include", headers: { "Accept": "*/*", "Referer": refUrl, "X-Requested-With": "XMLHttpRequest" } });
                  const txt = await resp.text();
                  return { status: resp.status, text: txt.slice(0, 1000000) };
                } catch (e) { return { error: String(e).slice(0, 100) }; }
              }, su.toString(), currentUrl || shareUrl);
              let added = 0;
              if (sr && sr.text && sr.text.includes("#EXTM3U")) {
                hlsType = st;
                const mseqM = sr.text.match(/#EXT-X-MEDIA-SEQUENCE:(\d+)/);
                const baseSeq = mseqM ? parseInt(mseqM[1], 10) : 0;
                let dur = 10;
                let idx = 0;
                for (const line of sr.text.split("\n")) {
                  const t = line.trim();
                  if (t.startsWith("#EXTINF:")) {
                    const dm = t.match(/#EXTINF:([\d.]+)/);
                    if (dm) dur = parseFloat(dm[1]);
                  } else if (t && !t.startsWith("#") && t.length > 10) {
                    const seq = baseSeq + idx;
                    const full = t.startsWith("http") ? t : (new URL(t, su.origin).toString());
                    if (!chunkMap.has(seq)) { chunkMap.set(seq, { dur, url: full }); added++; }
                    idx++;
                  }
                }
              } else if (sr && sr.text) {
                try { const j = JSON.parse(sr.text); hlsErr = "errno " + j.errno; } catch (_) { hlsErr = "non-m3u8 status " + sr.status; }
              } else if (sr && sr.error) {
                hlsErr = sr.error;
              }
              noNew = (added === 0) ? noNew + 1 : 0;
              await new Promise(r => setTimeout(r, 250));
            }
            if (chunkMap.size > 0) break;
          }
          const sorted = [...chunkMap.entries()].sort((a, b) => a[0] - b[0]).map(([seq, v]) => ({ seq, dur: v.dur, url: v.url }));
          hlsInfo = { type: hlsType, total: sorted.length, chunks: sorted, error: hlsErr, fid: String(vfile.fs_id), filename: vfile.server_filename || vfile.filename || "file" };
        }
      } catch (e) { hlsInfo = { error: String(e && e.message || e).slice(0, 100) }; }
      debugLog.push({ step: "hls", total: hlsInfo && hlsInfo.total, error: hlsInfo && hlsInfo.error });
      return { data: { dlink: results[0].dlink, filename: results[0].filename, files: results, ndusCookieOk: !!captured.ndusCookieOk, dlinkProbe, dlinkProbeClean, hls: hlsInfo }, type: "application/json" };
    };'''
    safe_fid = json.dumps(str(fid) if fid else "")
    code = code.replace("__SHARE_URL__", safe_url).replace("__NDUS__", safe_ndus).replace("__FID__", safe_fid)
    try:
        result = client._post_function(code, timeout_ms=120000)
    except Exception as exc:
        fb = _try_public_fallbacks(share_url)
        if fb and fb.get("success"):
            return fb
        return {"success": False, "error": f"Browserless error: {_clean_browserless_error(exc)}"}
    if result.get("error"):
        fb = _try_public_fallbacks(share_url)
        if fb and fb.get("success"):
            return fb
        return {"success": False, "error": f"Browserless error: {_clean_browserless_error(result.get('error'))}"}
    files = _extract_files(result)
    dlink = _extract_dlink(result)
    if files:
        rdata = result.get("data", {}) if isinstance(result, dict) else {}
        return {"success": True, "dlink": files[0]["dlink"], "files": files, "filename": files[0].get("filename"), "ndus_cookie_ok": rdata.get("ndusCookieOk"), "dlink_probe": rdata.get("dlinkProbe"), "dlink_probe_clean": rdata.get("dlinkProbeClean"), "hls": rdata.get("hls")}
    if dlink:
        return {"success": True, "dlink": dlink, "files": [{"filename": "file", "dlink": dlink}]}
    fb = _try_public_fallbacks(share_url)
    if fb and fb.get("success"):
        return fb
    return {"success": False, "error": "Browserless selesai tetapi direct download link tidak ditemukan.", "debug": result}

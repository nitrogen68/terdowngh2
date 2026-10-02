import json
import os
import re
from urllib import request, error
from urllib.parse import quote

BROWSERLESS_TOKEN = os.environ.get("BROWSERLESS_TOKEN")
BROWSERLESS_ENDPOINT = "https://production-sfo.browserless.io/function"


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
            with request.urlopen(req, timeout=(timeout_ms / 1000) + 15) as resp:
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


def _extract_dlink(value):
    """Extract a URL specifically from dlink/download fields."""
    if isinstance(value, dict):
        for key in ("dlink", "downloadUrl", "download_url", "location"):
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


async def get_terabox_dlink(share_url: str) -> dict:
    """Resolve a public Terabox share URL using Browserless + Terabox share APIs.

    The browser is used to obtain the current jsToken and session context. The
    actual file metadata and download URL are then requested through Terabox's
    own share endpoints from inside the same browser session. This is much more
    reliable than waiting for a DOM Download button or guessing CDN hostnames.
    """
    client = BrowserlessClient()
    safe_url = json.dumps(share_url)

    code = f'''export default async ({{ page }}) => {{
      const shareUrl = {safe_url};
      let jsToken = null;
      let dpLogId = null;

      const rememberRequest = (url) => {{
        try {{
          const parsed = new URL(url);
          const token = parsed.searchParams.get("jsToken");
          const logid = parsed.searchParams.get("dp-logid");
          if (token) jsToken = token;
          if (logid) dpLogId = logid;
        }} catch (_) {{}}
      }};

      page.on("request", request => rememberRequest(request.url()));
      page.on("response", response => rememberRequest(response.url()));

      await page.goto(shareUrl, {{
        waitUntil: "domcontentloaded",
        timeout: 30000
      }});

      // Allow the share application to initialise and issue its normal API
      // requests. We capture jsToken/dp-logid from those requests.
      await new Promise(resolve => setTimeout(resolve, 1800));

      const html = await page.content();
      const tokenPatterns = [
        /[?&]jsToken=([A-Za-z0-9_-]+)/i,
        /["']jsToken["']\\s*[:=]\\s*["']([^"']+)["']/i,
        /jsToken\\s*=\\s*["']([^"']+)["']/i
      ];

      if (!jsToken) {{
        for (const pattern of tokenPatterns) {{
          const match = html.match(pattern);
          if (match) {{ jsToken = match[1]; break; }}
        }}
      }}

      // Resource URLs are another reliable source when the token is not
      // embedded directly in the HTML.
      if (!jsToken) {{
        const resources = await page.evaluate(() =>
          performance.getEntriesByType("resource").map(entry => entry.name)
        );
        for (const resource of resources) {{
          rememberRequest(resource);
          if (jsToken) break;
        }}
      }}

      const current = new URL(page.url());
      const pathParts = current.pathname.split("/").filter(Boolean);
      let surl = null;
      const sIndex = pathParts.findIndex(part => part.toLowerCase() === "s");
      if (sIndex >= 0 && pathParts[sIndex + 1]) surl = pathParts[sIndex + 1];
      if (!surl) surl = current.searchParams.get("surl");
      if (!surl) {{
        const original = new URL(shareUrl);
        const originalParts = original.pathname.split("/").filter(Boolean);
        const originalIndex = originalParts.findIndex(part => part.toLowerCase() === "s");
        if (originalIndex >= 0 && originalParts[originalIndex + 1]) surl = originalParts[originalIndex + 1];
        if (!surl) surl = original.searchParams.get("surl");
      }}

      if (!jsToken) throw new Error("jsToken Terabox tidak ditemukan");
      if (!surl) throw new Error("Kode share Terabox tidak ditemukan");

      if (!dpLogId) dpLogId = String(Date.now()) + String(Math.floor(Math.random() * 9000 + 1000));

      const common = new URLSearchParams({
        app_id: "250528",
        web: "1",
        channel: "dubox",
        clienttype: "0",
        jsToken,
        "dp-logid": dpLogId
      });

      // Step 1: obtain share metadata. This response contains shareid, uk,
      // sign, timestamp and the file list including fs_id.
      const infoUrl = new URL("/api/shorturlinfo", location.origin);
      for (const [key, value] of common) infoUrl.searchParams.set(key, value);
      infoUrl.searchParams.set("shorturl", surl);
      infoUrl.searchParams.set("root", "1");
      infoUrl.searchParams.set("scene", "");

      const infoResponse = await fetch(infoUrl.toString(), {{
        credentials: "include",
        headers: {{ "Accept": "application/json, text/plain, */*" }}
      }});
      const infoText = await infoResponse.text();
      let info;
      try {{ info = JSON.parse(infoText); }} catch (_) {{
        throw new Error("Terabox shorturlinfo bukan JSON");
      }}

      if (!infoResponse.ok || Number(info.errno) !== 0) {{
        throw new Error(`Terabox metadata gagal (HTTP ${{infoResponse.status}}, errno ${{info.errno ?? "?"}}): ${{info.show_msg || "unknown"}}`);
      }}

      const list = Array.isArray(info.list) ? info.list : [];
      const file = list.find(item => Number(item?.isdir || 0) === 0) || list[0];
      if (!file || !file.fs_id) throw new Error("File pada share Terabox tidak ditemukan");

      const shareId = info.shareid ?? info.share_id ?? file.shareid;
      const uk = info.uk ?? info.share_uk ?? file.uk;
      const sign = info.sign;
      const timestamp = info.timestamp;

      if (!shareId || !uk || !sign || !timestamp) {{
        throw new Error("Metadata download Terabox tidak lengkap (shareid/uk/sign/timestamp)");
      }}

      // Step 2: ask Terabox for the actual dlink using the metadata above.
      const downloadUrl = new URL("/share/download", location.origin);
      for (const [key, value] of common) downloadUrl.searchParams.set(key, value);
      downloadUrl.searchParams.set("shareid", String(shareId));
      downloadUrl.searchParams.set("sign", String(sign));
      downloadUrl.searchParams.set("timestamp", String(timestamp));

      const body = new URLSearchParams({
        product: "share",
        nozip: "0",
        fid_list: JSON.stringify([Number(file.fs_id)]),
        uk: String(uk),
        primaryid: String(shareId)
      });

      const downloadResponse = await fetch(downloadUrl.toString(), {{
        method: "POST",
        credentials: "include",
        headers: {{
          "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
          "Accept": "application/json, text/plain, */*"
        }},
        body: body.toString()
      }});

      const downloadText = await downloadResponse.text();
      let downloadData;
      try {{ downloadData = JSON.parse(downloadText); }} catch (_) {{
        throw new Error("Terabox share/download bukan JSON");
      }}

      if (!downloadResponse.ok || Number(downloadData.errno) !== 0) {{
        throw new Error(`Terabox download API gagal (HTTP ${{downloadResponse.status}}, errno ${{downloadData.errno ?? "?"}}): ${{downloadData.show_msg || "unknown"}}`);
      }}

      const returned = Array.isArray(downloadData.list) ? downloadData.list : [];
      const returnedFile = returned.find(item => String(item.fs_id) === String(file.fs_id)) || returned[0];
      const dlink = returnedFile?.dlink || downloadData.dlink || file.dlink;

      if (!dlink) throw new Error("Terabox API selesai tetapi dlink kosong");

      return {{
        data: {{
          dlink,
          filename: file.server_filename || file.filename || "",
          fs_id: String(file.fs_id),
          shareid: String(shareId),
          uk: String(uk)
        }},
        type: "application/json"
      }};
    }};'''

    result = client._post_function(code, timeout_ms=90000)
    if result.get("error"):
        return {
            "success": False,
            "error": f"Browserless error: {result.get('error')}",
        }

    dlink = _extract_dlink(result)
    if dlink:
        return {"success": True, "dlink": dlink}

    return {
        "success": False,
        "error": "Browserless selesai tetapi direct download link tidak ditemukan.",
        "debug": result,
    }

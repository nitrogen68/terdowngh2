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
        body = code.encode("utf-8")
        req = request.Request(
            endpoint,
            data=body,
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
    """Find a direct Terabox CDN URL in any Browserless response structure."""
    if isinstance(value, dict):
        for key in ("dlink", "downloadUrl", "download_url", "url", "location"):
            candidate = value.get(key)
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

    if isinstance(value, str):
        patterns = (
            r"https?://[^\s\"'<>]+teraboxcdn\.com/[^\s\"'<>]+",
            r"https?://d\.[^\s\"'<>]+terabox[^/\s\"'<>]*/[^\s\"'<>]+",
        )
        for pattern in patterns:
            match = re.search(pattern, value)
            if match:
                return match.group(0).rstrip(").,;'\"")

    return None


async def get_terabox_dlink(share_url: str) -> dict:
    """Resolve the first file's direct download URL using Browserless Function API."""
    client = BrowserlessClient()
    safe_url = json.dumps(share_url)

    # Browserless runs this Puppeteer function inside its managed browser.
    # We monitor download-related network responses while interacting with the
    # Terabox share page, avoiding the slower Browser Use agent/polling model.
    code = f'''export default async ({{ page }}) => {{
      const shareUrl = {safe_url};
      const found = new Set();
      let dlink = null;

      const clean = (value) => {{
        if (!value || typeof value !== "string") return null;
        const match = value.match(/https?:\\/\\/[^\\s"'<>]+(?:teraboxcdn\\.com|d\\.[^\\s"'<>]*terabox)[^\\s"'<>]*/i);
        return match ? match[0].replace(/[),.;'"\\]]+$/, "") : null;
      }};

      const inspectValue = (value) => {{
        if (!value) return;
        if (typeof value === "string") {{
          const direct = clean(value);
          if (direct) dlink = direct;
          try {{
            const parsed = JSON.parse(value);
            inspectValue(parsed);
          }} catch (_) {{}}
          return;
        }}
        if (Array.isArray(value)) {{
          for (const item of value) inspectValue(item);
          return;
        }}
        if (typeof value === "object") {{
          for (const [key, item] of Object.entries(value)) {{
            if (/dlink|download.?url|location/i.test(key)) inspectValue(item);
            else if (typeof item === "string" && /teraboxcdn|d\\.terabox/i.test(item)) inspectValue(item);
            else if (item && typeof item === "object") inspectValue(item);
          }}
        }}
      }};

      page.on("response", async (response) => {{
        try {{
          const url = response.url();
          if (/teraboxcdn\\.com/i.test(url)) {{
            dlink = clean(url) || url;
            return;
          }}
          if (/\\/(share\\/download|api\\/download|share\\/download\\?)/i.test(url)) {{
            const contentType = response.headers()["content-type"] || "";
            if (contentType.includes("json")) {{
              try {{ inspectValue(await response.json()); }} catch (_) {{}}
            }}
          }}
        }} catch (_) {{}}
      }});

      await page.goto(shareUrl, {{ waitUntil: "domcontentloaded", timeout: 30000 }});

      // Give Terabox's client-side file list a short time to render.
      await new Promise(resolve => setTimeout(resolve, 2500));

      if (!dlink) {{
        // Click the first visible download-like control. This covers the
        // current Terabox share UI without requiring an fs_id from the user.
        const clicked = await page.evaluate(() => {{
          const nodes = Array.from(document.querySelectorAll("button, a, [role=button], div"));
          const patterns = /^(download|unduh)|download|unduh/i;
          const candidate = nodes.find(el => {{
            const text = (el.innerText || el.getAttribute("aria-label") || el.title || "").trim();
            const rect = el.getBoundingClientRect();
            return text && patterns.test(text) && rect.width > 0 && rect.height > 0;
          }});
          if (!candidate) return false;
          candidate.click();
          return true;
        }});

        if (clicked) {{
          await new Promise(resolve => setTimeout(resolve, 4500));
        }}
      }}

      // Some Terabox builds expose a download link only after selecting the
      // file. Try the first file row if no download button was found yet.
      if (!dlink) {{
        await page.evaluate(() => {{
          const rows = Array.from(document.querySelectorAll("[role=button], li, tr, .file-item, [class*=file]"));
          const row = rows.find(el => {{
            const rect = el.getBoundingClientRect();
            const text = (el.innerText || "").trim();
            return text && rect.width > 0 && rect.height > 0 && !/download|unduh/i.test(text);
          }});
          if (row) row.click();
        }});
        await new Promise(resolve => setTimeout(resolve, 1500));

        await page.evaluate(() => {{
          const nodes = Array.from(document.querySelectorAll("button, a, [role=button]"));
          const candidate = nodes.find(el => /download|unduh/i.test((el.innerText || el.getAttribute("aria-label") || "").trim()));
          if (candidate) candidate.click();
        }});
        await new Promise(resolve => setTimeout(resolve, 3500));
      }}

      return {{
        data: {{
          dlink,
          url: page.url(),
          title: await page.title()
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

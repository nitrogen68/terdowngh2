import json
import time
from urllib import request, error
import os
import re

API_KEY = os.environ.get("BROWSER_USE_API_KEY")
BASE_URL = "https://api.browser-use.com"


class BrowserUseClient:
    def _headers(self, json_body=False):
        if not API_KEY:
            raise RuntimeError("BROWSER_USE_API_KEY belum dikonfigurasi")
        headers = {"X-Browser-Use-API-Key": API_KEY}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _post(self, path, data_dict):
        body = json.dumps(data_dict).encode("utf-8")
        req = request.Request(
            f"{BASE_URL}{path}",
            data=body,
            headers=self._headers(json_body=True),
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8", "ignore"))
            except Exception:
                return {"error": f"HTTP {exc.code}: {exc.reason}"}
        except Exception as exc:
            return {"error": str(exc)}

    def _get(self, path):
        req = request.Request(
            f"{BASE_URL}{path}",
            headers=self._headers(),
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8", "ignore"))
            except Exception:
                return {"error": f"HTTP {exc.code}: {exc.reason}"}
        except Exception as exc:
            return {"error": str(exc)}

    def create_run(self, task: str):
        # Browser Use v4 schema: send only the required task field.
        return self._post("/api/v4/runs", {"task": task})

    def get_run(self, run_id: str):
        return self._get(f"/api/v4/runs/{run_id}")


def extract_dlink_from_run(run_data: dict) -> str | None:
    candidates = []
    output = run_data.get("output")
    if isinstance(output, str):
        candidates.append(output)

    steps = run_data.get("steps") or []
    for step in steps:
        if not isinstance(step, dict):
            continue
        for key in ("output", "result", "tool_result", "content"):
            value = step.get(key)
            if isinstance(value, str):
                candidates.append(value)

    patterns = (
        r"https?://[^\s\"'<>]+teraboxcdn\.com/[^\s\"'<>]+",
        r"https?://d\.[^\s\"'<>]+terabox[^/\s\"'<>]*/[^\s\"'<>]+",
    )
    for text in candidates:
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0).rstrip(").,;'\"")

    return None


async def get_terabox_dlink(share_url: str) -> dict:
    client = BrowserUseClient()

    task = (
        f"Go to {share_url}. "
        "Wait for the Terabox share page to fully load. "
        "Identify the first file available on the share page. "
        "Trigger its download action and capture the direct CDN download URL (dlink) "
        "from the network/API response. "
        "Return ONLY the direct download URL, with no explanation."
    )

    run_res = client.create_run(task)
    run_id = run_res.get("id") or run_res.get("run_id")

    if not run_id:
        return {
            "success": False,
            "error": f"Failed to create browser-use run: {run_res}",
        }

    timeout = 180
    start = time.time()
    while time.time() - start < timeout:
        run = client.get_run(run_id)
        status = run.get("status") or run.get("state")

        if status in ("finished", "completed", "success", "done"):
            dlink = extract_dlink_from_run(run)
            if dlink:
                return {"success": True, "dlink": dlink}
            return {
                "success": False,
                "error": "Browser Use selesai tetapi dlink tidak ditemukan pada output.",
            }

        if status in ("failed", "error", "terminated"):
            return {
                "success": False,
                "error": f"Browser Use run gagal: {status}",
            }

        time.sleep(2)

    return {
        "success": False,
        "error": "Timeout menunggu Browser Use mengambil dlink.",
    }

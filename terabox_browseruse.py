import json
import time
from urllib import request, error
from urllib.parse import urlencode
import os

API_KEY = os.environ.get("BROWSER_USE_API_KEY", "bu_gpCxkLYIQq6MNufjqJsTOAUTRvrzzlEBe8BKzXfZNvE")
BASE_URL = "https://api.browser-use.com"


class BrowserUseClient:
    def _headers(self, json_body=False):
        h = {
            "X-Browser-Use-API-Key": API_KEY,
        }
        if json_body:
            h["Content-Type"] = "application/json"
        return h

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
        except error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8", "ignore"))
            except Exception:
                return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    def _get(self, path):
        req = request.Request(
            f"{BASE_URL}{path}",
            headers=self._headers(),
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8", "ignore"))
            except Exception:
                return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    def create_run(self, task: str):
        payload = {
            "task": task,
            "allowed_domains": [
                "terabox.com",
                "www.terabox.com",
                "1024terabox.com",
                "www.1024tera.com",
                "terabox.app",
                "www.terabox.app",
                "teraboxcdn.com",
                "d.terabox.com",
                "d.terabox.app",
            ],
            "headless": True,
            "use_agent_state": False,
            "max_steps": 40,
        }
        return self._post("/api/v4/runs", payload)

    def get_run(self, run_id: str):
        return self._get(f"/api/v4/runs/{run_id}")


def extract_dlink_from_run(run_data: dict) -> str | None:
    output = run_data.get("output")
    if output:
        import re

        m = re.search(r'(https?://[^\s]+teraboxcdn\.com/[^\s]+)', output)
        if m:
            return m.group(1).rstrip(").,;'\"")
        m = re.search(r'(https?://d\.[^\s]+terabox[^/s]+/[^\s]+)', output)
        if m:
            return m.group(1).rstrip(").,;'\"")
        m = re.search(r'"(https?://[^"]+teraboxcdn[^"]+)"', output)
        if m:
            return m.group(1)
        m = re.search(r'"(https?://d\.[^"]+terabox[^"]+)"', output)
        if m:
            return m.group(1)

    steps = run_data.get("steps") or []
    for step in steps:
        for key in ("output", "result", "tool_result", "content"):
            val = step.get(key)
            if isinstance(val, str):
                import re

                m = re.search(r'(https?://[^\s]+teraboxcdn\.com/[^\s]+)', val)
                if m:
                    return m.group(1).rstrip(").,;'\"")
                m = re.search(r'(https?://d\.[^\s]+terabox[^/s]+/[^\s]+)', val)
                if m:
                    return m.group(1).rstrip(").,;'\"")

    return None


async def get_terabox_dlink(share_url: str, fs_id: str | None = None) -> dict:
    client = BrowserUseClient()

    task_parts = [
        f"Go to {share_url}",
        "Wait for the share page to fully load and render all files.",
        "Extract all file information including fs_id, filename and size.",
    ]

    if fs_id:
        task_parts.append(
            f"Get the direct download link (dlink) for fs_id {fs_id} by triggering the download/API call that returns /share/download response containing dlink."
        )
    else:
        task_parts.append(
            "Get the direct download link (dlink) for the first file from the /share/download API/network response."
        )

    task_parts.append("Return ONLY the dlink URL (the direct CDN download link). No explanation, just the URL.")

    task = ". ".join(task_parts)

    run_res = client.create_run(task)
    run_id = run_res.get("id") or run_res.get("run_id")

    if not run_id:
        return {"success": False, "error": f"Failed to create browser-use run: {run_res}"}

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
                "error": "Run finished but no dlink found in output. Try specifying fs_id.",
                "debug": run.get("output") or run.get("status"),
            }
        if status in ("failed", "error", "terminated"):
            return {
                "success": False,
                "error": f"Browser-use run failed: {status}",
                "debug": run,
            }
        time.sleep(2)

    return {"success": False, "error": "Timeout waiting for browser-use to extract dlink"}

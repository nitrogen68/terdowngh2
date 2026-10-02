import json
import time
from urllib import request, error
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
            with request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8", "ignore"))
            except Exception:
                return {"error": str(e.read()) if hasattr(e, 'read') else str(e)}
        except Exception as e:
            return {"error": str(e)}

    def _get(self, path):
        req = request.Request(
            f"{BASE_URL}{path}",
            headers=self._headers(),
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=300) as resp:
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
        }
        return self._post("/api/v4/runs", payload)

    def get_run(self, run_id: str):
        return self._get(f"/api/v4/runs/{run_id}")


def extract_dlink_from_run(run_data: dict) -> str | None:
    # Check all string fields
    for key in ("output", "result", "final_result", "response"):
        val = run_data.get(key)
        if isinstance(val, str):
            import re
            # Find dlink
            m = re.search(r'(https?://[^\s]+teraboxcdn\.com/[^\s]+)', val)
            if m: return m.group(1).rstrip(").,;'\"")
            m = re.search(r'(https?://d\.[^\s]+terabox[^/\s]+/[^\s]+)', val)
            if m: return m.group(1).rstrip(").,;'\"")
            m = re.search(r'(https?://[^"\s]+teraboxcdn[^"\s]+)', val)
            if m: return m.group(1)

    # Steps
    steps = run_data.get("steps") or []
    for step in steps:
        if isinstance(step, dict):
            for key in ("output", "result", "tool_result", "content", "thought", "tool_output"):
                val = step.get(key)
                if isinstance(val, str):
                    import re
                    m = re.search(r'(https?://[^\s]+teraboxcdn\.com/[^\s]+)', val)
                    if m: return m.group(1).rstrip(").,;'\"")
                    m = re.search(r'(https?://d\.[^\s]+terabox[^/\s]+/[^\s]+)', val)
                    if m: return m.group(1).rstrip(").,;'\"")
                elif isinstance(val, dict):
                    # recurse
                    d = extract_dlink_from_run(val)
                    if d: return d

    return None


async def get_terabox_dlink(share_url: str, fs_id: str | None = None) -> dict:
    client = BrowserUseClient()

    task = f"""Extract direct download link (dlink/CDN) from Terabox share link {share_url}.
Do NOT call any shorturlinfo endpoints that return non-JSON. 
Navigate to the share page, wait for files to load, intercept or extract the /share/download network response that contains dlink.
Return ONLY the full dlink URL starting with http(s). No extra text."""

    run_res = client.create_run(task)
    run_id = run_res.get("id") or run_res.get("run_id") or run_res.get("runId")

    if not run_id:
        return {"success": False, "error": f"Failed to create browser-use run: {run_res}"}

    timeout = 300
    start = time.time()
    while time.time() - start < timeout:
        run = client.get_run(run_id)
        status = run.get("status") or run.get("state") or run.get("run_status")
        if status in ("finished", "completed", "success", "done", "succeeded", "stopped"):
            dlink = extract_dlink_from_run(run)
            if dlink:
                return {"success": True, "dlink": dlink}
            return {
                "success": False,
                "error": "No dlink found in run result",
            }
        if status in ("failed", "error", "terminated", "canceled", "crashed"):
            return {
                "success": False,
                "error": f"Run failed: {status}",
            }
        time.sleep(3)

    return {"success": False, "error": "Timeout waiting for dlink"}

"""
Reproduz o fluxo CORRETO da UI do iManager para puxar resultados de trace.
Descoberto via spy: a UI faz pre-check + query/result (GET), não query/sort (POST).
"""

import json
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings()

ROOT = Path(__file__).parent.parent
with open(ROOT / "data" / "session.json", "r", encoding="utf-8") as f:
    sess_data = json.load(f)
with open(ROOT / "sample_event.json", "r", encoding="utf-8") as f:
    event = json.load(f)

base_url = event["oss"]["base_url"]
task_id = (event["integration"].get("trace_task_ids") or [1925])[0]


def build_session():
    s = requests.Session()
    s.verify = False
    for c in sess_data["trace"]["cookies"]:
        s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    roarand = sess_data["trace"].get("roarand")
    if roarand:
        s.headers.update({"roarand": roarand})
    s.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{base_url}/omc/farswebsite/index.html",
        "Origin": base_url,
    })
    return s


def show(label, resp):
    print("=" * 70)
    print(f">> {label}")
    print(f"   status: {resp.status_code}  ct: {resp.headers.get('Content-Type','')}")
    body = resp.text or ""
    try:
        parsed = resp.json()
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
        print(f"   body:   {pretty[:2500]}")
    except Exception:
        print(f"   body:   {body[:1500]}")
    print()


s = build_session()
now = lambda: int(time.time() * 1000)

# 1) pre-check (inicializa sessão de query no FARS)
url = f"{base_url}/rest/oss/access/fars/v1/traceresult/pre-check"
resp = s.get(url, params={"taskId": task_id, "queryType": 0, "nocache": now()},
             headers={"showLoading": "true"}, timeout=30)
show("1) GET pre-check", resp)

# 2) task-status
url = f"{base_url}/rest/oss/access/fars/v1/tracetask/query/task-status"
resp = s.post(url, params={"nocache": now()}, json={"taskId": task_id}, timeout=30)
show("2) POST tracetask/query/task-status", resp)

# 3) get-support-search-time-range
url = f"{base_url}/rest/oss/access/fars/v1/collectiontask/query/get-support-search-time-range"
resp = s.get(url, params={"taskId": task_id, "nocache": now()}, timeout=30)
show("3) GET get-support-search-time-range", resp)

# 4) all-tasks
url = f"{base_url}/rest/oss/access/fars/v1/tracetask/query/all-tasks"
resp = s.post(url, params={"nocache": now()},
              json={"isCache": True, "pageVo": {"pageSize": 100, "currentPage": 1}},
              headers={"x-non-renewal-session": "true"}, timeout=30)
show("4) POST all-tasks", resp)

# 5) query/result — ESTE É O QUE PUXA OS DADOS
url = f"{base_url}/rest/oss/access/fars/v1/traceresult/query/result"
resp = s.get(url, params={
    "nocache": now(),
    "startRow": 0,
    "pageSize": 1000,
    "taskId": task_id,
    "msgId": 1,
    "isSetBenchMarkTime": "false",
    "benchMarkTimeRowNo": -1,
}, timeout=30)
show("5) GET query/result   <<--- ESTE É O QUE INTERESSA", resp)

"""
Diagnóstico do estado da task de trace no iManager FARS.

Tenta vários endpoints do módulo FARS para descobrir:
  - Se a task 1925 existe na lista
  - Qual seu estado (RUNNING, STOPPED, IDLE, etc.)
  - Se aceita ser iniciada via endpoint de start

Os endpoints abaixo são as variações típicas das APIs FARS do iManager 20.x:
  /rest/oss/access/fars/v1/task/list
  /rest/oss/access/fars/v1/task/query
  /rest/oss/access/fars/v1/task/state
  /rest/oss/access/fars/v1/task/start
"""

import json
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings()

ROOT = Path(__file__).parent.parent
SESSION_PATH = ROOT / "data" / "session.json"
EVENT_PATH = ROOT / "sample_event.json"

with open(SESSION_PATH, "r", encoding="utf-8") as f:
    sess_data = json.load(f)
with open(EVENT_PATH, "r", encoding="utf-8") as f:
    event = json.load(f)

base_url = event["oss"]["base_url"]
task_id = (event["integration"].get("trace_task_ids") or [1925])[0]


def build_session():
    s = requests.Session()
    s.verify = False
    for cookie in sess_data["trace"]["cookies"]:
        s.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""))
    roarand = sess_data["trace"].get("roarand")
    if roarand:
        s.headers.update({"roarand": roarand})
    s.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{base_url}/ossfacewebsite/index.html",
        "Origin": base_url,
    })
    return s


def try_endpoint(method: str, path: str, *, payload=None, params=None, label=""):
    s = build_session()
    now_ms = int(time.time() * 1000)
    url = f"{base_url}{path}"
    if params is None:
        params = {}
    params.setdefault("nocache", now_ms)

    print("=" * 70)
    print(f">> {label or path}  ({method})")
    try:
        if method == "GET":
            resp = s.get(url, params=params, timeout=30)
        else:
            resp = s.post(url, params=params, json=payload, timeout=30)
    except Exception as e:
        print(f"   EXCEPTION: {e}")
        return None

    print(f"   status: {resp.status_code}  ct: {resp.headers.get('Content-Type','')}")
    body = resp.text or ""
    try:
        parsed = resp.json()
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
        print(f"   body:   {pretty[:2500]}")
    except Exception:
        print(f"   body:   {body[:1500]}")
    print()
    return resp


print(f"task_id alvo: {task_id}")
print(f"base_url:     {base_url}\n")

# 1) Listar tasks (GET)
try_endpoint("GET",  "/rest/oss/access/fars/v1/task/list", label="GET task/list")

# 2) Listar tasks (POST sem filtro)
try_endpoint("POST", "/rest/oss/access/fars/v1/task/list",
             payload={"startRow": 0, "pageSize": 100},
             label="POST task/list (paged)")

# 3) Consulta direta da task
try_endpoint("GET",  f"/rest/oss/access/fars/v1/task/{task_id}",
             label=f"GET task/{task_id}")

try_endpoint("POST", "/rest/oss/access/fars/v1/task/query",
             payload={"taskId": task_id},
             label="POST task/query")

# 4) Estado da task
try_endpoint("GET",  f"/rest/oss/access/fars/v1/task/state/{task_id}",
             label=f"GET task/state/{task_id}")

try_endpoint("POST", "/rest/oss/access/fars/v1/task/state",
             payload={"taskId": task_id},
             label="POST task/state")

# 5) Variante "tasks" (plural) que algumas builds usam
try_endpoint("GET", "/rest/oss/access/fars/v1/tasks", label="GET tasks (plural)")
try_endpoint("POST", "/rest/oss/access/fars/v1/tasks/list",
             payload={"startRow": 0, "pageSize": 100},
             label="POST tasks/list")

# 6) Buffer/contagem de resultados — se 0 explicaria o 500 no /query/sort
try_endpoint("POST", "/rest/oss/access/fars/v1/traceresult/count",
             payload={"taskId": task_id},
             label="POST traceresult/count")

# 7) Tentar fazer um simples "totalRow"
try_endpoint("POST", "/rest/oss/access/fars/v1/traceresult/totalrow",
             payload={"taskId": task_id, "msgId": -1},
             label="POST traceresult/totalrow")

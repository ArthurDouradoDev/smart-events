"""
Diagnóstico do erro 500 em /rest/oss/access/fars/v1/traceresult/query/sort.

Reproduz a chamada exatamente como o HttpCollector faz, mas:
- Loga status, headers de resposta e corpo COMPLETO (até 4000 chars).
- Testa o payload base, depois variações isolando cada campo, para descobrir
  qual está sendo rejeitado pelo backend do iManager.
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
trace_task_ids = event["integration"].get("trace_task_ids", [])
# Permite override via session.json caso playwright tenha capturado
sess_task_id = sess_data.get("trace", {}).get("task_id")
if sess_task_id:
    trace_task_ids = [sess_task_id]

print(f"base_url     = {base_url}")
print(f"task_id      = {trace_task_ids}")
print(f"roarand head = {sess_data['trace'].get('roarand', '')[:20]}...")
print(f"cookies      = {len(sess_data['trace'].get('cookies', []))} itens")
print()


def build_session():
    s = requests.Session()
    s.verify = False
    for cookie in sess_data["trace"]["cookies"]:
        s.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""))
    roarand = sess_data["trace"].get("roarand")
    if roarand:
        s.headers.update({"roarand": roarand})
    # Headers comuns que o browser real envia ao iManager
    s.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    })
    return s


def call(label: str, payload: dict, extra_headers: dict | None = None):
    s = build_session()
    if extra_headers:
        s.headers.update(extra_headers)
    now_ms = int(time.time() * 1000)
    url = f"{base_url}/rest/oss/access/fars/v1/traceresult/query/sort?nocache={now_ms}"

    print("=" * 70)
    print(f">> {label}")
    print(f"   payload: {json.dumps(payload, ensure_ascii=False)}")
    try:
        resp = s.post(url, json=payload, timeout=30)
    except Exception as e:
        print(f"   EXCEPTION: {e}")
        return

    ct = resp.headers.get("Content-Type", "")
    print(f"   status:  {resp.status_code}")
    print(f"   ct:      {ct}")
    body = resp.text or ""
    # tenta json bonito
    try:
        parsed = resp.json()
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
        print(f"   body:    {pretty[:3000]}")
    except Exception:
        print(f"   body:    {body[:3000]}")
    print()


task_id = trace_task_ids[0] if trace_task_ids else 1925

# Payload exatamente como o collector envia hoje
base_payload = {
    "msgId": -1,
    "comparisonMsgId": -1,
    "taskId": task_id,
    "isAscend": True,
    "sqlColumnName": "Time",
    "startRow": 0,
    "pageSize": 1000,
    "templateName": [],
    "isSetBenchMarkTime": False,
    "benchMarkTimeRowNo": -1,
}

call("A) Payload atual (como está no collector)", base_payload)

# Variação 1: pageSize menor (alguns FARS limitam)
call("B) pageSize=100", {**base_payload, "pageSize": 100})

# Variação 2: msgId 0 em vez de -1
call("C) msgId=0", {**base_payload, "msgId": 0})

# Variação 3: sem campos opcionais (mínimo possível)
call("D) Mínimo absoluto", {"taskId": task_id, "startRow": 0, "pageSize": 100})

# Variação 4: com Referer (alguns endpoints validam)
call(
    "E) Com Referer + Origin",
    base_payload,
    extra_headers={
        "Referer": f"{base_url}/ossfacewebsite/index.html",
        "Origin": base_url,
    },
)

# Variação 5: taskId como string (algumas APIs Huawei exigem)
call("F) taskId como string", {**base_payload, "taskId": str(task_id)})

# Variação 6: sqlColumnName=None / outros campos
call("G) sqlColumnName vazio", {**base_payload, "sqlColumnName": ""})

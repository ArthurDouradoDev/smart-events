"""
Inspeciona o formato de retorno do query/result e verifica:
- Quais GULTrcMsgType aparecem (estatística)
- Se há RSRP/RSRQ no payload direto (sem precisar de msg-explain-info)
- Como casar com IMSI dos VIPs
- Como o msg-explain-info funciona para extrair RSRP de uma RRC_MEAS_RPRT
"""

import json
import time
from collections import Counter
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


s = build_session()
now = lambda: int(time.time() * 1000)

# Inicializa sessão de query
pre = s.get(f"{base_url}/rest/oss/access/fars/v1/traceresult/pre-check",
            params={"taskId": task_id, "queryType": 0, "nocache": now()}, timeout=30)
print(f"pre-check: {pre.status_code} {pre.json()}\n")

# Puxa a primeira página
resp = s.get(f"{base_url}/rest/oss/access/fars/v1/traceresult/query/result",
             params={
                 "nocache": now(),
                 "startRow": 0,
                 "pageSize": 1000,
                 "taskId": task_id,
                 "msgId": 1,
                 "isSetBenchMarkTime": "false",
                 "benchMarkTimeRowNo": -1,
             }, timeout=60)
print(f"query/result: {resp.status_code}")
data = resp.json()
print(f"recordCount: {data.get('recordCount')}")
print(f"msgId interno: {data.get('data', {}).get('msgId')}")

table = data.get("data", {}).get("tableData", [])
print(f"linhas retornadas: {len(table)}\n")

# Estatística de tipos de mensagem
msg_types = Counter()
for row in table:
    payload = row.get("payload", [])
    for field in payload:
        if field.get("name") == "GULTrcMsgType":
            msg_types[field.get("value")] += 1
            break

print("Top tipos de mensagem:")
for k, v in msg_types.most_common(15):
    print(f"  {v:6d}  {k}")
print()

# Procura por algum campo que sugira RSRP/RSRQ no payload nativo
print("Procurando RSRP no payload das mensagens RRC_MEAS_RPRT:")
found = 0
for row in table:
    payload = row.get("payload", [])
    type_field = next((f for f in payload if f.get("name") == "GULTrcMsgType"), None)
    if not type_field or type_field.get("value") != "RRC_MEAS_RPRT":
        continue
    found += 1
    if found > 2:
        break
    print(f"\n  --- linha serialNo={row.get('serialNo')} ---")
    # imprime todo o payload
    for f in payload:
        print(f"    {f.get('name'):25s} = {str(f.get('value'))[:80]}")
        for child in f.get("children", []):
            print(f"      → {child.get('name'):20s} = {str(child.get('value'))[:80]}")

if found == 0:
    print("  Nenhum RRC_MEAS_RPRT na primeira página. Mostrando 2 primeiras linhas inteiras:")
    for row in table[:2]:
        print(f"\n  --- linha serialNo={row.get('serialNo')} ---")
        print(json.dumps(row, indent=2, ensure_ascii=False)[:2000])

# Tenta o msg-explain-info da primeira linha pra ver se traz mais info
if table:
    first = table[0]
    serial_no = first.get("serialNo")
    msg_id_query = data.get("data", {}).get("msgId")
    print(f"\n\n--- Testando msg-explain-info (serialNo={serial_no}, msgId={msg_id_query}) ---")
    info_resp = s.get(f"{base_url}/rest/oss/access/fars/v1/traceresult/query/msg-explain-info",
                      params={
                          "nocache": now(),
                          "taskId": task_id,
                          "msgId": msg_id_query,
                          "rowNo": 0,
                          "tabularFlag": "y",
                          "isSubscribe": "false",
                          "isSecondDecode": "false",
                      }, timeout=30)
    print(f"status: {info_resp.status_code}")
    try:
        info_text = json.dumps(info_resp.json(), indent=2, ensure_ascii=False)
        print(info_text[:3000])
    except Exception:
        print(info_resp.text[:1500])

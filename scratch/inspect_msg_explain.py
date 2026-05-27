"""
Tenta decodificar uma mensagem RRC_MEAS_RPRT via msg-explain-info e ver
se RSRP/RSRQ aparecem decodificados (provavelmente o "rsrpResult: ----  0x30(48)"
que o nosso _extract_rsrp_rsrq espera).
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
print(f"pre-check: {pre.status_code}\n")

# Puxa primeira página e localiza um RRC_MEAS_RPRT
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
data = resp.json()
msg_id_query = data.get("data", {}).get("msgId")
table = data.get("data", {}).get("tableData", [])

# Pega índice e serialNo do primeiro RRC_MEAS_RPRT
meas_rows = []
for idx, row in enumerate(table):
    pl = row.get("payload", [])
    t = next((f for f in pl if f.get("name") == "GULTrcMsgType"), None)
    if t and t.get("value") == "RRC_MEAS_RPRT":
        meas_rows.append((idx, row.get("serialNo"), row))
    if len(meas_rows) >= 3:
        break

print(f"msgId da query: {msg_id_query}")
print(f"Achei {len(meas_rows)} RRC_MEAS_RPRT nas primeiras 1000 mensagens.\n")

# Tenta diferentes rowNo / msgId combos
for label, row_no_calc in [
    ("rowNo = idx (0-based)", lambda idx: idx),
    ("rowNo = idx+1 (1-based)", lambda idx: idx + 1),
    ("rowNo = serialNo", lambda idx, sn=None: sn),
]:
    if not meas_rows:
        break
    idx, sn, row = meas_rows[0]
    row_no = row_no_calc(idx) if "serialNo" not in label else sn
    print(f"=== {label}  →  rowNo={row_no}, msgId={msg_id_query} ===")
    info_resp = s.get(f"{base_url}/rest/oss/access/fars/v1/traceresult/query/msg-explain-info",
                      params={
                          "nocache": now(),
                          "taskId": task_id,
                          "msgId": msg_id_query,
                          "rowNo": row_no,
                          "tabularFlag": "y",
                          "isSubscribe": "false",
                          "isSecondDecode": "false",
                      },
                      headers={"showLoading": "true"}, timeout=30)
    print(f"  status: {info_resp.status_code}")
    body = info_resp.text or ""
    # se for OK, procura por RSRP
    if info_resp.status_code == 200:
        lower = body.lower()
        if "rsrp" in lower:
            print("  ✅ CONTÉM 'rsrp' no body")
            # acha a localização
            idx_rsrp = lower.find("rsrp")
            print(f"  trecho: {body[max(0, idx_rsrp-50):idx_rsrp+300]}")
        else:
            print(f"  body (primeiros 600 chars): {body[:600]}")
    else:
        print(f"  body: {body[:400]}")
    print()

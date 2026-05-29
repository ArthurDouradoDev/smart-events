"""Verifica se serialNo dos itens filtrados bate com rowNo=60 do curl do usuario."""
import json, requests, urllib3, time
from datetime import datetime
urllib3.disable_warnings()

BASE_URL = "https://10.220.30.9:31943"
VIP_TASK_ID = 14127

# Usa a sessao fresh do arquivo do usuario (new-explain-info.txt)
# bspsession capturado diretamente do curl
SESSION_COOKIE = "x-tifvfy8ak69hdhgbg7tjs5lco49h6lmq0auos95c6ohc0afs3umrtgs4471h3vhd6l7weoilmlfwry2p6qurin6ks8tfobs5iphcc6umntobddrstinsgalguqpjpfti"
ROARAND = "5b169ceca79142bd03a148f21d9b5b2545d0c80ffc3e5122"

sess = requests.Session()
sess.verify = False
sess.cookies.set("bspsession", SESSION_COOKIE, domain="10.220.30.9")
sess.headers.update({"roarand": ROARAND})

# pre-check
pre = sess.get(f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/pre-check",
    params={"taskId": VIP_TASK_ID, "queryType": 0, "nocache": int(time.time()*1000)},
    timeout=30)
print(f"pre-check: {pre.status_code}")
try:
    check_state = pre.json().get("checkState")
    print(f"checkState: {check_state}")
    if not check_state:
        print("Sessao expirou. Precisa gerar nova sessao com get_session_regional.py")
        exit(1)
except Exception as e:
    print(f"pre-check parse error: {e} | body: {pre.text[:200]}")
    exit(1)

# query/result
res = sess.get(f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/query/result",
    params={"nocache": int(time.time()*1000), "startRow": 0, "pageSize": 10,
            "taskId": VIP_TASK_ID, "msgId": 1,
            "isSetBenchMarkTime": "false", "benchMarkTimeRowNo": -1},
    timeout=30)
sess_msg_id = (res.json().get("data") or {}).get("msgId")
print(f"query/result: {res.status_code}, sess_msg_id={sess_msg_id}")

# filter-by-cols — pega os 10 primeiros para ver serialNos
filter_payload = {
    "colFilterDto": {
        "colFltExpSeq": [{"fieldId": "Message Type", "value": "RRC_MEAS_RPRT", "operator": {"op": 0}}],
        "signalList": [], "hasStartTime": False, "startTime": "2000-01-01 00:00:00",
        "hasEndTime": False, "endTime": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), "isReverse": False
    },
    "pageDto": {
        "sqlColumnName": "Time", "isAscend": False, "taskId": VIP_TASK_ID, "msgId": sess_msg_id,
        "comparisonMsgId": -1, "startRow": 0, "pageSize": 10, "templateName": [],
        "isSetBenchMarkTime": False, "benchMarkTimeRowNo": -1
    }
}
now_ms = int(time.time()*1000)
fr = sess.post(f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/query/filter-by-cols?nocache={now_ms}",
    json=filter_payload, timeout=60)
table = (fr.json().get("data") or {}).get("tableData", [])
print(f"filter-by-cols: {fr.status_code}, registros={len(table)}")

# Inspecionar serialNo de cada item
print("\n--- serialNo dos primeiros 10 itens filtrados ---")
for i, item in enumerate(table[:10]):
    serial = item.get("serialNo", "N/A")
    source = item.get("source", "")
    fields = {f.get("name", ""): f.get("value", "") for f in (item.get("payload") or [])}
    msg_type = fields.get("Message Type", "?")
    ts = fields.get("Time", "?")
    print(f"  idx={i} -> serialNo={serial} source={source} type={msg_type} ts={ts[:20]}")

# Testar msg-explain-info com serialNo do primeiro item (em vez de idx+1)
if table:
    first_serial = table[0].get("serialNo")
    print(f"\n--- msg-explain-info com rowNo=serialNo ({first_serial}) ---")
    if first_serial:
        ex = sess.get(f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/query/msg-explain-info",
            params={"nocache": int(time.time()*1000), "taskId": VIP_TASK_ID, "msgId": sess_msg_id,
                    "rowNo": first_serial, "tabularFlag": "y", "isSubscribe": "false",
                    "isSecondDecode": "false", "isPlayback": "false"},
            timeout=30)
        print(f"HTTP: {ex.status_code}")
        content = ex.json()
        has_bin = "binMsgExplain" in content
        print(f"tem binMsgExplain: {has_bin}")
        if has_bin:
            def extract_rsrp_rsrq(c):
                rsrp = rsrq = None
                def pi(v):
                    try: return int(str(v).split("(")[1].split(")")[0])
                    except: return None
                def visit(n):
                    nonlocal rsrp, rsrq
                    if isinstance(n, dict):
                        nm = n.get("name", "")
                        vl = n.get("val") or n.get("value") or ""
                        if rsrp is None and "rsrpResult" in nm and "0x" in str(vl):
                            idx = pi(str(vl))
                            if idx is not None: rsrp = idx - 140.0
                        if rsrq is None and "rsrqResult" in nm and "0x" in str(vl):
                            idx = pi(str(vl))
                            if idx is not None: rsrq = (idx / 2.0) - 19.5
                        for ch in n.get("children", []) or []:
                            if rsrp is not None and rsrq is not None: return
                            visit(ch)
                        for k, vv in n.items():
                            if rsrp is not None and rsrq is not None: return
                            if k == "children": continue
                            if isinstance(vv, (dict, list)): visit(vv)
                    elif isinstance(n, list):
                        for item in n:
                            if rsrp is not None and rsrq is not None: return
                            visit(item)
                visit(c)
                return rsrp, rsrq
            rsrp, rsrq = extract_rsrp_rsrq(content)
            print(f"RSRP={rsrp} dBm, RSRQ={rsrq} dB")
        else:
            tab = str(content.get("tabular", ""))[:100]
            print(f"tipo retornado (tabular inicio): {tab}")

    # Comparar com rowNo=idx+1=1
    print(f"\n--- msg-explain-info com rowNo=idx+1 (1) ---")
    ex2 = sess.get(f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/query/msg-explain-info",
        params={"nocache": int(time.time()*1000), "taskId": VIP_TASK_ID, "msgId": sess_msg_id,
                "rowNo": 1, "tabularFlag": "y", "isSubscribe": "false",
                "isSecondDecode": "false", "isPlayback": "false"},
        timeout=30)
    content2 = ex2.json()
    has_bin2 = "binMsgExplain" in content2
    print(f"tem binMsgExplain: {has_bin2}")
    if not has_bin2:
        tab = str(content2.get("tabular", ""))
        first_word = tab.strip().split("\n")[0][:80] if tab else "(vazio)"
        print(f"tipo (tabular primeira linha): {first_word}")

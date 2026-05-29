import json, requests, urllib3, time
from datetime import datetime
urllib3.disable_warnings()

BASE_URL = "https://10.220.30.9:31943"
VIP_TASK_ID = 14127

# Verifica se a funcao de extracao funciona com o wrapper binMsgExplain
sample_response = {
    "binMsgExplain": {
        "filedTree": {
            "name": "RRC-MSG",
            "val": "",
            "children": [
                {"name": "measResultPCell", "val": "", "children": [
                    {"name": "rsrpResult", "val": ": ---- 0x40(64) ---- *1000000", "children": []},
                    {"name": "rsrqResult", "val": ": ---- 0x15(21) ---- 010101**", "children": []}
                ]}
            ]
        }
    }
}

def extract_rsrp_rsrq(content):
    rsrp = rsrq = None
    def parse_index(val_str):
        try:
            return int(val_str.split("(")[1].split(")")[0])
        except Exception:
            return None
    def visit(node):
        nonlocal rsrp, rsrq
        if isinstance(node, dict):
            name = node.get("name", "")
            val = node.get("val") or node.get("value") or ""
            if rsrp is None and "rsrpResult" in name and "0x" in str(val):
                idx = parse_index(str(val))
                if idx is not None:
                    rsrp = idx - 140.0
            if rsrq is None and "rsrqResult" in name and "0x" in str(val):
                idx = parse_index(str(val))
                if idx is not None:
                    rsrq = (idx / 2.0) - 19.5
            for child in node.get("children", []) or []:
                if rsrp is not None and rsrq is not None:
                    return
                visit(child)
            for k, v in node.items():
                if rsrp is not None and rsrq is not None:
                    return
                if k == "children":
                    continue
                if isinstance(v, (dict, list)):
                    visit(v)
        elif isinstance(node, list):
            for item in node:
                if rsrp is not None and rsrq is not None:
                    return
                visit(item)
    visit(content)
    return rsrp, rsrq

rsrp, rsrq = extract_rsrp_rsrq(sample_response)
print(f"Extracao com binMsgExplain wrapper -> RSRP={rsrp} dBm, RSRQ={rsrq} dB")
print(f"Extracao OK: {rsrp is not None and rsrq is not None}")
print()

# Refaz o fluxo completo com sessao fresca + isPlayback=false
with open("data/session_regional.json", "r") as f:
    data = json.load(f)

sess = requests.Session()
sess.verify = False
for c in data["trace"]["cookies"]:
    sess.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
sess.headers.update({"roarand": data["trace"]["roarand"]})

# pre-check
pre = sess.get(
    f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/pre-check",
    params={"taskId": VIP_TASK_ID, "queryType": 0, "nocache": int(time.time()*1000)},
    timeout=30
)
check_state = pre.json().get("checkState")
print(f"pre-check: {pre.status_code}, checkState={check_state}")

# query/result
res = sess.get(
    f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/query/result",
    params={
        "nocache": int(time.time()*1000), "startRow": 0, "pageSize": 10,
        "taskId": VIP_TASK_ID, "msgId": 1,
        "isSetBenchMarkTime": "false", "benchMarkTimeRowNo": -1,
    },
    timeout=30
)
sess_msg_id = (res.json().get("data") or {}).get("msgId")
print(f"query/result: {res.status_code}, sess_msg_id={sess_msg_id}")

# filter-by-cols
filter_payload = {
    "colFilterDto": {
        "colFltExpSeq": [{"fieldId": "Message Type", "value": "RRC_MEAS_RPRT", "operator": {"op": 0}}],
        "signalList": [], "hasStartTime": False, "startTime": "2000-01-01 00:00:00",
        "hasEndTime": False, "endTime": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), "isReverse": False
    },
    "pageDto": {
        "sqlColumnName": "Time", "isAscend": False, "taskId": VIP_TASK_ID, "msgId": sess_msg_id,
        "comparisonMsgId": -1, "startRow": 0, "pageSize": 5, "templateName": [],
        "isSetBenchMarkTime": False, "benchMarkTimeRowNo": -1
    }
}
now_ms = int(time.time()*1000)
fr = sess.post(
    f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/query/filter-by-cols?nocache={now_ms}",
    json=filter_payload, timeout=60
)
table = (fr.json().get("data") or {}).get("tableData", [])
print(f"filter-by-cols: {fr.status_code}, registros={len(table)}")

# msg-explain-info COM isPlayback=false para rowNo=1
ex = sess.get(
    f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/query/msg-explain-info",
    params={
        "nocache": int(time.time()*1000), "taskId": VIP_TASK_ID, "msgId": sess_msg_id,
        "rowNo": 1, "tabularFlag": "y", "isSubscribe": "false",
        "isSecondDecode": "false", "isPlayback": "false",
    },
    timeout=30
)
print(f"msg-explain-info rowNo=1 + isPlayback=false: {ex.status_code}")
content = ex.json()
top_keys = list(content.keys()) if isinstance(content, dict) else type(content)
print(f"TOP KEYS: {top_keys}")
has_bin = "binMsgExplain" in content
print(f"tem binMsgExplain: {has_bin}")

if has_bin:
    rsrp, rsrq = extract_rsrp_rsrq(content)
    print(f"RSRP={rsrp} dBm, RSRQ={rsrq} dB")

    def find_names(node, depth=0):
        if depth > 8:
            return
        if isinstance(node, dict):
            n = node.get("name", "")
            v = str(node.get("val", ""))
            if n:
                print("  " * depth + f"{n}: {v[:60]}")
            for c in node.get("children", []) or []:
                find_names(c, depth+1)
            for k, vv in node.items():
                if k not in ("name", "val", "children", "attrType", "offset", "len"):
                    if isinstance(vv, (dict, list)):
                        find_names(vv, depth)
        elif isinstance(node, list):
            for item in node[:10]:
                find_names(item, depth)

    print("--- Arvore binMsgExplain ---")
    find_names(content.get("binMsgExplain", {}))
else:
    # Mostra o campo tabular para identificar tipo da mensagem
    tabular = content.get("tabular", "")
    first_line = str(tabular)[:120] if tabular else "(vazio)"
    print(f"tabular (inicio): {first_line}")

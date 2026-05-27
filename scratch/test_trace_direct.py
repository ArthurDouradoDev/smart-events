import json
import time
import requests
import urllib3

urllib3.disable_warnings()

session_path = r"c:\Users\a50057663\Desktop\Automações\SmartEvents\data\session.json"
with open(session_path, "r", encoding="utf-8") as f:
    sess_data = json.load(f)

base_url = "https://10.220.50.9:31943"

s = requests.Session()
s.verify = False
for cookie in sess_data["trace"]["cookies"]:
    s.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""))
roarand = sess_data["trace"].get("roarand")
if roarand:
    s.headers.update({"roarand": roarand})

now_ms = int(time.time() * 1000)
trace_url = f"{base_url}/rest/oss/access/fars/v1/traceresult/query/sort?nocache={now_ms}"

# Test with different msgId values
for msg_id in [-1, 0, 1]:
    payload = {
        "msgId": msg_id,
        "comparisonMsgId": -1,
        "taskId": 1925,
        "isAscend": True,
        "sqlColumnName": "Time",
        "startRow": 0,
        "pageSize": 10,
        "templateName": [],
        "isSetBenchMarkTime": False,
        "benchMarkTimeRowNo": -1
    }
    resp = s.post(trace_url, json=payload, timeout=30)
    print(f"msgId={msg_id}: status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(json.dumps(data, indent=2)[:2000])
        break
    else:
        print(f"Response: {resp.text[:500]}")

import json
import time
import requests
import urllib3

urllib3.disable_warnings()

session_path = r"c:\Users\a50057663\Desktop\Automações\SmartEvents\data\session.json"
with open(session_path, "r", encoding="utf-8") as f:
    sess_data = json.load(f)

base_url = "https://10.220.50.9:31943"

# Build session for monitoring
s = requests.Session()
s.verify = False
for cookie in sess_data["monitoring"]["cookies"]:
    s.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""))
roarand = sess_data["monitoring"].get("roarand")
if roarand:
    s.headers.update({"roarand": roarand})

now_ms = int(time.time() * 1000)
task_id = 374  # From event config integration.pm_task_id

payload = [{
    "taskId": task_id,
    "preExecTime": now_ms,
    "objNoExecTimes": []  # Empty = all objects in task
}]

print(f"Testing KPI endpoint with taskId={task_id}...")
url = f"{base_url}/rest/oss/access/pm/v1/monitor/task/result?nocache={now_ms}"
resp = s.post(url, json=payload, timeout=30, headers={"x-non-renewal-session": "true"})
print(f"Status: {resp.status_code}")
print(f"Content-Type: {resp.headers.get('Content-Type', '')}")
try:
    data = resp.json()
    print("Response JSON (truncated):")
    text = json.dumps(data, indent=2)
    print(text[:3000])
except Exception:
    print("Response text (first 2000 chars):")
    print(resp.text[:2000])

print("\n\n--- Testing Trace endpoint ---")
s2 = requests.Session()
s2.verify = False
for cookie in sess_data["trace"]["cookies"]:
    s2.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""))
roarand_trace = sess_data["trace"].get("roarand")
if roarand_trace:
    s2.headers.update({"roarand": roarand_trace})

trace_task_id = 1925  # From event config integration.trace_task_ids[0]
now_ms2 = int(time.time() * 1000)
trace_url = f"{base_url}/rest/oss/access/fars/v1/traceresult/query/sort?nocache={now_ms2}"
trace_payload = {
    "msgId": -1,
    "comparisonMsgId": -1,
    "taskId": trace_task_id,
    "isAscend": True,
    "sqlColumnName": "Time",
    "startRow": 0,
    "pageSize": 100,
    "templateName": [],
    "isSetBenchMarkTime": False,
    "benchMarkTimeRowNo": -1
}

resp2 = s2.post(trace_url, json=trace_payload, timeout=30)
print(f"Status: {resp2.status_code}")
print(f"Content-Type: {resp2.headers.get('Content-Type', '')}")
try:
    data2 = resp2.json()
    print("Response JSON (truncated):")
    print(json.dumps(data2, indent=2)[:3000])
except Exception:
    print("Response text (first 2000 chars):")
    print(resp2.text[:2000])

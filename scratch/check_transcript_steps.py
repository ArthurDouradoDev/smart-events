import json
from pathlib import Path

transcript_path = Path(r"C:\Users\a50057663\.gemini\antigravity-ide\brain\7992c32b-3b17-42cc-aac2-b69f8f912bbe\.system_generated\logs\transcript.jsonl")

print("Checking steps 280 to 290 in current transcript:")
if transcript_path.exists():
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                step = json.loads(line)
                idx = step.get("step_index")
                if 280 <= idx <= 290:
                    print(f"Step {idx}: type={step.get('type')}, source={step.get('source')}, content length={len(step.get('content', ''))}")
                    tcs = step.get("tool_calls", [])
                    if tcs:
                        for tc in tcs:
                            print(f"  Tool call: {tc.get('name')}, args keys={list(tc.get('args', {}).keys())}")
            except Exception as e:
                print("Error parsing line:", e)
else:
    print("Not found")

import json
from pathlib import Path

conv_id = "1dd16908-2017-40c2-89bf-67b6300ed851"
transcript_path = Path(fr"C:\Users\a50057663\.gemini\antigravity-ide\brain\{conv_id}\.system_generated\logs\transcript.jsonl")

print(f"Checking all steps targeting collector.py in {conv_id}:")
if transcript_path.exists():
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                step = json.loads(line)
                content = step.get("content", "")
                idx = step.get("step_index")
                
                # Check tool calls
                tcs = step.get("tool_calls", [])
                for tc in tcs:
                    args = tc.get("args", {})
                    target = args.get("TargetFile") or args.get("Target")
                    if target and "collector.py" in str(target):
                        print(f"Step {idx}: Tool Call {tc.get('name')}, content len={len(content)}")
                        
                # Check if it was a file view result
                if "collector.py" in content and ("class HttpCollector" in content or "class CsvCollector" in content):
                    print(f"Step {idx}: Type={step.get('type')}, content len={len(content)}")
            except Exception as e:
                pass
else:
    print("Not found")

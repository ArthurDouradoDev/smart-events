import json
from pathlib import Path

conv_id = "7992c32b-3b17-42cc-aac2-b69f8f912bbe"
transcript_path = Path(fr"C:\Users\a50057663\.gemini\antigravity-ide\brain\{conv_id}\.system_generated\logs\transcript.jsonl")

print("Searching current transcript for HttpCollector:")
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
                    code = args.get("CodeContent") or args.get("ReplacementContent") or ""
                    if "HttpCollector" in code or "HttpCollector" in str(target):
                        print(f"Step {idx}: Tool Call {tc.get('name')}, code len={len(code)}")
                        
                # Check if it was a file view result or something containing HttpCollector
                if "HttpCollector" in content:
                    print(f"Step {idx}: Type={step.get('type')}, content len={len(content)}")
            except Exception as e:
                pass
else:
    print("Not found")

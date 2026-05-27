import os
import json
from pathlib import Path

brain_dir = Path(r"C:\Users\a50057663\.gemini\antigravity-ide\brain")

for p in brain_dir.iterdir():
    if p.is_dir():
        transcript_path = p / ".system_generated" / "logs" / "transcript.jsonl"
        if transcript_path.exists():
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        step = json.loads(line)
                        tcs = step.get("tool_calls", [])
                        for tc in tcs:
                            args = tc.get("args", {})
                            target = args.get("TargetFile") or args.get("Target")
                            if target and "collector.py" in str(target):
                                print(f"Conv: {p.name}, Step: {step.get('step_index')}, Tool: {tc.get('name')}, Keys: {list(args.keys())}")
                                # Print keys of args and first 100 chars of code
                                code = args.get("CodeContent") or args.get("ReplacementContent")
                                if code:
                                    print(f"  Code len: {len(code)}, Snippet: {repr(code[:100])}")
                    except Exception as e:
                        pass

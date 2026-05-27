import json
from pathlib import Path

transcript_path = Path(r"C:\Users\a50057663\.gemini\antigravity-ide\brain\7992c32b-3b17-42cc-aac2-b69f8f912bbe\.system_generated\logs\transcript.jsonl")

with open(transcript_path, "r", encoding="utf-8") as f:
    for line in f:
        try:
            step = json.loads(line)
            if step.get("step_index") == 282:
                print("Keys:", list(step.keys()))
                for k, v in step.items():
                    if k != "content":
                        print(f"{k}: {repr(v)[:200]}")
                    else:
                        print(f"content length: {len(v)}")
                        print(f"content start: {repr(v[:300])}")
                        print(f"content end: {repr(v[-300:])}")
        except Exception as e:
            pass

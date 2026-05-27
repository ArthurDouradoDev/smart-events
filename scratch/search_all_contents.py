import os
import json
from pathlib import Path

brain_dir = Path(r"C:\Users\a50057663\.gemini\antigravity-ide\brain")

print("Searching all steps' content for HttpCollector...")
matches = []
for p in brain_dir.iterdir():
    if p.is_dir():
        transcript_path = p / ".system_generated" / "logs" / "transcript.jsonl"
        if transcript_path.exists():
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        step = json.loads(line)
                        content = step.get("content", "")
                        if "class HttpCollector" in content:
                            matches.append((p.name, step.get("step_index"), step.get("type"), len(content)))
                    except Exception:
                        pass

print(f"Found {len(matches)} matches:")
for m in matches:
    print(f"Conv: {m[0]}, Step: {m[1]}, Type: {m[2]}, Content Length: {m[3]}")

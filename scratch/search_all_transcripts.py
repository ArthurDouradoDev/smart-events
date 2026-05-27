import os
import json
from pathlib import Path

brain_dir = Path(r"C:\Users\a50057663\.gemini\antigravity-ide\brain")

print("Searching all transcripts for collector.py...")
found = []
for p in brain_dir.iterdir():
    if p.is_dir():
        transcript_path = p / ".system_generated" / "logs" / "transcript.jsonl"
        if transcript_path.exists():
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        step = json.loads(line)
                        content = step.get("content", "")
                        # Look for a specific signature of our completed collector.py
                        if "class HttpCollector" in content and "def _parse_kpi_response" in content and len(content) > 5000:
                            found.append((p.name, step.get("step_index"), len(content)))
                    except Exception:
                        pass

print(f"Found {len(found)} matches:")
for f_item in found:
    print(f"Folder: {f_item[0]}, Step: {f_item[1]}, Length: {f_item[2]}")

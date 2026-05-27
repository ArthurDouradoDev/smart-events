import json
import re
from pathlib import Path

TRANSCRIPT_PATH = Path(r"C:\Users\a50057663\.gemini\antigravity-ide\brain\7992c32b-3b17-42cc-aac2-b69f8f912bbe\.system_generated\logs\transcript.jsonl")

def clean_lines(text):
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        match = re.match(r"^\s*(\d+):\s?(.*)$", line)
        if match:
            cleaned.append(match.group(2))
    return "\n".join(cleaned)

parts = {}
with open(TRANSCRIPT_PATH, "r", encoding="utf-8") as f:
    for line in f:
        try:
            step = json.loads(line)
            idx = step.get("step_index")
            if idx in (282, 284, 286):
                parts[idx] = clean_lines(step.get("content", ""))
        except Exception as e:
            pass

if len(parts) == 3:
    full_code = parts[282] + "\n" + parts[284] + "\n" + parts[286]
    with open("scratch/stitched_collector.py", "w", encoding="utf-8") as f:
        f.write(full_code)
    print("Stitched code saved to scratch/stitched_collector.py. Length:", len(full_code))
    
    # Check if old_collect_kpis exists in stitched code
    print("Has old_collect_kpis signature:")
    print("def collect_kpis" in full_code)
    # Print lines around "def collect_kpis"
    lines = full_code.split("\n")
    for i, line in enumerate(lines):
        if "def collect_kpis" in line:
            print(f"Line {i}: {line}")
            for j in range(max(0, i-5), min(len(lines), i+20)):
                print(f"  {j}: {lines[j]}")
else:
    print("Could not find all parts. Found:", list(parts.keys()))

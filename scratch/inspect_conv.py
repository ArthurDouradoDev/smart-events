import json
from pathlib import Path

conv_id = "0837b2f6-1df5-4465-b15b-258357524696"
transcript_path = Path(fr"C:\Users\a50057663\.gemini\antigravity-ide\brain\{conv_id}\.system_generated\logs\transcript.jsonl")

if transcript_path.exists():
    with open(transcript_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        print(f"Total lines in {conv_id}: {len(lines)}")
        if lines:
            print("First line:")
            print(repr(lines[0][:300]))
else:
    print("Transcript not found")

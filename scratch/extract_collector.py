import json
import re
from pathlib import Path

brain_dir = Path(r"C:\Users\a50057663\.gemini\antigravity-ide\brain")

def find_collector_code(conv_id):
    transcript_path = brain_dir / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
    if not transcript_path.exists():
        print(f"Transcript for {conv_id} does not exist at {transcript_path}")
        return None
    
    print(f"Searching {conv_id}...")
    collector_versions = []
    
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                step = json.loads(line)
                tool_calls = step.get("tool_calls", [])
                for tc in tool_calls:
                    args = tc.get("args", {})
                    # Look for write_to_file or replace_file_content
                    target = args.get("TargetFile") or args.get("Target")
                    if target and ("collector.py" in str(target)):
                        code = args.get("CodeContent") or args.get("ReplacementContent")
                        if code:
                            collector_versions.append((step.get("step_index"), tc.get("name"), len(code), code))
            except Exception as e:
                pass
                
    return collector_versions

# Let's search current conversation first
current_id = "7992c32b-3b17-42cc-aac2-b69f8f912bbe"
versions = find_collector_code(current_id)
if versions:
    print(f"Found {len(versions)} versions in current conversation:")
    for idx, name, length, _ in versions:
        print(f"Step {idx}: tool {name}, size {length}")
else:
    print("None found in current conversation.")

# Search previous conversation
prev_id = "0837b2f6-1df5-4465-b15b-258357524696"
versions_prev = find_collector_code(prev_id)
if versions_prev:
    print(f"Found {len(versions_prev)} versions in previous conversation:")
    for idx, name, length, _ in versions_prev:
        print(f"Step {idx}: tool {name}, size {length}")
    
    # Save the longest / latest version
    latest = versions_prev[-1]
    print(f"Saving latest version from step {latest[0]} (length {latest[2]})")
    with open("scratch/recovered_collector.py", "w", encoding="utf-8") as out:
        out.write(latest[3])
    print("Saved to scratch/recovered_collector.py")
else:
    print("None found in previous conversation.")

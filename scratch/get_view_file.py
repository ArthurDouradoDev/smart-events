import json
from pathlib import Path

TRANSCRIPT_PATH = Path(r"C:\Users\a50057663\\.gemini\antigravity-ide\brain\7992c32b-3b17-42cc-aac2-b69f8f912bbe\.system_generated\logs\transcript.jsonl")

def main():
    if not TRANSCRIPT_PATH.exists():
        print("Transcript file not found!")
        return

    code_parts = {}
    with open(TRANSCRIPT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            step = json.loads(line)
            idx = step.get("step_index")
            if idx in (282, 284, 286):
                # The output content of the step is in step["content"] or step["tool_calls"] or step["result"]
                print(f"Step {idx}: Type={step.get('type')}, Keys={list(step.keys())}")
                if "content" in step:
                    print(f"Content length: {len(step['content'])}")
                # Let's inspect the step structure
                print(json.dumps({k: str(step[k])[:100] for k in step}, indent=2))

if __name__ == "__main__":
    main()

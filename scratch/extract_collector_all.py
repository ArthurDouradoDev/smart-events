import json
from pathlib import Path

brain_dir = Path(r"C:\Users\a50057663\.gemini\antigravity-ide\brain")
conv_id = "7992c32b-3b17-42cc-aac2-b69f8f912bbe"
transcript_path = brain_dir / conv_id / ".system_generated" / "logs" / "transcript.jsonl"

with open(transcript_path, "r", encoding="utf-8") as f:
    for line in f:
        try:
            step = json.loads(line)
            tool_calls = step.get("tool_calls", [])
            for tc in tool_calls:
                args = tc.get("args", {})
                target = args.get("TargetFile") or args.get("Target")
                if target and ("collector.py" in str(target)):
                    code = args.get("CodeContent") or args.get("ReplacementContent")
                    if code:
                        name = tc.get("name")
                        step_idx = step.get("step_index")
                        out_path = Path(f"scratch/version_{step_idx}_{name}.py")
                        with open(out_path, "w", encoding="utf-8") as out:
                            out.write(code)
                        print(f"Saved step {step_idx} ({name}) to {out_path} (length {len(code)})")
        except Exception as e:
            pass

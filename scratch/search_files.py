import os
from pathlib import Path

workspace_dir = Path(r"c:\Users\a50057663\Desktop\Automações\SmartEvents")

print("Searching all .py files in workspace:")
for root, dirs, files in os.walk(workspace_dir):
    for f in files:
        if f.endswith(".py") or "collector" in f.lower():
            p = Path(root) / f
            print(p.relative_to(workspace_dir), f"size={p.stat().st_size}")

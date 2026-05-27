import os
from pathlib import Path

brain_dir = Path(r"C:\Users\a50057663\.gemini\antigravity-ide\brain")
print("Exists:", brain_dir.exists())
if brain_dir.exists():
    print("Contents:")
    for p in brain_dir.iterdir():
        print(p)

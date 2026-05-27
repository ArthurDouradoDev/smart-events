import json
from pathlib import Path

# Grab the actual content of the replacement files (they're JSON string-encoded)
for fname, step in [
    ("scratch/version_115_replace_file_content.py", 115),
    ("scratch/version_123_replace_file_content.py", 123),
    ("scratch/version_192_replace_file_content.py", 192),
    ("scratch/version_402_replace_file_content.py", 402),
    ("scratch/version_424_replace_file_content.py", 424),
    ("scratch/version_444_replace_file_content.py", 444),
    ("scratch/version_483_replace_file_content.py", 483),
]:
    with open(fname, "r", encoding="utf-8") as f:
        raw = f.read()

    print(f"\n=== Step {step} ===")
    # The files contain Python string literal content (raw code)
    # Try to parse it as JSON string
    try:
        decoded = json.loads(raw)
        # Save decoded version
        out = fname.replace(".py", "_decoded.txt")
        with open(out, "w", encoding="utf-8") as f2:
            f2.write(decoded)
        print(f"Decoded! Length: {len(decoded)}")
        print(f"Snippet: {repr(decoded[:200])}")
    except Exception as e:
        print(f"Not JSON-decodable: {e}")
        print(f"Length: {len(raw)}")
        print(f"Snippet: {repr(raw[:200])}")

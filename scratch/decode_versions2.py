import json
import re
from pathlib import Path

def decode_escaped_string(s):
    """Decode a Python-repr escaped string (like what the transcript stores)."""
    # Strip surrounding quotes if present
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1]
    # Decode common escape sequences
    s = s.replace("\\\\n", "\n")
    s = s.replace("\\\\t", "\t")
    s = s.replace('\\\\\\"', '"')
    s = s.replace("\\\\'", "'")
    s = s.replace("\\\\\\\\", "\\")
    return s

# Read each raw patch file and decode it
patches = {}
for fname, step in [
    ("scratch/version_115_replace_file_content.py", 115),
    ("scratch/version_424_replace_file_content.py", 424),
]:
    with open(fname, "r", encoding="utf-8") as f:
        raw = f.read()

    # The content is a Python string literal with double-escaped newlines
    # Strip the surrounding quote
    raw = raw.strip()
    if raw.startswith('"'):
        raw = raw[1:]
    if raw.endswith('"'):
        raw = raw[:-1]

    # Unescape
    decoded = raw.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
    patches[step] = decoded
    out = f"scratch/version_{step}_decoded.txt"
    with open(out, "w", encoding="utf-8") as f2:
        f2.write(decoded)
    print(f"Saved step {step} to {out} (length {len(decoded)})")
    print(f"Snippet: {repr(decoded[:200])}")
    print()

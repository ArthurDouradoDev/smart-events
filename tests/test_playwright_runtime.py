import json
import re
from pathlib import Path

import playwright


ROOT = Path(__file__).parents[1]


def _required_browser_dirs() -> set[str]:
    manifest_path = (
        Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
    )
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))["browsers"]
    required = {"chromium", "chromium-headless-shell", "ffmpeg", "winldd"}
    return {
        f"{entry['name'].replace('-', '_')}-{entry['revision']}"
        for entry in entries
        if entry["name"] in required
    }


def test_playwright_dependency_is_pinned_to_keep_driver_and_browser_compatible():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert re.search(r"(?m)^playwright==\d+\.\d+\.\d+$", requirements)


def test_pyinstaller_selects_revisions_from_the_installed_playwright_manifest():
    spec = (ROOT / "main.spec").read_text(encoding="utf-8")

    assert "browsers.json" in spec
    assert "entry['revision']" in spec
    assert "p.name.startswith(_browser_prefixes)" not in spec
    assert _required_browser_dirs()

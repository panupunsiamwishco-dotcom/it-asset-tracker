
#!/usr/bin/env python3
"""
apply_rerun_patch.py - Patch Streamlit apps that still use deprecated
st.experimental_rerun() by replacing them with st.rerun().

Usage:
  1) Put this file at the repo root (same level as your app.py).
  2) Run:  python apply_rerun_patch.py
  3) Commit and redeploy.
"""
import re
from pathlib import Path
from datetime import datetime

EXCLUDE_DIRS = {".git", ".venv", "venv", "__pycache__"}
PATTERN = re.compile(r"\bst\.experimental_rerun\s*\(")

def main(root: Path):
    changed = []
    for py in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in py.parts):
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception as e:
            print(f"[skip] {py}: {e}")
            continue
        if not PATTERN.search(src):
            continue
        backup = py.with_suffix(py.suffix + f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        backup.write_text(src, encoding="utf-8")
        dst = PATTERN.sub("st.rerun(", src)
        py.write_text(dst, encoding="utf-8")
        changed.append(str(py))
        print(f"[patched] {py}")
    if not changed:
        print("No files needed patching.")
    else:
        print("\nPatched files:")
        for c in changed:
            print(" -", c)

if __name__ == "__main__":
    main(Path(".").resolve())

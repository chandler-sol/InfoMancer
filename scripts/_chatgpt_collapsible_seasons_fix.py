from pathlib import Path

path = Path("docs/PACKAGING.md")
text = path.read_text(encoding="utf-8")
old = "**Media\nfiles and user-selected recovery packages are never deleted.**"
new = "**Media files and user-selected recovery packages are never deleted.**"
if text.count(old) != 1:
    raise RuntimeError(f"expected one wrapped media-safety sentence, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

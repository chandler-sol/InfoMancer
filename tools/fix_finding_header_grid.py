from pathlib import Path

path = Path("app/static/workspace.css")
text = path.read_text(encoding="utf-8")
old = '''.mie-finding-head {
  display: grid !important;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: start !important;
  justify-content: initial !important;
  gap: 12px !important;
}
.mie-finding-head > input[type="checkbox"] { margin: 7px 0 0 0; align-self: start; }
'''
new = '''.mie-finding-head {
  display: grid !important;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start !important;
  justify-content: initial !important;
  gap: 12px !important;
}
.mie-finding-head:has(> input[type="checkbox"]) {
  grid-template-columns: auto minmax(0, 1fr) auto;
}
.mie-finding-head > input[type="checkbox"] { margin: 7px 0 0 0; align-self: start; }
'''
if old not in text:
    raise SystemExit("finding header grid marker not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

path = Path("tests/test_safety_ui_stabilization.py")
text = path.read_text(encoding="utf-8")
old = '        self.assertIn("grid-template-columns: auto minmax(0, 1fr) auto", styles)\n'
new = '''        self.assertIn("grid-template-columns: minmax(0, 1fr) auto", styles)
        self.assertIn('.mie-finding-head:has(> input[type="checkbox"])', styles)
        self.assertIn("grid-template-columns: auto minmax(0, 1fr) auto", styles)
'''
if old not in text:
    raise SystemExit("finding grid contract marker not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Finding header grid corrected")

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ui_path = ROOT / "tests/test_workspace_ui.py"
ui = ui_path.read_text(encoding="utf-8")
old = '''    def test_library_inspector_preserves_full_detail_navigation(self):
        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")
        self.assertIn("workspace-inspector", script)
        self.assertIn("Open full details", script)
        self.assertIn("dblclick", script)
        self.assertIn('event.key === "Escape"', script)
        self.assertIn('event.key === "Enter"', script)
'''
new = '''    def test_library_inspector_preserves_full_detail_navigation(self):
        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")
        partial = (ROOT / "app" / "templates" / "_workspace_inspector.html").read_text(encoding="utf-8")
        self.assertIn("workspace-inspector", script)
        self.assertIn("Open full details", partial)
        self.assertIn("dblclick", script)
        self.assertIn('event.key === "Escape"', script)
        self.assertIn('event.key === "Enter"', script)
'''
if old not in ui:
    raise RuntimeError("W1 Inspector navigation test anchor not found")
ui_path.write_text(ui.replace(old, new, 1), encoding="utf-8")

functional_path = ROOT / "tests/test_workspace_inspector.py"
functional = functional_path.read_text(encoding="utf-8")
if "from html import unescape\n" not in functional:
    functional = functional.replace("from dataclasses import replace\n", "from dataclasses import replace\nfrom html import unescape\n", 1)
functional = functional.replace('            "Inspector Film", "Health &amp; attention", "Quality deserves review",\n', '            "Inspector Film", "Health & attention", "Quality deserves review",\n', 1)
old_assert = "            self.assertIn(expected, response.text)\n"
new_assert = "            self.assertIn(expected, unescape(response.text))\n"
if old_assert not in functional:
    raise RuntimeError("Inspector HTML assertion anchor not found")
functional_path.write_text(functional.replace(old_assert, new_assert, 1), encoding="utf-8")

print("W2 validation expectations updated")

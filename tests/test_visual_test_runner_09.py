import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class VisualTestRunner09Tests(unittest.TestCase):
    def test_visual_runner_is_valid_python(self):
        source = (ROOT / "e2e" / "run_visual.py").read_text(encoding="utf-8")
        ast.parse(source, filename="e2e/run_visual.py")

    def test_playwright_visual_commands_remain_available(self):
        package = json.loads((ROOT / "e2e" / "package.json").read_text(encoding="utf-8"))
        scripts = package["scripts"]
        self.assertEqual(scripts["test:watch"], "python run_visual.py watch")
        self.assertEqual(scripts["test:ui"], "python run_visual.py ui")
        self.assertEqual(scripts["test:debug"], "python run_visual.py debug")
        self.assertEqual(scripts["test:local"], "python run_visual.py headless")

    def test_canonical_09_branch_is_ci_qualified(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("- testing/0.9-alpha", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("- testing/0.9-alpha-reseed", workflow)


if __name__ == "__main__":
    unittest.main()

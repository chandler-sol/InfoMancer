import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class VisualTestRunner09Tests(unittest.TestCase):
    def test_visual_runners_are_valid_python(self):
        for relative in ("e2e/run_visual.py", "e2e/run_full.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            ast.parse(source, filename=relative)

    def test_playwright_visual_commands_remain_available(self):
        package = json.loads((ROOT / "e2e" / "package.json").read_text(encoding="utf-8"))
        scripts = package["scripts"]
        self.assertEqual(scripts["test:watch"], "python run_visual.py watch")
        self.assertEqual(scripts["test:ui"], "python run_visual.py ui")
        self.assertEqual(scripts["test:debug"], "python run_visual.py debug")
        self.assertEqual(scripts["test:local"], "python run_visual.py headless")

    def test_smoke_deep_and_full_tiers_remain_available(self):
        package = json.loads((ROOT / "e2e" / "package.json").read_text(encoding="utf-8"))
        scripts = package["scripts"]
        self.assertEqual(scripts["test:smoke"], "python run_visual.py watch tests/smoke.spec.js")
        self.assertEqual(
            scripts["test:deep"],
            "python run_visual.py watch tests/smoke.spec.js tests/deep.spec.js",
        )
        self.assertEqual(scripts["test:full"], "python run_full.py watch")
        self.assertTrue((ROOT / "e2e" / "tests" / "smoke.spec.js").is_file())
        self.assertTrue((ROOT / "e2e" / "tests" / "deep.spec.js").is_file())

    def test_canonical_09_branch_is_ci_qualified(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("- testing/0.9-alpha", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("- testing/0.9-alpha-reseed", workflow)
        self.assertIn("run: npm test", workflow)
        self.assertIn("e2e/.e2e-runtime/logs", workflow)


if __name__ == "__main__":
    unittest.main()

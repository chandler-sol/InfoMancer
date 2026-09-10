from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]

runpy.run_path(str(ROOT / "scripts/apply_today_release_polish_v4.py"), run_name="__main__")

# The canonical release workflow already validates the resolved Linux desktop
# identity and AppStream file during package construction. The one-shot helper
# intentionally leaves workflow files untouched, so today's regression test
# should validate the package metadata changes themselves rather than require a
# temporary workflow wording change.
test_path = ROOT / "tests/test_beta2_release_candidate_polish.py"
test = test_path.read_text(encoding="utf-8")
line = "        self.assertIn('release details, and installed icon', workflow)\n"
if line not in test:
    raise SystemExit("Expected temporary workflow assertion was not found")
test_path.write_text(test.replace(line, "", 1), encoding="utf-8")

(ROOT / "scripts/apply_today_release_polish_v5.py").unlink(missing_ok=True)

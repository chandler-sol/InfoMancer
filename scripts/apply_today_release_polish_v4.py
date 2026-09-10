from pathlib import Path
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[1]

# Apply and validate the final release-candidate transformation.
runpy.run_path(str(ROOT / "scripts/apply_today_release_polish_v3.py"), run_name="__main__")

# The GitHub Actions token used by this one-shot helper intentionally does not have
# workflow-write permission. Leave workflow files unchanged in the bot commit; the
# authenticated repository connector updates/removes them immediately afterward.
for relative in (
    ".github/workflows/draft-08-release.yml",
    ".github/workflows/apply-today-release-polish.yml",
):
    content = subprocess.check_output(
        ["git", "show", f"HEAD:{relative}"], cwd=ROOT, text=True
    )
    target = ROOT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

(ROOT / "scripts/apply_today_release_polish_v4.py").unlink(missing_ok=True)

from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
metainfo = ROOT / "desktop/src-tauri/linux/cloud.arsenik.infomancer.metainfo.xml"
text = metainfo.read_text(encoding="utf-8")
multiline = '''  <releases>
    <release version="0.8.1-beta.2" date="2026-09-09">
      <description>
        <p>Current cross-platform beta testing build.</p>
      </description>
    </release>
  </releases>'''
compact = '''  <releases>
    <release version="0.8.1-beta.2" date="2026-09-09">
      <description><p>Current cross-platform beta testing build.</p></description>
    </release>
  </releases>'''
if multiline not in text:
    raise SystemExit("Expected current AppStream release block was not found")
metainfo.write_text(text.replace(multiline, compact, 1), encoding="utf-8")

runpy.run_path(str(ROOT / "scripts/apply_today_release_polish.py"), run_name="__main__")
(ROOT / "scripts/apply_today_release_polish_v2.py").unlink(missing_ok=True)

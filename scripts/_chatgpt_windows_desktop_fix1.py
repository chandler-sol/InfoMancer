from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

hooks_path = ROOT / "desktop/src-tauri/windows/hooks.nsh"
hooks = hooks_path.read_text(encoding="utf-8")
old = '    MessageBox MB_ICONQUESTION|MB_YESNOCANCEL "Before InfoMancer removes its local data, would you like to create a verified recovery backup? Your media files are not included or modified." IDYES infomancer_backup_choose IDNO infomancer_preuninstall_done IDCANCEL infomancer_uninstall_cancel\n'
new = '    MessageBox MB_ICONQUESTION|MB_YESNOCANCEL "Before InfoMancer removes its local data, would you like to create a verified recovery backup? Your media files are not included or modified." IDYES infomancer_backup_choose IDNO infomancer_preuninstall_done\n    Goto infomancer_uninstall_cancel\n'
if hooks.count(old) != 1:
    raise RuntimeError("Expected one three-way recovery MessageBox")
hooks_path.write_text(hooks.replace(old, new, 1), encoding="utf-8")

release_path = ROOT / ".github/workflows/windows-desktop-release.yml"
release = release_path.read_text(encoding="utf-8")
old = '''          updaterJsonPreferNsis: true
          uploadUpdaterJson: true
          uploadUpdaterSignatures: true
'''
new = '''          updaterJsonPreferNsis: true
          includeUpdaterJson: true
'''
if release.count(old) != 1:
    raise RuntimeError("Expected one updater JSON option block")
release_path.write_text(release.replace(old, new, 1), encoding="utf-8")

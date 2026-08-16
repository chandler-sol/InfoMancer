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

# Build-generated Tauri schemas and icon renditions are recreated in CI/local builds
# from source configuration and desktop/app-icon.svg. Keep them out of the source
# branch along with the bundled sidecar binary and Cargo target directory.
desktop_ignore = ROOT / "desktop/.gitignore"
ignore = desktop_ignore.read_text(encoding="utf-8")
for entry in ("src-tauri/gen/", "src-tauri/icons/"):
    if entry not in ignore.splitlines():
        ignore += entry + "\n"
desktop_ignore.write_text(ignore, encoding="utf-8")

# PyInstaller creates these at the repository root during desktop builds.
root_ignore = ROOT / ".gitignore"
ignore = root_ignore.read_text(encoding="utf-8")
for entry in ("build/", "*.spec"):
    if entry not in ignore.splitlines():
        ignore += entry + "\n"
root_ignore.write_text(ignore, encoding="utf-8")

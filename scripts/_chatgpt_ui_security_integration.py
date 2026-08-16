from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_sources() -> None:
    path = "app/templates/sources.html"
    text = read(path)
    text = replace_once(
        text,
        '<button class="source-trash-button" type="submit" title="Remove source"',
        '<button class="button source-trash-button" type="submit" title="Remove source"',
        "source trash button shell",
    )
    write(path, text)


def patch_workspace_js() -> None:
    path = "app/static/workspace.js"
    text = read(path)
    anchor = '''    document.addEventListener("click", (event) => {\n      const item = event.target.closest(".library-title-row, .cover-card");\n'''
    replacement = '''    // Prevent the browser's native Shift+mousedown text-range selection from\n    // painting over cover titles before the click handler selects media cards.\n    document.addEventListener("mousedown", (event) => {\n      const item = event.target.closest(".library-title-row, .cover-card");\n      if (item && event.shiftKey && !interactive(event.target)) event.preventDefault();\n    });\n\n    document.addEventListener("click", (event) => {\n      const item = event.target.closest(".library-title-row, .cover-card");\n'''
    text = replace_once(text, anchor, replacement, "shift selection guard")
    write(path, text)


def patch_settings() -> None:
    path = "app/templates/settings.html"
    text = read(path)

    text = replace_once(
        text,
        '<a href="#storage">Storage</a><a href="#safety">Safety</a><a href="#fingerprints">Fingerprints</a><a href="#backups">Backups</a>',
        '<a href="#storage">Storage</a><a href="#fingerprints">Fingerprints</a><a href="#safety">Safety</a><a href="#backups">Backups</a>',
        "system jump navigation order",
    )

    safety_block = '''  <section class="panel settings-card system-safety-card" id="safety">\n    <div class="settings-card-head"><div><p class="eyebrow">SAFETY</p><h2>File protection mode</h2></div><span class="settings-state {{ 'warn' if preferences.lockdown_mode == '1' else 'good' }}">{{ 'Lockdown' if preferences.lockdown_mode == '1' else 'Standard' }}</span></div>\n    <p class="muted">Standard Mode uses clear confirmation dialogs for destructive actions. Lockdown Mode adds typed confirmation to irreversible file decisions and pauses automatic managed-trash deletion.</p>\n    <form class="settings-form safety-mode-form" method="post" action="/settings/safety">\n      <label class="safety-mode-choice"><input type="radio" name="lockdown_mode" value="0" {% if preferences.lockdown_mode != '1' %}checked{% endif %}><span><strong>Standard Mode</strong><small>Confirm destructive actions with a normal warning dialog.</small></span></label>\n      <label class="safety-mode-choice"><input type="radio" name="lockdown_mode" value="1" {% if preferences.lockdown_mode == '1' %}checked{% endif %}><span><strong>Lockdown Mode</strong><small>Require additional typed confirmation for irreversible file deletion. Automatic managed-trash purging is paused.</small></span></label>\n      <button class="button primary">Save safety mode</button>\n    </form>\n  </section>\n'''
    if text.count(safety_block) != 1:
        raise RuntimeError("safety block: expected exactly one match")
    text = text.replace(safety_block, "", 1)

    text = replace_once(
        text,
        '<form class="settings-form hashing-settings-form" method="post" action="/settings/hashing">',
        '<form class="settings-form hashing-settings-form" id="hashing-settings-form" method="post" action="/settings/hashing">',
        "hashing form id",
    )
    text = replace_once(
        text,
        '<label>Start time<input type="time" name="hash_schedule_time" value="{{ preferences.hash_schedule_time }}" required><small>Uses the installation time zone. The default is Sunday at 3:00 AM.</small></label>',
        '<label class="hash-time-label">Start time<input type="time" name="hash_schedule_time" value="{{ preferences.hash_schedule_time }}" required><small>Uses the installation time zone. The default is Sunday at 3:00 AM.</small></label>',
        "hash time label",
    )
    old_actions = '''      <label class="checkbox-label"><input type="checkbox" name="hash_pause_for_activity" value="1" {% if preferences.hash_pause_for_activity == '1' %}checked{% endif %}> Pause fingerprinting while scans, matching, or media inspection are running</label>\n      <div class="actions"><button class="button primary">Save fingerprint settings</button></div>\n    </form>\n    <div class="actions hashing-task-actions"><form method="post" action="/hashes/run"><button class="button">Run now</button></form><form method="post" action="/hashes/pause"><button class="button">Pause</button></form><form method="post" action="/hashes/resume"><button class="button">Resume</button></form><form method="post" action="/hashes/cancel"><button class="button danger">Cancel</button></form></div>\n  </section>\n'''
    new_actions = '''      <label class="checkbox-label"><input type="checkbox" name="hash_pause_for_activity" value="1" {% if preferences.hash_pause_for_activity == '1' %}checked{% endif %}> Pause fingerprinting while scans, matching, or media inspection are running</label>\n    </form>\n    <div class="actions hashing-command-bar"><button class="button primary" type="submit" form="hashing-settings-form">Save fingerprint settings</button><form method="post" action="/hashes/run"><button class="button">Run now</button></form><form method="post" action="/hashes/pause"><button class="button">Pause</button></form><form method="post" action="/hashes/resume"><button class="button">Resume</button></form><form method="post" action="/hashes/cancel"><button class="button danger">Cancel</button></form></div>\n  </section>\n'''
    text = replace_once(text, old_actions, new_actions, "fingerprint action rail")

    backup_marker = '  <section class="panel settings-card full-width" id="backups">\n'
    safety_full = safety_block.replace(
        'class="panel settings-card system-safety-card"',
        'class="panel settings-card system-safety-card full-width"',
    )
    text = replace_once(text, backup_marker, safety_full + backup_marker, "move safety before backups")
    text = replace_once(
        text,
        '<section class="panel settings-card full-width" id="backups">',
        '<section class="panel settings-card full-width backup-protection-card" id="backups">',
        "backup card class",
    )
    text = replace_once(
        text,
        '<div class="button-row"><form method="post" action="/maintenance/backups/verify">',
        '<div class="button-row backup-header-actions"><form method="post" action="/maintenance/backups/verify">',
        "backup header actions class",
    )
    text = replace_once(
        text,
        '<form class="settings-restore-upload" method="post" action="/maintenance/restore/upload"',
        '<form class="settings-restore-upload backup-upload-row" method="post" action="/maintenance/restore/upload"',
        "backup upload class",
    )
    write(path, text)


def patch_workspace_css() -> None:
    path = "app/static/workspace.css"
    text = read(path)
    old_source = '''.source-remove-form { display: inline-flex; margin: 0; }\n.source-trash-button {\n  display: inline-grid; place-items: center; width: 38px; height: 38px;\n  border: 1px solid var(--border); border-radius: 8px; background: transparent;\n  color: var(--muted); cursor: pointer;\n}\n.source-trash-button svg { width: 17px; height: 17px; fill: currentColor; }\n.source-trash-button:hover,\n.source-trash-button:focus-visible { color: #ff6b6b; border-color: rgba(255, 107, 107, .65); background: rgba(255, 107, 107, .08); }\n'''
    new_source = '''.source-remove-form { display: inline-flex; margin: 0; }\n.source-trash-button.button {\n  display: inline-grid;\n  flex: 0 0 40px;\n  width: 40px;\n  min-width: 40px;\n  height: 38px;\n  place-items: center;\n  padding: 0;\n  border-color: #3a4857;\n  color: var(--muted);\n  background: rgba(255, 255, 255, .018);\n}\n.source-trash-button svg { width: 19px; height: 19px; fill: currentColor; }\n.source-trash-button:hover,\n.source-trash-button:focus-visible,\n.source-trash-button:active {\n  color: #ff6b6b;\n  border-color: rgba(255, 107, 107, .65);\n  background: rgba(255, 107, 107, .08);\n}\n'''
    text = replace_once(text, old_source, new_source, "source trash styling")

    polish = r'''

/* Screenshot polish: selection, safety, fingerprint controls, and backup rhythm. */
.cover-card[data-workspace-title-id] {
  -webkit-user-select: none;
  user-select: none;
}

.system-safety-card.full-width {
  display: grid;
  gap: 16px;
}
.system-safety-card.full-width > p.muted { margin: 0; max-width: 980px; }
.system-safety-card.full-width .safety-mode-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr)) auto;
  align-items: stretch;
  gap: 10px;
}
.system-safety-card.full-width .safety-mode-choice {
  min-height: 74px;
  margin: 0;
  background: rgba(255, 255, 255, .018);
}
.system-safety-card.full-width .safety-mode-form > .button {
  align-self: stretch;
  min-width: 154px;
  white-space: nowrap;
}

#fingerprints {
  display: grid;
  gap: 16px;
}
#fingerprints > p.muted,
#fingerprints .hashing-settings-form,
#fingerprints .hashing-command-bar { margin-top: 0; margin-bottom: 0; }
.hashing-settings-form { gap: 14px; }
.hashing-settings-form .settings-form-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px 16px;
}
.hashing-settings-form .settings-form-grid > label {
  display: grid;
  align-content: start;
  gap: 6px;
  min-width: 0;
  margin: 0;
}
.hashing-settings-form .settings-form-grid input,
.hashing-settings-form .settings-form-grid select {
  width: 100%;
  min-height: 44px;
  box-sizing: border-box;
}
.hashing-settings-form .settings-form-grid small {
  display: block;
  margin: 0;
  line-height: 1.4;
}
.hashing-settings-form .hash-time-label input[type="time"] {
  max-width: 220px;
  color-scheme: dark;
  font-variant-numeric: tabular-nums;
}
.hashing-settings-form > .checkbox-label {
  margin: 0;
  padding-top: 2px;
}
.hashing-command-bar {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 8px;
}
.hashing-command-bar > form { margin: 0; }
.hashing-command-bar .button { white-space: nowrap; }

.backup-protection-card {
  display: grid;
  gap: 16px;
}
.backup-protection-card > .settings-card-head {
  align-items: flex-start;
  margin: 0;
}
.backup-protection-card > p.muted { margin: 0; max-width: 1120px; }
.backup-header-actions {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.backup-header-actions form { margin: 0; }
.backup-protection-card .settings-history { margin: 2px 0 0; }
.backup-protection-card .settings-history table { margin: 0; }
.backup-protection-card .settings-history th,
.backup-protection-card .settings-history td { vertical-align: middle; }
.backup-protection-card .settings-history td:last-child { white-space: nowrap; }
.backup-protection-card .settings-history td:last-child .inline-form { margin: 0 0 0 6px; }
.backup-upload-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: 12px;
  margin: 0;
  padding-top: 18px;
  border-top: 1px solid var(--line);
}
.backup-upload-row label {
  display: grid;
  gap: 8px;
  min-width: 0;
  margin: 0;
}
.backup-upload-row input[type="file"] {
  width: 100%;
  min-height: 44px;
  box-sizing: border-box;
  padding: 8px 10px;
}
.backup-upload-row > .button {
  min-height: 44px;
  white-space: nowrap;
}

@media (max-width: 1180px) {
  .hashing-settings-form .settings-form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .system-safety-card.full-width .safety-mode-form { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .system-safety-card.full-width .safety-mode-form > .button { grid-column: 1 / -1; justify-self: start; min-height: 42px; }
}

@media (max-width: 720px) {
  .hashing-settings-form .settings-form-grid { grid-template-columns: 1fr; }
  .hashing-settings-form .hash-time-label input[type="time"] { max-width: none; }
  .hashing-command-bar { flex-wrap: wrap; }
  .system-safety-card.full-width .safety-mode-form { grid-template-columns: 1fr; }
  .system-safety-card.full-width .safety-mode-form > .button { grid-column: auto; width: 100%; }
  .backup-header-actions { justify-content: flex-start; }
  .backup-upload-row { grid-template-columns: 1fr; align-items: stretch; }
  .backup-upload-row > .button { width: 100%; }
}
'''
    if "/* Screenshot polish: selection, safety, fingerprint controls, and backup rhythm. */" in text:
        raise RuntimeError("workspace polish styles already present")
    text += polish
    write(path, text)


def patch_main_security() -> None:
    path = "app/main.py"
    text = read(path)
    text = replace_once(
        text,
        'app = FastAPI(title="InfoMancer", version=APP_VERSION)\n',
        'app = FastAPI(\n    title="InfoMancer", version=APP_VERSION,\n    docs_url=None, redoc_url=None, openapi_url=None,\n)\n',
        "disable FastAPI generated docs",
    )

    event_anchor = '''    event_log.write(\n        category, message, level=stored_level, detail=detail,\n        context=context, user_id=user_id,\n    )\n\n\nbackground = BackgroundCoordinator(\n'''
    event_replacement = '''    event_log.write(\n        category, message, level=stored_level, detail=detail,\n        context=context, user_id=user_id,\n    )\n\n\ndef _primary_librarian_id() -> int | None:\n    """Return the first active Librarian for targeted security notifications."""\n    try:\n        with db.connect() as conn:\n            row = conn.execute(\n                """SELECT id FROM users\n                   WHERE role='librarian' AND active=1\n                   ORDER BY id LIMIT 1"""\n            ).fetchone()\n        return int(row["id"]) if row else None\n    except sqlite3.Error:\n        return None\n\n\ndef record_security_event(\n    message: str, *, level: str = "info", detail: str = "",\n    context: dict | None = None, user_id: int | None = None,\n    notify_librarian: bool = False,\n) -> None:\n    """Audit a security event and optionally surface it to the primary Librarian."""\n    security_context = dict(context or {})\n    security_context.setdefault("category", "authentication")\n    record_event(\n        "authentication", message, level=level, detail=detail,\n        context=security_context, user_id=user_id,\n    )\n    if not notify_librarian:\n        return\n    librarian_id = _primary_librarian_id()\n    if librarian_id is None:\n        return\n    record_event(\n        "library", message, level=level, detail=detail,\n        context=security_context, user_id=librarian_id,\n    )\n\n\nbackground = BackgroundCoordinator(\n'''
    text = replace_once(text, event_anchor, event_replacement, "security event helpers")

    old_login = '''    try:\n        user = auth_service.authenticate_local(identity, password, request_ip(request, settings))\n    except AuthenticationError as exc:\n        return preauth_response(request, "login.html", {\n            "next": safe_next(next), "identity": identity, "error": str(exc),\n        })\n    return signed_in_response(request, user, next)\n'''
    new_login = '''    client_ip = request_ip(request, settings)\n    try:\n        user = auth_service.authenticate_local(identity, password, client_ip)\n    except LoginLocked as exc:\n        if exc.new_lockout:\n            locked_user = auth_service.get_user(exc.user_id) if exc.user_id else None\n            subject = locked_user.display_name if locked_user else "an account"\n            record_security_event(\n                f"Repeated sign-in attempts were blocked for {subject}.",\n                level="warning",\n                detail=(\n                    f"Temporary lock scope: {exc.scope or 'existing'}. "\n                    f"Source IP: {client_ip or 'unknown'}."\n                ),\n                context={\n                    "operation": "login_lockout", "scope": exc.scope,\n                    "ip_address": client_ip,\n                },\n                user_id=exc.user_id, notify_librarian=True,\n            )\n        return preauth_response(request, "login.html", {\n            "next": safe_next(next), "identity": identity, "error": str(exc),\n        })\n    except AuthenticationError as exc:\n        return preauth_response(request, "login.html", {\n            "next": safe_next(next), "identity": identity, "error": str(exc),\n        })\n    record_security_event(\n        "Local account signed in.",\n        context={"operation": "login_success", "ip_address": client_ip},\n        user_id=user.id,\n    )\n    return signed_in_response(request, user, next)\n'''
    text = replace_once(text, old_login, new_login, "local login audit")

    revoke_anchor = '''        auth_service.revoke_user_sessions(\n            request.state.user.id, except_session=request.state.auth_session.id\n        )\n'''
    revoke_replacement = revoke_anchor + '''        record_security_event(\n            "Account password was changed and other sessions were revoked.",\n            context={"operation": "password_changed"},\n            user_id=request.state.user.id,\n        )\n'''
    text = replace_once(text, revoke_anchor, revoke_replacement, "password change audit")
    write(path, text)


def add_ui_contract_tests() -> None:
    path = ROOT / "tests" / "test_ui_polish.py"
    if path.exists():
        raise RuntimeError("tests/test_ui_polish.py already exists")
    path.write_text('''import unittest\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parent.parent\n\n\nclass UiPolishContractTests(unittest.TestCase):\n    def test_source_remove_uses_normal_button_shell(self):\n        template = (ROOT / "app" / "templates" / "sources.html").read_text(encoding="utf-8")\n        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")\n        self.assertIn('class="button source-trash-button"', template)\n        self.assertIn(".source-trash-button.button", styles)\n        self.assertIn("width: 19px; height: 19px", styles)\n\n    def test_shift_range_selection_suppresses_native_text_selection(self):\n        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")\n        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")\n        self.assertIn('document.addEventListener("mousedown"', script)\n        self.assertIn("event.shiftKey", script)\n        self.assertIn("event.preventDefault()", script)\n        self.assertIn(".cover-card[data-workspace-title-id]", styles)\n        self.assertIn("user-select: none", styles)\n\n    def test_system_safety_fingerprints_and_backups_have_polish_hooks(self):\n        template = (ROOT / "app" / "templates" / "settings.html").read_text(encoding="utf-8")\n        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")\n        self.assertLess(template.index('id="fingerprints"'), template.index('id="safety"'))\n        self.assertLess(template.index('id="safety"'), template.index('id="backups"'))\n        self.assertIn('id="hashing-settings-form"', template)\n        self.assertIn("hashing-command-bar", template)\n        self.assertIn("hash-time-label", template)\n        self.assertIn("backup-protection-card", template)\n        self.assertIn("backup-header-actions", template)\n        self.assertIn("backup-upload-row", template)\n        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", styles)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")


def main() -> None:
    patch_sources()
    patch_workspace_js()
    patch_settings()
    patch_workspace_css()
    patch_main_security()
    add_ui_contract_tests()


if __name__ == "__main__":
    main()

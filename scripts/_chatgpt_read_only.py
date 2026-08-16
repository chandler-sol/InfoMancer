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


def add_file_protection_service() -> None:
    path = ROOT / "app" / "file_protection.py"
    if path.exists():
        raise RuntimeError("app/file_protection.py already exists")
    path.write_text('''from __future__ import annotations\n\n\nclass MediaWriteBlocked(RuntimeError):\n    pass\n\n\nclass FileProtectionService:\n    """One server-side gate for operations that mutate user media files."""\n\n    def __init__(self, app_settings) -> None:\n        self.app_settings = app_settings\n\n    @property\n    def mode(self) -> str:\n        return self.app_settings.file_protection_mode()\n\n    @property\n    def media_writes_allowed(self) -> bool:\n        return self.mode != "readonly"\n\n    @property\n    def automatic_permanent_delete_allowed(self) -> bool:\n        return self.mode == "standard"\n\n    def require_media_write(self, action: str = "change media files") -> None:\n        if self.media_writes_allowed:\n            return\n        raise MediaWriteBlocked(\n            f"Read-Only Mode is active. InfoMancer can scan, inspect, match, and review media, "\n            f"but it will not {action}. Switch File Protection Mode to Standard or Lockdown "\n            "before making filesystem changes."\n        )\n''', encoding="utf-8")


def patch_app_settings() -> None:
    path = "app/app_settings.py"
    text = read(path)
    text = replace_once(
        text,
        '        "lockdown_mode",\n',
        '        "lockdown_mode",\n        "read_only_mode",\n',
        "read-only editable key",
    )
    text = replace_once(
        text,
        '            "lockdown_mode": "0",\n',
        '            "lockdown_mode": "0",\n            "read_only_mode": "0",\n',
        "read-only default",
    )
    old_safety = '''    def validate_safety(self, lockdown_mode: str) -> dict[str, str]:\n        mode = lockdown_mode.strip().casefold()\n        if mode not in {"0", "1", "standard", "lockdown"}:\n            raise AppSettingError("Choose Standard Mode or Lockdown Mode.")\n        return {"lockdown_mode": "1" if mode in {"1", "lockdown"} else "0"}\n\n'''
    new_safety = '''    def file_protection_mode(self) -> str:\n        if self.get("read_only_mode") == "1":\n            return "readonly"\n        if self.get("lockdown_mode") == "1":\n            return "lockdown"\n        return "standard"\n\n    def validate_safety(self, protection_mode: str) -> dict[str, str]:\n        mode = protection_mode.strip().casefold().replace("-", "_")\n        aliases = {"0": "standard", "1": "lockdown", "read_only": "readonly"}\n        mode = aliases.get(mode, mode)\n        if mode not in {"readonly", "standard", "lockdown"}:\n            raise AppSettingError(\n                "Choose Read-Only Mode, Standard Mode, or Lockdown Mode."\n            )\n        return {\n            "read_only_mode": "1" if mode == "readonly" else "0",\n            "lockdown_mode": "1" if mode == "lockdown" else "0",\n        }\n\n'''
    text = replace_once(text, old_safety, new_safety, "three-state safety validation")
    old_import = '''        if "lockdown_mode" in text_values:\n            validated.update(self.validate_safety(text_values["lockdown_mode"]))\n'''
    new_import = '''        if {"lockdown_mode", "read_only_mode"}.intersection(text_values):\n            current = self.values()\n            read_only = text_values.get("read_only_mode", current["read_only_mode"]).strip()\n            lockdown = text_values.get("lockdown_mode", current["lockdown_mode"]).strip()\n            if read_only not in {"0", "1"} or lockdown not in {"0", "1"}:\n                raise AppSettingError(\n                    "Imported file-protection flags must be 0 or 1. No settings were changed."\n                )\n            mode = "readonly" if read_only == "1" else "lockdown" if lockdown == "1" else "standard"\n            validated.update(self.validate_safety(mode))\n'''
    text = replace_once(text, old_import, new_import, "read-only settings import")
    write(path, text)


def patch_settings_route() -> None:
    path = "app/routes/settings.py"
    text = read(path)
    old = '''    @librarian_post("/settings/safety")\n    def update_safety_mode(request: Request, lockdown_mode: str = Form("0")):\n        try:\n            values = app_settings.validate_safety(lockdown_mode)\n            changed = app_settings.update(values, request.state.user.id)\n        except AppSettingError as exc:\n            return render_settings(request, "system", str(exc), status_code=400)\n        mode = "Lockdown Mode" if values["lockdown_mode"] == "1" else "Standard Mode"\n        message = (\n            f"Safety mode changed to {mode}." if changed else f"{mode} is already active."\n        )\n        record_event(\n            "settings", message, context={"lockdown_mode": values["lockdown_mode"]},\n            user_id=request.state.user.id,\n        )\n        return redirect("/settings/system", message)\n'''
    new = '''    @librarian_post("/settings/safety")\n    def update_safety_mode(\n        request: Request, protection_mode: str = Form("standard"),\n    ):\n        try:\n            values = app_settings.validate_safety(protection_mode)\n            changed = app_settings.update(values, request.state.user.id)\n        except AppSettingError as exc:\n            return render_settings(request, "system", str(exc), status_code=400)\n        mode = app_settings.file_protection_mode()\n        label = {\n            "readonly": "Read-Only Mode", "standard": "Standard Mode",\n            "lockdown": "Lockdown Mode",\n        }[mode]\n        message = (\n            f"File protection changed to {label}."\n            if changed else f"{label} is already active."\n        )\n        record_event(\n            "settings", message, context={"file_protection_mode": mode},\n            user_id=request.state.user.id,\n        )\n        return redirect("/settings/system", message)\n'''
    text = replace_once(text, old, new, "read-only safety route")
    write(path, text)


def patch_settings_template() -> None:
    path = "app/templates/settings.html"
    text = read(path)
    old = '''  <section class="panel settings-card system-safety-card full-width" id="safety">\n    <div class="settings-card-head"><div><p class="eyebrow">SAFETY</p><h2>File protection mode</h2></div><span class="settings-state {{ 'warn' if preferences.lockdown_mode == '1' else 'good' }}">{{ 'Lockdown' if preferences.lockdown_mode == '1' else 'Standard' }}</span></div>\n    <p class="muted">Standard Mode uses clear confirmation dialogs for destructive actions. Lockdown Mode adds typed confirmation to irreversible file decisions and pauses automatic managed-trash deletion.</p>\n    <form class="settings-form safety-mode-form" method="post" action="/settings/safety">\n      <label class="safety-mode-choice"><input type="radio" name="lockdown_mode" value="0" {% if preferences.lockdown_mode != '1' %}checked{% endif %}><span><strong>Standard Mode</strong><small>Confirm destructive actions with a normal warning dialog.</small></span></label>\n      <label class="safety-mode-choice"><input type="radio" name="lockdown_mode" value="1" {% if preferences.lockdown_mode == '1' %}checked{% endif %}><span><strong>Lockdown Mode</strong><small>Require additional typed confirmation for irreversible file deletion. Automatic managed-trash purging is paused.</small></span></label>\n      <button class="button primary">Save safety mode</button>\n    </form>\n  </section>\n'''
    new = '''  <section class="panel settings-card system-safety-card full-width" id="safety">\n    {% set protection_mode = 'readonly' if preferences.read_only_mode == '1' else 'lockdown' if preferences.lockdown_mode == '1' else 'standard' %}\n    <div class="settings-card-head"><div><p class="eyebrow">SAFETY</p><h2>File protection mode</h2></div><span class="settings-state {{ 'active' if protection_mode == 'readonly' else 'warn' if protection_mode == 'lockdown' else 'good' }}">{{ 'Read-Only' if protection_mode == 'readonly' else 'Lockdown' if protection_mode == 'lockdown' else 'Standard' }}</span></div>\n    <p class="muted">Choose how much authority InfoMancer has over your actual media files. Catalog, metadata, analysis, matching, and organization remain available in every mode.</p>\n    <form class="settings-form safety-mode-form safety-mode-three" method="post" action="/settings/safety">\n      <label class="safety-mode-choice"><input type="radio" name="protection_mode" value="readonly" {% if protection_mode == 'readonly' %}checked{% endif %}><span><strong>Read-Only Mode</strong><small>Never rename, move, restore, or permanently delete media files. Scanning, inspection, matching, MIE, tags, collections, and backups keep working.</small></span></label>\n      <label class="safety-mode-choice"><input type="radio" name="protection_mode" value="standard" {% if protection_mode == 'standard' %}checked{% endif %}><span><strong>Standard Mode</strong><small>Allow reviewed filesystem changes with normal confirmation and managed-trash retention.</small></span></label>\n      <label class="safety-mode-choice"><input type="radio" name="protection_mode" value="lockdown" {% if protection_mode == 'lockdown' %}checked{% endif %}><span><strong>Lockdown Mode</strong><small>Allow reviewed reversible file changes, but add stronger confirmation for irreversible actions and pause automatic permanent managed-trash deletion.</small></span></label>\n      <button class="button primary">Save file protection mode</button>\n    </form>\n  </section>\n'''
    text = replace_once(text, old, new, "three-mode safety UI")
    write(path, text)


def patch_main_context() -> None:
    path = "app/main.py"
    text = read(path)
    anchor = '        "default_cover_size": int(preferences["default_cover_size"]),\n'
    replacement = anchor + '        "file_protection_mode": app_settings.file_protection_mode(),\n'
    text = replace_once(text, anchor, replacement, "global file protection context")
    write(path, text)


def patch_base_template() -> None:
    path = "app/templates/base.html"
    text = read(path)
    anchor = '''    {% if message %}<div class="notice{% if is_match_notice %} match-notice{% endif %}{% if is_account_notice %} account-notice{% endif %}" id="flash-message" role="status"><span>{{ message }}</span>{% if is_match_notice %}<a href="{{ match_return }}">{{ request.query_params.get('return_label', 'Back to search results') }}</a>{% endif %}</div>{% endif %}\n    {% block content %}{% endblock %}\n'''
    replacement = '''    {% if message %}<div class="notice{% if is_match_notice %} match-notice{% endif %}{% if is_account_notice %} account-notice{% endif %}" id="flash-message" role="status"><span>{{ message }}</span>{% if is_match_notice %}<a href="{{ match_return }}">{{ request.query_params.get('return_label', 'Back to search results') }}</a>{% endif %}</div>{% endif %}\n    {% if file_protection_mode == 'readonly' and current_user and current_user.is_librarian %}<div class="read-only-mode-banner" role="status"><strong>Read-Only Mode</strong><span>Media filesystem changes are blocked. Scanning, matching, inspection, analysis, and catalog organization remain available.</span><a href="/settings/system#safety">Change mode</a></div>{% endif %}\n    {% block content %}{% endblock %}\n'''
    text = replace_once(text, anchor, replacement, "read-only global banner")
    write(path, text)


def patch_background() -> None:
    path = "app/background.py"
    text = read(path)
    old = '''        if self.app_settings.get("lockdown_mode") == "1":\n            with self.trash_cleanup_lock:\n                self.trash_cleanup_job.clear()\n                self.trash_cleanup_job.update({\n                    "status": "paused",\n                    "detail": "Lockdown Mode is preventing permanent managed-trash deletion",\n                })\n            return\n'''
    new = '''        protection_mode = self.app_settings.file_protection_mode()\n        if protection_mode in {"readonly", "lockdown"}:\n            label = "Read-Only Mode" if protection_mode == "readonly" else "Lockdown Mode"\n            with self.trash_cleanup_lock:\n                self.trash_cleanup_job.clear()\n                self.trash_cleanup_job.update({\n                    "status": "paused",\n                    "detail": f"{label} is preventing permanent managed-trash deletion",\n                })\n            return\n'''
    text = replace_once(text, old, new, "read-only automatic trash cleanup")
    write(path, text)


def patch_title_routes() -> None:
    path = "app/routes/titles.py"
    text = read(path)
    text = replace_once(
        text,
        'from ..access import require_librarian\n',
        'from ..access import require_librarian\nfrom ..file_protection import FileProtectionService, MediaWriteBlocked\n',
        "title file protection import",
    )
    text = replace_once(
        text,
        '    operation_history = OperationHistoryService(db)\n',
        '    operation_history = OperationHistoryService(db)\n    file_protection = FileProtectionService(app_settings)\n',
        "title file protection service",
    )

    guards = [
        (
            '    def rename_folder(request: Request, title_id: int, confirm: str = Form("")):\n',
            '    def rename_folder(request: Request, title_id: int, confirm: str = Form("")):\n        try:\n            file_protection.require_media_write("rename show folders")\n        except MediaWriteBlocked as exc:\n            return redirect(f"/titles/{title_id}", str(exc))\n',
            "show folder rename guard",
        ),
        (
            '''    def bulk_rename_apply(\n        request: Request, title_id: int, selected_file_ids: list[int] = Form(default=[]),\n    ):\n''',
            '''    def bulk_rename_apply(\n        request: Request, title_id: int, selected_file_ids: list[int] = Form(default=[]),\n    ):\n        try:\n            file_protection.require_media_write("rename episode files")\n        except MediaWriteBlocked as exc:\n            return redirect(f"/titles/{title_id}", str(exc))\n''',
            "bulk episode rename guard",
        ),
        (
            '    def restore_filenames_apply(request: Request, title_id: int):\n',
            '    def restore_filenames_apply(request: Request, title_id: int):\n        try:\n            file_protection.require_media_write("restore original media filenames")\n        except MediaWriteBlocked as exc:\n            return redirect(f"/titles/{title_id}", str(exc))\n',
            "restore filenames guard",
        ),
        (
            '    def rename_file(request: Request, file_id: int):\n',
            '    def rename_file(request: Request, file_id: int):\n        try:\n            file_protection.require_media_write("rename episode files")\n        except MediaWriteBlocked as exc:\n            return redirect("/library", str(exc))\n',
            "single episode rename guard",
        ),
        (
            '    def rename_movie(request: Request, file_id: int):\n',
            '    def rename_movie(request: Request, file_id: int):\n        try:\n            file_protection.require_media_write("rename movie files")\n        except MediaWriteBlocked as exc:\n            return redirect("/library", str(exc))\n',
            "movie rename guard",
        ),
    ]
    for old, new, label in guards:
        text = replace_once(text, old, new, label)
    write(path, text)


def patch_review_routes() -> None:
    path = "app/routes/review.py"
    text = read(path)
    text = replace_once(
        text,
        'from ..access import require_librarian\n',
        'from ..access import require_librarian\nfrom ..file_protection import FileProtectionService, MediaWriteBlocked\n',
        "review file protection import",
    )
    text = replace_once(
        text,
        '    operation_history = OperationHistoryService(db)\n',
        '    operation_history = OperationHistoryService(db)\n    file_protection = FileProtectionService(app_settings)\n',
        "review file protection service",
    )
    text = replace_once(
        text,
        '    def move_duplicate_to_trash(request: Request, file_id: int):\n        try:\n            trash_id = duplicate_trash.move(file_id, trash_retention_days(), request.state.user.id)\n',
        '    def move_duplicate_to_trash(request: Request, file_id: int):\n        try:\n            file_protection.require_media_write("move media files into managed Trash")\n            trash_id = duplicate_trash.move(file_id, trash_retention_days(), request.state.user.id)\n',
        "managed trash move guard",
    )
    text = replace_once(
        text,
        '        except (DuplicateTrashError, OSError, sqlite3.Error) as exc:\n            return redirect(\n                f"/duplicates/{file_id}/trash-preview",\n',
        '        except MediaWriteBlocked as exc:\n            return redirect(f"/duplicates/{file_id}/trash-preview", str(exc))\n        except (DuplicateTrashError, OSError, sqlite3.Error) as exc:\n            return redirect(\n                f"/duplicates/{file_id}/trash-preview",\n',
        "managed trash move blocked response",
    )
    text = replace_once(
        text,
        '    def restore_duplicate_trash(request: Request, trash_id: int):\n        try:\n            path = duplicate_trash.restore(trash_id)\n',
        '    def restore_duplicate_trash(request: Request, trash_id: int):\n        try:\n            file_protection.require_media_write("restore media files from managed Trash")\n            path = duplicate_trash.restore(trash_id)\n',
        "managed trash restore guard",
    )
    text = replace_once(
        text,
        '        except (DuplicateTrashError, OSError, sqlite3.Error) as exc:\n            return redirect(\n                "/duplicates/trash",\n                str(exc) if isinstance(exc, DuplicateTrashError) else\n',
        '        except MediaWriteBlocked as exc:\n            return redirect("/duplicates/trash", str(exc))\n        except (DuplicateTrashError, OSError, sqlite3.Error) as exc:\n            return redirect(\n                "/duplicates/trash",\n                str(exc) if isinstance(exc, DuplicateTrashError) else\n',
        "managed trash restore blocked response",
    )
    text = text.replace(
        '            "lockdown_mode": app_settings.get("lockdown_mode") == "1",\n',
        '            "lockdown_mode": app_settings.get("lockdown_mode") == "1",\n            "read_only_mode": app_settings.get("read_only_mode") == "1",\n',
    )
    write(path, text)


def patch_operation_routes() -> None:
    path = "app/routes/operations.py"
    text = read(path)
    text = replace_once(
        text,
        'from ..access import require_librarian\n',
        'from ..access import require_librarian\nfrom ..file_protection import FileProtectionService, MediaWriteBlocked\n',
        "operations file protection import",
    )
    text = replace_once(
        text,
        '    db = ctx.live("db")\n',
        '    app_settings = ctx.live("app_settings")\n    db = ctx.live("db")\n',
        "operations app settings",
    )
    text = replace_once(
        text,
        '    operation_history = OperationHistoryService(db)\n',
        '    operation_history = OperationHistoryService(db)\n    file_protection = FileProtectionService(app_settings)\n',
        "operations file protection service",
    )
    old = '''    def undo_operation(request: Request, operation_id: int):\n        try:\n            message = operation_history.undo(\n                operation_id, request.state.user.id, duplicate_trash=duplicate_trash,\n            )\n        except OperationHistoryError as exc:\n'''
    new = '''    def undo_operation(request: Request, operation_id: int):\n        try:\n            file_protection.require_media_write("undo filesystem operations")\n            message = operation_history.undo(\n                operation_id, request.state.user.id, duplicate_trash=duplicate_trash,\n            )\n        except MediaWriteBlocked as exc:\n            return redirect("/operations", str(exc))\n        except OperationHistoryError as exc:\n'''
    text = replace_once(text, old, new, "operation undo read-only guard")
    write(path, text)


def patch_operation_history_json_edge() -> None:
    path = "app/operation_history.py"
    text = read(path)
    old = '''        with self.database.connect() as conn:\n            row = conn.execute("SELECT * FROM operation_history WHERE id=?", (operation_id,)).fetchone()\n            if not row:\n                raise OperationHistoryError("That operation no longer exists.")\n            if row["status"] == "undone":\n                raise OperationHistoryError("That operation has already been undone.")\n            if row["status"] != "completed" or not row["undo_kind"]:\n                raise OperationHistoryError("That operation does not have a safe automatic undo.")\n            claimed = conn.execute(\n                "UPDATE operation_history SET status='undoing',undo_error='' WHERE id=? AND status='completed'",\n                (operation_id,),\n            )\n            if not claimed.rowcount:\n                raise OperationHistoryError("That operation is already being changed. Refresh and try again.")\n            payload = json.loads(row["undo_payload"] or "{}")\n            undo_kind = row["undo_kind"]\n'''
    new = '''        with self.database.connect() as conn:\n            row = conn.execute("SELECT * FROM operation_history WHERE id=?", (operation_id,)).fetchone()\n            if not row:\n                raise OperationHistoryError("That operation no longer exists.")\n            if row["status"] == "undone":\n                raise OperationHistoryError("That operation has already been undone.")\n            if row["status"] != "completed" or not row["undo_kind"]:\n                raise OperationHistoryError("That operation does not have a safe automatic undo.")\n            try:\n                payload = json.loads(row["undo_payload"] or "{}")\n            except (TypeError, json.JSONDecodeError) as exc:\n                raise OperationHistoryError(\n                    "Undo stopped because the recorded operation data is invalid. Nothing was changed."\n                ) from exc\n            if not isinstance(payload, dict):\n                raise OperationHistoryError(\n                    "Undo stopped because the recorded operation data is invalid. Nothing was changed."\n                )\n            claimed = conn.execute(\n                "UPDATE operation_history SET status='undoing',undo_error='' WHERE id=? AND status='completed'",\n                (operation_id,),\n            )\n            if not claimed.rowcount:\n                raise OperationHistoryError("That operation is already being changed. Refresh and try again.")\n            undo_kind = row["undo_kind"]\n'''
    text = replace_once(text, old, new, "malformed operation payload fail-closed")
    write(path, text)


def patch_trash_templates() -> None:
    path = "app/templates/duplicate_trash_preview.html"
    text = read(path)
    marker = '{% if lockdown_mode %}'
    if marker in text:
        text = text.replace(
            marker,
            '{% if read_only_mode %}<div class="form-error"><strong>Read-Only Mode</strong><br>This copy can be reviewed, but InfoMancer will not move it into managed Trash until Read-Only Mode is turned off.</div>{% endif %}\n' + marker,
            1,
        )
    write(path, text)

    path = "app/templates/duplicate_trash.html"
    text = read(path)
    anchor = '{% if lockdown_mode %}'
    if anchor in text:
        text = text.replace(
            anchor,
            '{% if read_only_mode %}<div class="form-error"><strong>Read-Only Mode</strong><br>Restore and other media-file changes are blocked. Existing managed-trash files are preserved and automatic permanent removal is paused.</div>{% endif %}\n' + anchor,
            1,
        )
    write(path, text)


def patch_css() -> None:
    path = "app/static/workspace.css"
    text = read(path)
    marker = "/* Global media read-only mode */"
    if marker in text:
        raise RuntimeError("read-only CSS already exists")
    text += '''\n\n/* Global media read-only mode */\n.read-only-mode-banner {align-items:center;background:rgba(74,160,255,.08);border:1px solid rgba(74,160,255,.38);border-radius:8px;color:var(--text);display:flex;gap:10px;margin:0 0 12px;padding:9px 12px}\n.read-only-mode-banner strong {color:#78b9ff;font-size:12px;white-space:nowrap}\n.read-only-mode-banner span {color:var(--muted);font-size:11px;line-height:1.35}\n.read-only-mode-banner a {color:#9bceff;font-size:11px;margin-left:auto;white-space:nowrap}\n.safety-mode-three {grid-template-columns:repeat(3,minmax(0,1fr))!important}\n@media (max-width:900px) {.safety-mode-three {grid-template-columns:1fr!important}.read-only-mode-banner {align-items:flex-start;flex-wrap:wrap}.read-only-mode-banner a {margin-left:0}}\n'''
    write(path, text)


def patch_tests() -> None:
    path = "tests/test_app_settings.py"
    text = read(path)
    text = replace_once(
        text,
        '        self.assertEqual(self.settings.get("lockdown_mode"), "0")\n',
        '        self.assertEqual(self.settings.get("lockdown_mode"), "0")\n        self.assertEqual(self.settings.get("read_only_mode"), "0")\n        self.assertEqual(self.settings.file_protection_mode(), "standard")\n',
        "read-only defaults test",
    )
    anchor = '''    def test_tv_season_display_default_is_explicit_and_portable(self):\n'''
    new_test = '''    def test_file_protection_modes_are_mutually_exclusive_and_portable(self):\n        self.assertEqual(\n            self.settings.validate_safety("read-only"),\n            {"read_only_mode": "1", "lockdown_mode": "0"},\n        )\n        self.assertEqual(\n            self.settings.validate_safety("standard"),\n            {"read_only_mode": "0", "lockdown_mode": "0"},\n        )\n        self.assertEqual(\n            self.settings.validate_safety("lockdown"),\n            {"read_only_mode": "0", "lockdown_mode": "1"},\n        )\n        imported = self.settings.validate_import({\n            "read_only_mode": "1", "lockdown_mode": "0",\n        })\n        self.assertEqual(imported["read_only_mode"], "1")\n        self.assertEqual(imported["lockdown_mode"], "0")\n        with self.assertRaisesRegex(AppSettingError, "Read-Only Mode"):\n            self.settings.validate_safety("unsafe")\n\n'''
    text = replace_once(text, anchor, new_test + anchor, "file protection mode settings tests")
    write(path, text)

    path = "tests/test_safety_ui_stabilization.py"
    text = read(path)
    text = text.replace(
        '        self.assertIn(\'self.app_settings.get("lockdown_mode") == "1"\', background)\n',
        '        self.assertIn(\'protection_mode in {"readonly", "lockdown"}\', background)\n',
        1,
    )
    text = text.replace(
        '        self.assertIn(\'"lockdown_mode": app_settings.get("lockdown_mode") == "1"\', routes)\n',
        '        self.assertIn(\'"lockdown_mode": app_settings.get("lockdown_mode") == "1"\', routes)\n        self.assertIn(\'"read_only_mode": app_settings.get("read_only_mode") == "1"\', routes)\n',
        1,
    )
    text = text.replace(
        '    def test_system_settings_expose_standard_and_lockdown_modes(self):\n',
        '    def test_system_settings_expose_read_only_standard_and_lockdown_modes(self):\n',
        1,
    )
    text = text.replace(
        '        self.assertIn("Standard Mode", template)\n',
        '        self.assertIn("Read-Only Mode", template)\n        self.assertIn("Standard Mode", template)\n',
        1,
    )
    write(path, text)

    path = ROOT / "tests" / "test_read_only_mode.py"
    if path.exists():
        raise RuntimeError("tests/test_read_only_mode.py already exists")
    path.write_text('''import tempfile\nimport unittest\nfrom pathlib import Path\n\nfrom app.app_settings import AppSettings\nfrom app.db import Database\nfrom app.file_protection import FileProtectionService, MediaWriteBlocked\n\n\nclass ReadOnlyModeTests(unittest.TestCase):\n    def setUp(self):\n        self.temporary = tempfile.TemporaryDirectory()\n        self.database = Database(Path(self.temporary.name) / "catalog.db")\n        self.database.initialize()\n        self.settings = AppSettings(self.database, "https://example.test/?q={query}")\n        self.protection = FileProtectionService(self.settings)\n\n    def tearDown(self):\n        self.temporary.cleanup()\n\n    def test_read_only_blocks_media_writes_but_standard_and_lockdown_allow_reviewed_changes(self):\n        self.settings.update(self.settings.validate_safety("readonly"), None)\n        self.assertEqual(self.protection.mode, "readonly")\n        self.assertFalse(self.protection.media_writes_allowed)\n        self.assertFalse(self.protection.automatic_permanent_delete_allowed)\n        with self.assertRaisesRegex(MediaWriteBlocked, "Read-Only Mode"):\n            self.protection.require_media_write("rename a file")\n        self.settings.update(self.settings.validate_safety("standard"), None)\n        self.protection.require_media_write("rename a file")\n        self.assertTrue(self.protection.automatic_permanent_delete_allowed)\n        self.settings.update(self.settings.validate_safety("lockdown"), None)\n        self.protection.require_media_write("rename a file")\n        self.assertFalse(self.protection.automatic_permanent_delete_allowed)\n\n    def test_filesystem_mutating_routes_use_the_central_read_only_gate(self):\n        root = Path(__file__).resolve().parents[1]\n        titles = (root / "app/routes/titles.py").read_text(encoding="utf-8")\n        review = (root / "app/routes/review.py").read_text(encoding="utf-8")\n        operations = (root / "app/routes/operations.py").read_text(encoding="utf-8")\n        background = (root / "app/background.py").read_text(encoding="utf-8")\n        self.assertGreaterEqual(titles.count("file_protection.require_media_write"), 5)\n        self.assertGreaterEqual(review.count("file_protection.require_media_write"), 2)\n        self.assertIn('file_protection.require_media_write("undo filesystem operations")', operations)\n        self.assertIn('protection_mode in {"readonly", "lockdown"}', background)\n\n    def test_read_only_banner_and_three_mode_settings_are_visible(self):\n        root = Path(__file__).resolve().parents[1]\n        base = (root / "app/templates/base.html").read_text(encoding="utf-8")\n        settings = (root / "app/templates/settings.html").read_text(encoding="utf-8")\n        self.assertIn("read-only-mode-banner", base)\n        self.assertIn('value="readonly"', settings)\n        self.assertIn('value="standard"', settings)\n        self.assertIn('value="lockdown"', settings)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")

    path = "tests/test_operation_history.py"
    text = read(path)
    anchor = '''    def test_synthetic_auth_disabled_actor_is_recorded_as_system(self):\n'''
    test = '''    def test_malformed_undo_payload_fails_closed_before_operation_is_claimed(self):\n        operation_id = self.history.record(\n            "rename_file", "Bad payload", actor_user_id=self.user_id,\n            undo_kind="rename_file", undo_payload={"file_id": self.file_id},\n        )\n        with self.database.connect() as conn:\n            conn.execute(\n                "UPDATE operation_history SET undo_payload='not-json' WHERE id=?",\n                (operation_id,),\n            )\n        with self.assertRaisesRegex(OperationHistoryError, "recorded operation data is invalid"):\n            self.history.undo(operation_id, self.user_id)\n        with self.database.connect() as conn:\n            status = conn.execute(\n                "SELECT status FROM operation_history WHERE id=?", (operation_id,)\n            ).fetchone()["status"]\n        self.assertEqual(status, "completed")\n\n'''
    text = replace_once(text, anchor, test + anchor, "malformed undo payload test")
    write(path, text)


def patch_docs() -> None:
    path = "docs/WORKSPACE.md"
    text = read(path)
    marker = "## W6 Operation History + Safe Undo\n"
    section = '''## Global File Protection Modes\n\nInfoMancer exposes three mutually exclusive installation-wide media safety modes.\n**Read-Only Mode** blocks every InfoMancer operation that renames, moves, restores,\nor permanently deletes user media while leaving scans, matching, inspection, MIE,\nmetadata, tags, collections, and application/database maintenance available.\n**Standard Mode** permits reviewed filesystem changes. **Lockdown Mode** permits\nreviewed reversible changes while pausing automatic permanent managed-trash deletion\nand reserving stronger confirmation for irreversible actions.\n\nThe media-write boundary is enforced server-side through one FileProtectionService and\nnot only by hidden or disabled controls. Current rename paths, managed-trash move and\nrestore, W6 undo, and scheduled permanent trash cleanup all consult that boundary.\nA persistent Librarian banner makes Read-Only state visible throughout the workspace.\n\n'''
    text = replace_once(text, marker, section + marker, "read-only mode documentation")
    write(path, text)


def main() -> None:
    add_file_protection_service()
    patch_app_settings()
    patch_settings_route()
    patch_settings_template()
    patch_main_context()
    patch_base_template()
    patch_background()
    patch_title_routes()
    patch_review_routes()
    patch_operation_routes()
    patch_operation_history_json_edge()
    patch_trash_templates()
    patch_css()
    patch_tests()
    patch_docs()


if __name__ == "__main__":
    main()

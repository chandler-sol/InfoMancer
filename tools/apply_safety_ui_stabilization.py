from __future__ import annotations

import re
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"marker not found: {label}")
    return text.replace(old, new, 1)


# App-level safety preference.
path = "app/app_settings.py"
text = read(path)
text = replace_once(
    text,
    '        "trash_retention_days",\n        "hash_mode",',
    '        "trash_retention_days",\n        "lockdown_mode",\n        "hash_mode",',
    "editable lockdown key",
)
text = replace_once(
    text,
    '            "trash_retention_days": "30",\n            "hash_mode": "automatic",',
    '            "trash_retention_days": "30",\n            "lockdown_mode": "0",\n            "hash_mode": "automatic",',
    "lockdown default",
)
text = replace_once(
    text,
    '    def validate_hashing(\n',
    '''    def validate_safety(self, lockdown_mode: str) -> dict[str, str]:\n        mode = lockdown_mode.strip().casefold()\n        if mode not in {"0", "1", "standard", "lockdown"}:\n            raise AppSettingError("Choose Standard Mode or Lockdown Mode.")\n        return {"lockdown_mode": "1" if mode in {"1", "lockdown"} else "0"}\n\n    def validate_hashing(\n''',
    "safety validator",
)
text = replace_once(
    text,
    '        if "log_level" in text_values:\n            validated.update(self.validate_logging(text_values["log_level"]))\n',
    '        if "log_level" in text_values:\n            validated.update(self.validate_logging(text_values["log_level"]))\n        if "lockdown_mode" in text_values:\n            validated.update(self.validate_safety(text_values["lockdown_mode"]))\n',
    "safety import validation",
)
write(path, text)


# Lockdown pauses automatic permanent trash deletion.
path = "app/background.py"
text = read(path)
text = replace_once(
    text,
    '    def maybe_start_trash_cleanup(self) -> None:\n        """Check for expired managed-trash items at most once per day."""\n        now = time.time()\n',
    '''    def maybe_start_trash_cleanup(self) -> None:\n        """Check for expired managed-trash items at most once per day."""\n        if self.app_settings.get("lockdown_mode") == "1":\n            with self.trash_cleanup_lock:\n                self.trash_cleanup_job.clear()\n                self.trash_cleanup_job.update({\n                    "status": "paused",\n                    "detail": "Lockdown Mode is preventing permanent managed-trash deletion",\n                })\n            return\n        now = time.time()\n''',
    "lockdown trash guard",
)
write(path, text)


# MIE library-wide quality defaults and source inheritance.
path = "app/mie.py"
text = read(path)
text = replace_once(
    text,
    '''            profiles = {\n                int(row["root_id"]): dict(row)\n                for row in conn.execute("SELECT * FROM mie_quality_profiles")\n            }\n            title_quality_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)\n            for title_id, title_files in files_by_title.items():\n                title = titles.get(title_id)\n                if not title or int(title["root_id"]) not in profiles:\n                    continue\n                profile = profiles[int(title["root_id"])]\n''',
    '''            source_profiles = {\n                int(row["root_id"]): dict(row)\n                for row in conn.execute("SELECT * FROM mie_quality_profiles")\n            }\n            library_profile = self._library_quality_defaults_raw(conn)\n            enabled_root_ids = {\n                int(row["id"]) for row in conn.execute("SELECT id FROM roots WHERE enabled=1")\n            }\n            profiles = {\n                root_id: source_profiles.get(root_id) or library_profile\n                for root_id in enabled_root_ids\n                if source_profiles.get(root_id) is not None or library_profile is not None\n            }\n            title_quality_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)\n            for title_id, title_files in files_by_title.items():\n                title = titles.get(title_id)\n                if not title or int(title["root_id"]) not in profiles:\n                    continue\n                profile = profiles[int(title["root_id"])]\n''',
    "quality inheritance analysis",
)
text = text.replace("outside this source's quality profile", "outside its effective quality profile")
text = text.replace(
    '"set for this source. " + "; ".join(violations) + "."',
    '"set for this source or inherited from the library defaults. " + "; ".join(violations) + "."',
)
start = text.index("    def quality_profiles(self) -> list[dict[str, Any]]:")
end = text.index("    def summary(self) -> dict[str, Any]:", start)
quality_block = '''    @staticmethod\n    def _normalize_quality_profile(\n        *, minimum_width: str = "", minimum_height: str = "",\n        minimum_bitrate_mbps: str = "", preferred_video_codecs: str = "",\n        preferred_containers: str = "", minimum_audio_channels: str = "",\n        dynamic_range: str = "any", detect_outliers: bool = True,\n    ) -> dict[str, Any]:\n        def optional_integer(value: str, label: str, maximum: int) -> int | None:\n            value = value.strip()\n            if not value:\n                return None\n            try:\n                parsed = int(value)\n            except ValueError as exc:\n                raise ValueError(f"{label} must be a whole number or left blank.") from exc\n            if parsed < 1 or parsed > maximum:\n                raise ValueError(f"{label} must be between 1 and {maximum:,}.")\n            return parsed\n\n        width = optional_integer(minimum_width, "Minimum width", 16_384)\n        height = optional_integer(minimum_height, "Minimum height", 16_384)\n        channels = optional_integer(minimum_audio_channels, "Minimum audio channels", 32)\n        bitrate_text = minimum_bitrate_mbps.strip()\n        bitrate = None\n        if bitrate_text:\n            try:\n                bitrate_mbps = float(bitrate_text)\n            except ValueError as exc:\n                raise ValueError("Minimum bitrate must be a number in Mbps or left blank.") from exc\n            if bitrate_mbps <= 0 or bitrate_mbps > 1_000:\n                raise ValueError("Minimum bitrate must be greater than 0 and at most 1,000 Mbps.")\n            bitrate = round(bitrate_mbps * 1_000_000)\n\n        def normalized_list(value: str, label: str) -> str:\n            items = []\n            for item in value.split(","):\n                normalized = item.strip().upper()\n                if not normalized:\n                    continue\n                if not re.fullmatch(r"[A-Z0-9._+\\-]{1,30}", normalized):\n                    raise ValueError(\n                        f"{label} entries may use letters, numbers, dots, plus signs, "\n                        "dashes, or underscores. Separate multiple entries with commas."\n                    )\n                if normalized not in items:\n                    items.append(normalized)\n            return ", ".join(items)\n\n        codecs = normalized_list(preferred_video_codecs, "Preferred video codec")\n        containers = normalized_list(preferred_containers, "Preferred container")\n        range_value = dynamic_range.strip().casefold()\n        if range_value not in {"any", "sdr", "hdr"}:\n            raise ValueError("Dynamic range preference must be Any, SDR, or HDR.")\n        return {\n            "minimum_width": width, "minimum_height": height,\n            "minimum_bitrate": bitrate,\n            "preferred_video_codecs": codecs, "preferred_containers": containers,\n            "minimum_audio_channels": channels, "dynamic_range": range_value,\n            "detect_outliers": int(bool(detect_outliers)),\n        }\n\n    @staticmethod\n    def _quality_profile_display(profile: dict[str, Any] | None) -> dict[str, Any]:\n        profile = dict(profile or {})\n        return {\n            "minimum_width": profile.get("minimum_width"),\n            "minimum_height": profile.get("minimum_height"),\n            "minimum_bitrate": profile.get("minimum_bitrate"),\n            "minimum_bitrate_mbps": (\n                round(int(profile["minimum_bitrate"]) / 1_000_000, 2)\n                if profile.get("minimum_bitrate") else ""\n            ),\n            "preferred_video_codecs": profile.get("preferred_video_codecs") or "",\n            "preferred_containers": profile.get("preferred_containers") or "",\n            "minimum_audio_channels": profile.get("minimum_audio_channels"),\n            "dynamic_range": profile.get("dynamic_range") or "any",\n            "detect_outliers": True if profile.get("detect_outliers") is None else bool(profile.get("detect_outliers")),\n        }\n\n    def _library_quality_defaults_raw(self, conn=None) -> dict[str, Any] | None:\n        owns_connection = conn is None\n        if owns_connection:\n            conn = self.database.connect()\n        try:\n            row = conn.execute(\n                "SELECT value FROM app_settings WHERE key='mie_quality_defaults'"\n            ).fetchone()\n            if not row or not row["value"]:\n                return None\n            value = json.loads(row["value"])\n            return value if isinstance(value, dict) else None\n        except (json.JSONDecodeError, TypeError):\n            return None\n        finally:\n            if owns_connection:\n                conn.close()\n\n    def library_quality_defaults(self) -> dict[str, Any]:\n        raw = self._library_quality_defaults_raw()\n        result = self._quality_profile_display(raw)\n        result["configured"] = raw is not None\n        with self.database.connect() as conn:\n            total = int(conn.execute("SELECT COUNT(*) FROM roots WHERE enabled=1").fetchone()[0])\n            overrides = int(conn.execute(\n                """SELECT COUNT(*) FROM mie_quality_profiles p\n                   JOIN roots r ON r.id=p.root_id WHERE r.enabled=1"""\n            ).fetchone()[0])\n        result["source_count"] = total\n        result["override_count"] = overrides\n        result["inherited_count"] = max(0, total - overrides) if raw is not None else 0\n        return result\n\n    def save_library_quality_defaults(\n        self, *, minimum_width: str = "", minimum_height: str = "",\n        minimum_bitrate_mbps: str = "", preferred_video_codecs: str = "",\n        preferred_containers: str = "", minimum_audio_channels: str = "",\n        dynamic_range: str = "any", detect_outliers: bool = True,\n        user_id: int | None = None,\n    ) -> None:\n        profile = self._normalize_quality_profile(\n            minimum_width=minimum_width, minimum_height=minimum_height,\n            minimum_bitrate_mbps=minimum_bitrate_mbps,\n            preferred_video_codecs=preferred_video_codecs,\n            preferred_containers=preferred_containers,\n            minimum_audio_channels=minimum_audio_channels, dynamic_range=dynamic_range,\n            detect_outliers=detect_outliers,\n        )\n        with self.database.connect() as conn:\n            conn.execute(\n                """INSERT INTO app_settings(key,value,updated_by,updated_at)\n                   VALUES ('mie_quality_defaults',?,?,CURRENT_TIMESTAMP)\n                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,\n                     updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",\n                (json.dumps(profile, ensure_ascii=False), user_id if user_id and user_id > 0 else None),\n            )\n\n    def delete_library_quality_defaults(self) -> bool:\n        with self.database.connect() as conn:\n            result = conn.execute("DELETE FROM app_settings WHERE key='mie_quality_defaults'")\n        return bool(result.rowcount)\n\n    def quality_profiles(self) -> list[dict[str, Any]]:\n        library_defaults = self._library_quality_defaults_raw()\n        with self.database.connect() as conn:\n            rows = conn.execute(\n                """SELECT r.id root_id,r.label,r.path,r.kind,\n                          p.minimum_width,p.minimum_height,p.minimum_bitrate,\n                          p.preferred_video_codecs,p.preferred_containers,\n                          p.minimum_audio_channels,p.dynamic_range,p.detect_outliers,\n                          p.updated_at\n                   FROM roots r LEFT JOIN mie_quality_profiles p ON p.root_id=r.id\n                   WHERE r.enabled=1 ORDER BY r.kind,r.label COLLATE NOCASE,r.path"""\n            ).fetchall()\n        profiles = []\n        for row in rows:\n            override = row["updated_at"] is not None\n            raw = dict(row) if override else library_defaults\n            profile = {"root_id": row["root_id"], "label": row["label"],\n                       "path": row["path"], "kind": row["kind"],\n                       "updated_at": row["updated_at"]}\n            profile.update(self._quality_profile_display(raw))\n            profile["configured"] = override\n            profile["inheriting"] = bool(not override and library_defaults is not None)\n            profile["state_label"] = (\n                "Source override" if override else\n                "Inheriting library defaults" if library_defaults is not None else\n                "Not configured"\n            )\n            profiles.append(profile)\n        return profiles\n\n    def save_quality_profile(\n        self, root_id: int, *, minimum_width: str = "", minimum_height: str = "",\n        minimum_bitrate_mbps: str = "", preferred_video_codecs: str = "",\n        preferred_containers: str = "", minimum_audio_channels: str = "",\n        dynamic_range: str = "any", detect_outliers: bool = True,\n        user_id: int | None = None,\n    ) -> None:\n        profile = self._normalize_quality_profile(\n            minimum_width=minimum_width, minimum_height=minimum_height,\n            minimum_bitrate_mbps=minimum_bitrate_mbps,\n            preferred_video_codecs=preferred_video_codecs,\n            preferred_containers=preferred_containers,\n            minimum_audio_channels=minimum_audio_channels, dynamic_range=dynamic_range,\n            detect_outliers=detect_outliers,\n        )\n        with self.database.connect() as conn:\n            if not conn.execute(\n                "SELECT 1 FROM roots WHERE id=? AND enabled=1", (root_id,)\n            ).fetchone():\n                raise ValueError(\n                    "That media source is no longer available. Refresh Library Health and try again."\n                )\n            conn.execute(\n                """INSERT INTO mie_quality_profiles(\n                     root_id,minimum_width,minimum_height,minimum_bitrate,\n                     preferred_video_codecs,preferred_containers,\n                     minimum_audio_channels,dynamic_range,detect_outliers,\n                     updated_by,updated_at\n                   ) VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)\n                   ON CONFLICT(root_id) DO UPDATE SET\n                     minimum_width=excluded.minimum_width,\n                     minimum_height=excluded.minimum_height,\n                     minimum_bitrate=excluded.minimum_bitrate,\n                     preferred_video_codecs=excluded.preferred_video_codecs,\n                     preferred_containers=excluded.preferred_containers,\n                     minimum_audio_channels=excluded.minimum_audio_channels,\n                     dynamic_range=excluded.dynamic_range,\n                     detect_outliers=excluded.detect_outliers,\n                     updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",\n                (root_id, profile["minimum_width"], profile["minimum_height"],\n                 profile["minimum_bitrate"], profile["preferred_video_codecs"],\n                 profile["preferred_containers"], profile["minimum_audio_channels"],\n                 profile["dynamic_range"], profile["detect_outliers"],\n                 user_id if user_id and user_id > 0 else None),\n            )\n\n    def delete_quality_profile(self, root_id: int) -> bool:\n        with self.database.connect() as conn:\n            result = conn.execute(\n                "DELETE FROM mie_quality_profiles WHERE root_id=?", (root_id,)\n            )\n        return result.rowcount == 1\n\n'''
text = text[:start] + quality_block + text[end:]
write(path, text)


# Review routes: pass defaults, add global-default mutations.
path = "app/routes/review.py"
text = read(path)
text = text.replace(
    '                        "quality_profiles": mie.quality_profiles(),\n',
    '                        "quality_profiles": mie.quality_profiles(),\n                        "quality_defaults": mie.library_quality_defaults(),\n',
)
text = text.replace(
    '            "quality_profiles": mie.quality_profiles(),\n',
    '            "quality_profiles": mie.quality_profiles(),\n            "quality_defaults": mie.library_quality_defaults(),\n',
)
marker = '    @librarian_post("/library-health/quality-profiles/{root_id}")\n'
if marker not in text:
    raise SystemExit("quality route marker not found")
default_routes = '''    @librarian_post("/library-health/quality-defaults")\n    def save_library_quality_defaults(\n        request: Request, minimum_width: str = Form(""), minimum_height: str = Form(""),\n        minimum_bitrate_mbps: str = Form(""), preferred_video_codecs: str = Form(""),\n        preferred_containers: str = Form(""), minimum_audio_channels: str = Form(""),\n        dynamic_range: str = Form("any"), detect_outliers: str = Form(""),\n    ):\n        try:\n            mie.save_library_quality_defaults(\n                minimum_width=minimum_width, minimum_height=minimum_height,\n                minimum_bitrate_mbps=minimum_bitrate_mbps,\n                preferred_video_codecs=preferred_video_codecs,\n                preferred_containers=preferred_containers,\n                minimum_audio_channels=minimum_audio_channels,\n                dynamic_range=dynamic_range, detect_outliers=detect_outliers == "on",\n                user_id=request.state.user.id,\n            )\n            finding_count = mie.analyze()\n        except (ValueError, sqlite3.Error) as exc:\n            return redirect("/library-health", f"Library quality defaults were not saved. {exc}")\n        record_event(\n            "mie", "Library-wide quality defaults were saved.",\n            context={"finding_count": finding_count}, user_id=request.state.user.id,\n        )\n        return redirect(\n            "/library-health",\n            "Library quality defaults saved and analysis refreshed. Source overrides were preserved.",\n        )\n\n    @librarian_post("/library-health/quality-defaults/delete")\n    def delete_library_quality_defaults(request: Request):\n        mie.delete_library_quality_defaults()\n        finding_count = mie.analyze()\n        record_event(\n            "mie", "Library-wide quality defaults were cleared.",\n            context={"finding_count": finding_count}, user_id=request.state.user.id,\n        )\n        return redirect(\n            "/library-health",\n            "Library quality defaults cleared. Existing source overrides remain configured.",\n        )\n\n'''
text = text.replace(marker, default_routes + marker, 1)
write(path, text)


# Settings safety route and configuration-only source removal.
path = "app/routes/settings.py"
text = read(path)
marker = '    @librarian_post("/maintenance/backups")\n'
if marker not in text:
    raise SystemExit("settings safety route marker not found")
safety_route = '''    @librarian_post("/settings/safety")\n    def update_safety_mode(request: Request, lockdown_mode: str = Form("0")):\n        try:\n            values = app_settings.validate_safety(lockdown_mode)\n            changed = app_settings.update(values, request.state.user.id)\n        except AppSettingError as exc:\n            return render_settings(request, "system", str(exc), status_code=400)\n        mode = "Lockdown Mode" if values["lockdown_mode"] == "1" else "Standard Mode"\n        message = (\n            f"Safety mode changed to {mode}." if changed else f"{mode} is already active."\n        )\n        record_event(\n            "settings", message, context={"lockdown_mode": values["lockdown_mode"]},\n            user_id=request.state.user.id,\n        )\n        return redirect("/settings/system", message)\n\n'''
text = text.replace(marker, safety_route + marker, 1)
old = '''    @librarian_post("/roots/{root_id}/delete")\n    def delete_root(root_id: int, confirm: str = Form("")):\n        if confirm != "REMOVE":\n            return redirect("/sources", "Type REMOVE to remove a catalog root")\n        with db.connect() as conn:\n            conn.execute("DELETE FROM roots WHERE id=?", (root_id,))\n'''
new = '''    @librarian_post("/roots/{root_id}/delete")\n    def delete_root(root_id: int):\n        with db.connect() as conn:\n            conn.execute("DELETE FROM roots WHERE id=?", (root_id,))\n'''
text = replace_once(text, old, new, "source delete confirmation backend")
write(path, text)


# System settings safety card.
path = "app/templates/settings.html"
text = read(path)
text = text.replace(
    '<a href="#storage">Storage</a><a href="#fingerprints">Fingerprints</a>',
    '<a href="#storage">Storage</a><a href="#safety">Safety</a><a href="#fingerprints">Fingerprints</a>',
    1,
)
marker = '  <section class="panel settings-card system-portable-card">\n'
if marker not in text:
    raise SystemExit("system portable card marker not found")
safety_card = '''  <section class="panel settings-card system-safety-card" id="safety">\n    <div class="settings-card-head"><div><p class="eyebrow">SAFETY</p><h2>File protection mode</h2></div><span class="settings-state {{ 'warn' if preferences.lockdown_mode == '1' else 'good' }}">{{ 'Lockdown' if preferences.lockdown_mode == '1' else 'Standard' }}</span></div>\n    <p class="muted">Standard Mode uses clear confirmation dialogs for destructive actions. Lockdown Mode adds typed confirmation to irreversible file decisions and pauses automatic managed-trash deletion.</p>\n    <form class="settings-form safety-mode-form" method="post" action="/settings/safety">\n      <label class="safety-mode-choice"><input type="radio" name="lockdown_mode" value="0" {% if preferences.lockdown_mode != '1' %}checked{% endif %}><span><strong>Standard Mode</strong><small>Confirm destructive actions with a normal warning dialog.</small></span></label>\n      <label class="safety-mode-choice"><input type="radio" name="lockdown_mode" value="1" {% if preferences.lockdown_mode == '1' %}checked{% endif %}><span><strong>Lockdown Mode</strong><small>Require additional typed confirmation for irreversible file deletion. Automatic managed-trash purging is paused.</small></span></label>\n      <button class="button primary">Save safety mode</button>\n    </form>\n  </section>\n'''
text = text.replace(marker, safety_card + marker, 1)
write(path, text)


# Source action rail: edit is a button; remove is a trash icon using W4 confirmation.
path = "app/templates/sources.html"
text = read(path)
text = text.replace('<div class="actions"><a class="button intake-button"', '<div class="actions sources-global-actions"><a class="button intake-button"', 1)
text = text.replace('<div class="actions">\n          <form method="post" action="/roots/{{ root.id }}/check">', '<div class="actions source-action-rail">\n          <form method="post" action="/roots/{{ root.id }}/check">', 1)
old = '''          <details class="root-name-editor"><summary>Edit name</summary>\n            <form method="post" action="/roots/{{ root.id }}/label" class="root-name-form">\n              <label>Display name <span class="muted">optional</span><input name="label" value="{{ root.label or '' }}" placeholder="For example, Family Movies" autocomplete="off" maxlength="120"></label>\n              <div class="actions"><button type="button" class="button small" data-cancel-root-name>Cancel</button><button class="button primary small">Save name</button></div>\n            </form>\n          </details>\n          <details><summary>Remove</summary><form class="confirm-inline" method="post" action="/roots/{{ root.id }}/delete"><input name="confirm" placeholder="Type REMOVE"><button class="danger">Remove catalog</button></form></details>\n'''
new = '''          <details class="root-name-editor source-action-popover"><summary class="button">Edit name</summary>\n            <form method="post" action="/roots/{{ root.id }}/label" class="root-name-form">\n              <label>Display name <span class="muted">optional</span><input name="label" value="{{ root.label or '' }}" placeholder="For example, Family Movies" autocomplete="off" maxlength="120"></label>\n              <div class="actions"><button type="button" class="button small" data-cancel-root-name>Cancel</button><button class="button primary small">Save name</button></div>\n            </form>\n          </details>\n          <form class="source-remove-form" method="post" action="/roots/{{ root.id }}/delete" data-workspace-confirm="Remove {{ root.label or root.path }} from InfoMancer? Its catalog records will be removed, but your media files will not be deleted.">\n            <button class="source-trash-button" type="submit" title="Remove source" aria-label="Remove {{ root.label or root.path }} source"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-2 6h10l-1 11H8L7 9Zm3 2v7h2v-7h-2Zm4 0v7h2v-7h-2Z"/></svg></button>\n          </form>\n'''
text = replace_once(text, old, new, "source action controls")
old_script = '''(() => {\n  document.querySelectorAll("[data-cancel-root-name]").forEach(button => {\n    button.addEventListener("click", () => button.closest("details")?.removeAttribute("open"));\n  });\n  document.addEventListener("click", event => {\n    document.querySelectorAll(".root-name-editor[open]").forEach(editor => {\n      if (!editor.contains(event.target)) editor.removeAttribute("open");\n    });\n  });\n})();\n'''
new_script = '''(() => {\n  const editors = [...document.querySelectorAll(".root-name-editor")];\n  const closeEditors = except => editors.forEach(editor => { if (editor !== except) editor.removeAttribute("open"); });\n  editors.forEach(editor => editor.addEventListener("toggle", () => { if (editor.open) closeEditors(editor); }));\n  document.querySelectorAll("[data-cancel-root-name]").forEach(button => {\n    button.addEventListener("click", () => button.closest("details")?.removeAttribute("open"));\n  });\n  document.addEventListener("click", event => {\n    editors.forEach(editor => { if (editor.open && !editor.contains(event.target)) editor.removeAttribute("open"); });\n  });\n  document.addEventListener("keydown", event => {\n    if (event.key === "Escape") closeEditors(null);\n  });\n})();\n'''
text = replace_once(text, old_script, new_script, "source editor exclusivity")
write(path, text)


# Quality-profile UI with library defaults and source overrides.
path = "app/templates/library_health.html"
text = read(path)
section_start = text.index('<details class="panel mie-quality-profiles">\n  <summary>\n    <span><strong>Quality and consistency profiles</strong>')
section_end = text.index('\n{% if feedback_rules %}', section_start)
quality_template = '''<details class="panel mie-quality-profiles">\n  <summary>\n    <span><strong>Quality and consistency profiles</strong><small>Set a library-wide baseline, then customize only the sources that need an exception.</small></span>\n  </summary>\n  <p class="mie-profile-intro">Blank preferences are ignored. MIE only reports differences and never replaces, moves, or deletes a media file.</p>\n  <section class="mie-profile-card mie-library-default-card">\n    <header>\n      <div><p class="eyebrow">LIBRARY DEFAULTS</p><h2>Default quality profile</h2><p>{% if quality_defaults.configured %}Applies to {{ quality_defaults.inherited_count }} of {{ quality_defaults.source_count }} enabled sources. {{ quality_defaults.override_count }} source{{ '' if quality_defaults.override_count == 1 else 's' }} use custom overrides.{% else %}No library-wide baseline is configured yet. Existing source overrides continue to work independently.{% endif %}</p></div>\n      <span class="mie-profile-state">{{ 'Configured' if quality_defaults.configured else 'Not configured' }}</span>\n    </header>\n    <form class="mie-profile-form" method="post" action="/library-health/quality-defaults">\n      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">\n      <label>Minimum width (pixels)<input name="minimum_width" inputmode="numeric" value="{{ quality_defaults.minimum_width or '' }}" placeholder="1920"></label>\n      <label>Minimum height (pixels)<input name="minimum_height" inputmode="numeric" value="{{ quality_defaults.minimum_height or '' }}" placeholder="1080"></label>\n      <label>Minimum bitrate (Mbps)<input name="minimum_bitrate_mbps" inputmode="decimal" value="{{ quality_defaults.minimum_bitrate_mbps }}" placeholder="8"></label>\n      <label>Preferred video codecs<input name="preferred_video_codecs" value="{{ quality_defaults.preferred_video_codecs }}" placeholder="HEVC, AV1"></label>\n      <label>Preferred containers<input name="preferred_containers" value="{{ quality_defaults.preferred_containers }}" placeholder="MATROSKA, MP4"></label>\n      <label>Minimum audio channels<input name="minimum_audio_channels" inputmode="numeric" value="{{ quality_defaults.minimum_audio_channels or '' }}" placeholder="6"></label>\n      <label>Dynamic range<select name="dynamic_range"><option value="any" {% if quality_defaults.dynamic_range == 'any' %}selected{% endif %}>Any</option><option value="sdr" {% if quality_defaults.dynamic_range == 'sdr' %}selected{% endif %}>Prefer SDR</option><option value="hdr" {% if quality_defaults.dynamic_range == 'hdr' %}selected{% endif %}>Prefer HDR</option></select></label>\n      <label class="mie-profile-checkbox"><input type="checkbox" name="detect_outliers" {% if quality_defaults.detect_outliers %}checked{% endif %}> Detect files that differ from a title's dominant technical profile</label>\n      <button class="button primary">Save library defaults and analyze</button>\n    </form>\n    {% if quality_defaults.configured %}<form method="post" action="/library-health/quality-defaults/delete" data-workspace-confirm="Clear the library-wide quality defaults? Source overrides will be preserved."><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="button">Clear library defaults</button></form>{% endif %}\n  </section>\n  <div class="mie-profile-list mie-source-overrides">\n    {% for profile in quality_profiles %}\n    <section class="mie-profile-card">\n      <header>\n        <div><h2>{{ profile.label or profile.path }}</h2><p>{{ profile.kind|title }} source{% if profile.configured %} · Override updated {{ local_time(profile.updated_at) }}{% endif %}</p></div>\n        <span class="mie-profile-state {{ 'inherited' if profile.inheriting else '' }}">{{ profile.state_label }}</span>\n      </header>\n      {% if profile.inheriting %}<p class="mie-effective-profile">Effective profile: inherited from the library defaults. Customize only when this source needs different expectations.</p>{% elif not profile.configured %}<p class="mie-effective-profile">No quality expectations apply to this source yet.</p>{% endif %}\n      <details class="mie-source-profile-editor" {% if profile.configured %}open{% endif %}>\n        <summary class="button">{{ 'Edit source override' if profile.configured else 'Customize this source' }}</summary>\n        <form class="mie-profile-form" method="post" action="/library-health/quality-profiles/{{ profile.root_id }}">\n          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">\n          <label>Minimum width (pixels)<input name="minimum_width" inputmode="numeric" value="{{ profile.minimum_width or '' }}" placeholder="1920"></label>\n          <label>Minimum height (pixels)<input name="minimum_height" inputmode="numeric" value="{{ profile.minimum_height or '' }}" placeholder="1080"></label>\n          <label>Minimum bitrate (Mbps)<input name="minimum_bitrate_mbps" inputmode="decimal" value="{{ profile.minimum_bitrate_mbps }}" placeholder="8"></label>\n          <label>Preferred video codecs<input name="preferred_video_codecs" value="{{ profile.preferred_video_codecs }}" placeholder="HEVC, AV1"></label>\n          <label>Preferred containers<input name="preferred_containers" value="{{ profile.preferred_containers }}" placeholder="MATROSKA, MP4"></label>\n          <label>Minimum audio channels<input name="minimum_audio_channels" inputmode="numeric" value="{{ profile.minimum_audio_channels or '' }}" placeholder="6"></label>\n          <label>Dynamic range<select name="dynamic_range"><option value="any" {% if profile.dynamic_range == 'any' %}selected{% endif %}>Any</option><option value="sdr" {% if profile.dynamic_range == 'sdr' %}selected{% endif %}>Prefer SDR</option><option value="hdr" {% if profile.dynamic_range == 'hdr' %}selected{% endif %}>Prefer HDR</option></select></label>\n          <label class="mie-profile-checkbox"><input type="checkbox" name="detect_outliers" {% if profile.detect_outliers %}checked{% endif %}> Detect files that differ from a title's dominant technical profile</label>\n          <button class="button primary">Save source override and analyze</button>\n        </form>\n      </details>\n      {% if profile.configured %}<form class="mie-profile-delete" method="post" action="/library-health/quality-profiles/{{ profile.root_id }}/delete"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="button">Use library defaults</button></form>{% endif %}\n    </section>\n    {% else %}<p>No enabled media sources are available. Add a source before creating a quality profile.</p>{% endfor %}\n  </div>\n</details>\n'''
text = text[:section_start] + quality_template + text[section_end:]
write(path, text)


# Workspace polish overrides for finding headers, sources, safety and profiles.
path = "app/static/workspace.css"
text = read(path)
css = r'''

/* 0.8 stabilization: Review alignment, Sources action rail, safety and MIE inheritance. */
.mie-finding-head {
  display: grid !important;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: start !important;
  justify-content: initial !important;
  gap: 12px !important;
}
.mie-finding-head > input[type="checkbox"] { margin: 7px 0 0 0; align-self: start; }
.mie-finding-head > div { min-width: 0; }
.mie-finding-head .mie-labels { justify-content: flex-start; }
.mie-finding-head .mie-title-kind { align-self: start; }

.sources-global-actions,
.source-action-rail { justify-content: flex-end; }
.source-action-rail { min-width: 430px; flex-wrap: nowrap; align-items: center; }
.source-action-rail > form,
.source-action-rail > details { margin: 0; }
.root-name-editor > summary.button { list-style: none; cursor: pointer; white-space: nowrap; }
.root-name-editor > summary.button::-webkit-details-marker { display: none; }
.source-remove-form { display: inline-flex; margin: 0; }
.source-trash-button {
  display: inline-grid; place-items: center; width: 38px; height: 38px;
  border: 1px solid var(--border); border-radius: 8px; background: transparent;
  color: var(--muted); cursor: pointer;
}
.source-trash-button svg { width: 17px; height: 17px; fill: currentColor; }
.source-trash-button:hover,
.source-trash-button:focus-visible { color: #ff6b6b; border-color: rgba(255, 107, 107, .65); background: rgba(255, 107, 107, .08); }
.root-name-editor[open] { position: relative; }
.root-name-editor[open] .root-name-form { z-index: 30; }

.mie-library-default-card { border-color: rgba(183, 255, 47, .28); margin-bottom: 18px; }
.mie-profile-state.inherited { color: var(--accent); }
.mie-effective-profile { margin: 0 0 12px; color: var(--muted); }
.mie-source-profile-editor { margin-top: 10px; }
.mie-source-profile-editor > summary { width: fit-content; list-style: none; cursor: pointer; }
.mie-source-profile-editor > summary::-webkit-details-marker { display: none; }
.mie-source-profile-editor[open] > summary { margin-bottom: 14px; }
.safety-mode-form { display: grid; gap: 10px; }
.safety-mode-choice { display: flex !important; gap: 10px; align-items: flex-start; padding: 12px; border: 1px solid var(--border); border-radius: 9px; }
.safety-mode-choice input { margin-top: 3px; }
.safety-mode-choice span { display: grid; gap: 3px; }
.safety-mode-choice small { color: var(--muted); }

@media (max-width: 1050px) {
  .source-action-rail { min-width: 0; flex-wrap: wrap; }
}
'''
if "0.8 stabilization: Review alignment" not in text:
    text += css
write(path, text)


# Regression tests.
path = "tests/test_app_settings.py"
text = read(path)
text = replace_once(
    text,
    '        self.assertEqual(self.settings.get("search_provider_name"), "example.test")\n',
    '        self.assertEqual(self.settings.get("search_provider_name"), "example.test")\n        self.assertEqual(self.settings.get("lockdown_mode"), "0")\n',
    "lockdown default test",
)
insert = '''\n    def test_safety_mode_is_explicit_and_portable(self):\n        self.assertEqual(self.settings.validate_safety("standard"), {"lockdown_mode": "0"})\n        self.assertEqual(self.settings.validate_safety("lockdown"), {"lockdown_mode": "1"})\n        self.settings.update({"lockdown_mode": "1"}, None)\n        self.assertEqual(self.settings.get("lockdown_mode"), "1")\n        self.assertEqual(self.settings.validate_import({"lockdown_mode": "0"}), {"lockdown_mode": "0"})\n        with self.assertRaisesRegex(AppSettingError, "Standard Mode or Lockdown Mode"):\n            self.settings.validate_safety("reckless")\n'''
text = text.replace('\n    def test_external_search_update(self):\n', insert + '\n    def test_external_search_update(self):\n', 1)
write(path, text)

path = "tests/test_mie.py"
text = read(path)
insert = '''\n    def test_library_quality_defaults_are_inherited_and_source_overrides_win(self):\n        initial = self.engine.library_quality_defaults()\n        self.assertFalse(initial["configured"])\n        self.engine.save_library_quality_defaults(\n            minimum_width="1920", minimum_height="1080", minimum_bitrate_mbps="8",\n            preferred_video_codecs="HEVC, AV1", preferred_containers="MATROSKA, MP4",\n            minimum_audio_channels="6", dynamic_range="any", detect_outliers=True,\n        )\n        defaults = self.engine.library_quality_defaults()\n        self.assertTrue(defaults["configured"])\n        self.assertEqual(defaults["minimum_width"], 1920)\n        inherited = self.engine.quality_profiles()[0]\n        self.assertTrue(inherited["inheriting"])\n        self.assertFalse(inherited["configured"])\n        self.assertEqual(inherited["minimum_width"], 1920)\n        self.engine.save_quality_profile(1, minimum_width="1280", detect_outliers=False)\n        override = self.engine.quality_profiles()[0]\n        self.assertTrue(override["configured"])\n        self.assertFalse(override["inheriting"])\n        self.assertEqual(override["minimum_width"], 1280)\n        self.engine.delete_quality_profile(1)\n        inherited_again = self.engine.quality_profiles()[0]\n        self.assertTrue(inherited_again["inheriting"])\n        self.assertEqual(inherited_again["minimum_width"], 1920)\n'''
text = text.replace('\n    def test_analysis_explains_existing_catalog_facts_without_changing_media(self):\n', insert + '\n    def test_analysis_explains_existing_catalog_facts_without_changing_media(self):\n', 1)
write(path, text)

path = "tests/test_safety_ui_stabilization.py"
write(path, '''import unittest\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parent.parent\n\n\nclass SafetyUiStabilizationContractTests(unittest.TestCase):\n    def test_lockdown_guards_automatic_permanent_trash_cleanup(self):\n        background = (ROOT / "app" / "background.py").read_text(encoding="utf-8")\n        self.assertIn('self.app_settings.get("lockdown_mode") == "1"', background)\n        self.assertIn("preventing permanent managed-trash deletion", background)\n\n    def test_sources_use_standard_confirmation_and_one_edit_surface(self):\n        template = (ROOT / "app" / "templates" / "sources.html").read_text(encoding="utf-8")\n        routes = (ROOT / "app" / "routes" / "settings.py").read_text(encoding="utf-8")\n        self.assertIn("source-action-rail", template)\n        self.assertIn("source-trash-button", template)\n        self.assertIn("data-workspace-confirm", template)\n        self.assertIn("media files will not be deleted", template)\n        self.assertNotIn("Type REMOVE", template)\n        self.assertIn("closeEditors(editor)", template)\n        self.assertIn('event.key === "Escape"', template)\n        self.assertIn('@librarian_post("/roots/{root_id}/delete")', routes)\n        self.assertNotIn('confirm != "REMOVE"', routes)\n\n    def test_quality_defaults_and_review_alignment_are_wired(self):\n        routes = (ROOT / "app" / "routes" / "review.py").read_text(encoding="utf-8")\n        template = (ROOT / "app" / "templates" / "library_health.html").read_text(encoding="utf-8")\n        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")\n        self.assertIn('/library-health/quality-defaults', routes)\n        self.assertIn("LIBRARY DEFAULTS", template)\n        self.assertIn("Inheriting library defaults", (ROOT / "app" / "mie.py").read_text(encoding="utf-8"))\n        self.assertIn("grid-template-columns: auto minmax(0, 1fr) auto", styles)\n\n    def test_system_settings_expose_standard_and_lockdown_modes(self):\n        template = (ROOT / "app" / "templates" / "settings.html").read_text(encoding="utf-8")\n        routes = (ROOT / "app" / "routes" / "settings.py").read_text(encoding="utf-8")\n        self.assertIn("Standard Mode", template)\n        self.assertIn("Lockdown Mode", template)\n        self.assertIn('/settings/safety', template)\n        self.assertIn('@librarian_post("/settings/safety")', routes)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''')

print("Safety/UI stabilization patch applied")

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"marker not found: {label}")
    return text.replace(old, new, 1)


# Clean Review context and make managed Trash reflect Lockdown Mode.
path = Path("app/routes/review.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''                        "quality_profiles": mie.quality_profiles(),
            "quality_defaults": mie.library_quality_defaults(),
                        "quality_defaults": mie.library_quality_defaults(),
''',
    '''                        "quality_profiles": mie.quality_profiles(),
                        "quality_defaults": mie.library_quality_defaults(),
''',
    "duplicate quality defaults context",
)
text = replace_once(
    text,
    '''        return templates.TemplateResponse(request, "duplicate_trash_preview.html", {
            "preview": preview,
            "message": request.query_params.get("message", ""),
        })
''',
    '''        return templates.TemplateResponse(request, "duplicate_trash_preview.html", {
            "preview": preview,
            "lockdown_mode": app_settings.get("lockdown_mode") == "1",
            "message": request.query_params.get("message", ""),
        })
''',
    "trash preview lockdown context",
)
text = replace_once(
    text,
    '''        message = (
            "The selected copy was moved into managed trash and removed from the active catalog. "
            "You can restore it from Duplicate Review → Trash until its retention date."
        )
''',
    '''        lockdown = app_settings.get("lockdown_mode") == "1"
        message = (
            "The selected copy was moved into managed trash and removed from the active catalog. "
            + (
                "Lockdown Mode is active, so automatic permanent removal is paused."
                if lockdown else
                "You can restore it from Duplicate Review → Trash until its retention date."
            )
        )
''',
    "trash move lockdown message",
)
text = replace_once(
    text,
    '''        return templates.TemplateResponse(request, "duplicate_trash.html", {
            "items": duplicate_trash.items(),
            "retention": app_settings.get("trash_retention_days"),
            "message": request.query_params.get("message", ""),
        })
''',
    '''        return templates.TemplateResponse(request, "duplicate_trash.html", {
            "items": duplicate_trash.items(),
            "retention": app_settings.get("trash_retention_days"),
            "lockdown_mode": app_settings.get("lockdown_mode") == "1",
            "message": request.query_params.get("message", ""),
        })
''',
    "trash page lockdown context",
)
text = replace_once(
    text,
    '''        label = "Never automatically" if retention == "never" else f"After {retention} days"
        return redirect(
            "/duplicates/trash",
            f"Managed-trash retention updated: {label}. This applies to files moved to trash from now on.",
        )
''',
    '''        label = "Never automatically" if retention == "never" else f"After {retention} days"
        lockdown_note = (
            " Lockdown Mode is active, so automatic permanent removal remains paused."
            if app_settings.get("lockdown_mode") == "1" else ""
        )
        return redirect(
            "/duplicates/trash",
            f"Managed-trash retention updated: {label}. This applies to files moved to trash from now on.{lockdown_note}",
        )
''',
    "retention lockdown message",
)
path.write_text(text, encoding="utf-8")


# Source removal remains configuration-only, but record it in Activity.
path = Path("app/routes/settings.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''    @librarian_post("/roots/{root_id}/delete")
    def delete_root(root_id: int):
        with db.connect() as conn:
            conn.execute("DELETE FROM roots WHERE id=?", (root_id,))
        return redirect("/sources", "Catalog root removed; media files were untouched")
''',
    '''    @librarian_post("/roots/{root_id}/delete")
    def delete_root(request: Request, root_id: int):
        with db.connect() as conn:
            root = conn.execute(
                "SELECT label,path,kind FROM roots WHERE id=?", (root_id,)
            ).fetchone()
            if not root:
                return redirect("/sources", "That media source no longer exists; nothing changed")
            conn.execute("DELETE FROM roots WHERE id=?", (root_id,))
        label = root["label"] or root["path"]
        record_event(
            "source", f"Media source removed from InfoMancer: {label}",
            context={"root_id": root_id, "path": root["path"], "kind": root["kind"]},
            user_id=request.state.user.id,
        )
        return redirect("/sources", "Catalog root removed; media files were untouched")
''',
    "source removal audit",
)
path.write_text(text, encoding="utf-8")


# Clarify inheritance fallback when no library default exists.
path = Path("app/templates/library_health.html")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''{% if profile.configured %}<form class="mie-profile-delete" method="post" action="/library-health/quality-profiles/{{ profile.root_id }}/delete"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="button">Use library defaults</button></form>{% endif %}''',
    '''{% if profile.configured %}<form class="mie-profile-delete" method="post" action="/library-health/quality-profiles/{{ profile.root_id }}/delete"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="button">{{ 'Use library defaults' if quality_defaults.configured else 'Remove source override' }}</button></form>{% endif %}''',
    "quality fallback label",
)
path.write_text(text, encoding="utf-8")


# Make Lockdown state explicit anywhere Managed Trash promises future deletion.
path = Path("app/templates/duplicate_trash_preview.html")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''    <div><dt>Automatic removal</dt><dd>{% if preview.purge_after %}After {{ preview.retention_days }} days{% else %}Never; restore or remove it manually{% endif %}</dd></div>
''',
    '''    <div><dt>Automatic removal</dt><dd>{% if lockdown_mode %}Paused by Lockdown Mode{% if preview.purge_after %}; the {{ preview.retention_days }}-day retention date is recorded{% endif %}{% elif preview.purge_after %}After {{ preview.retention_days }} days{% else %}Never; restore or remove it manually{% endif %}</dd></div>
''',
    "trash preview automatic removal",
)
text = replace_once(
    text,
    '''  <div class="notice warning">
    Confirm playback and edition differences before continuing. Moving this file removes it from the active catalog, but does not permanently delete it now.
  </div>
''',
    '''  <div class="notice warning">
    Confirm playback and edition differences before continuing. Moving this file removes it from the active catalog, but does not permanently delete it now.{% if lockdown_mode %} Lockdown Mode is active, so InfoMancer will not automatically purge it while that mode remains enabled.{% endif %}
  </div>
''',
    "trash preview lockdown notice",
)
path.write_text(text, encoding="utf-8")

path = Path("app/templates/duplicate_trash.html")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''    <p>Files moved here are outside the active catalog but remain on their original source until restored or their retention date passes.</p>
''',
    '''    <p>Files moved here are outside the active catalog but remain on their original source until restored or permanently removed.{% if lockdown_mode %} Lockdown Mode currently prevents automatic permanent removal.{% endif %}</p>
''',
    "trash page intro",
)
text = replace_once(
    text,
    '''    <p>InfoMancer checks once per day and permanently removes only managed-trash files whose retention date has passed.</p>
''',
    '''    <p>{% if lockdown_mode %}Lockdown Mode is active. Automatic permanent removal is paused; retention dates stay recorded and will be considered again only after returning to Standard Mode.{% else %}InfoMancer checks once per day and permanently removes only managed-trash files whose retention date has passed.{% endif %}</p>
''',
    "trash retention explanation",
)
text = replace_once(
    text,
    '''      <small>Moved {{ item.moved_at }}{% if item.purge_after %} · scheduled for removal {{ item.purge_after }}{% else %} · kept until you restore or remove it{% endif %}</small>
''',
    '''      <small>Moved {{ item.moved_at }}{% if item.purge_after and lockdown_mode %} · retention date {{ item.purge_after }} · automatic removal paused{% elif item.purge_after %} · scheduled for removal {{ item.purge_after }}{% else %} · kept until you restore or remove it{% endif %}</small>
''',
    "trash item lockdown state",
)
path.write_text(text, encoding="utf-8")


# Tighten regression contracts around the cleanup.
path = Path("tests/test_safety_ui_stabilization.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''        self.assertIn('/library-health/quality-defaults', routes)
        self.assertIn("LIBRARY DEFAULTS", template)
''',
    '''        self.assertIn('/library-health/quality-defaults', routes)
        self.assertEqual(routes.count('"quality_defaults": mie.library_quality_defaults()'), 2)
        self.assertIn("LIBRARY DEFAULTS", template)
        self.assertIn("Remove source override", template)
''',
    "quality cleanup contract",
)
text = replace_once(
    text,
    '''        self.assertIn('@librarian_post("/roots/{root_id}/delete")', routes)
        self.assertNotIn('confirm != "REMOVE"', routes)
''',
    '''        self.assertIn('@librarian_post("/roots/{root_id}/delete")', routes)
        self.assertNotIn('confirm != "REMOVE"', routes)
        self.assertIn("Media source removed from InfoMancer", routes)
''',
    "source audit contract",
)
insert = '''
    def test_managed_trash_explains_lockdown_pause(self):
        routes = (ROOT / "app" / "routes" / "review.py").read_text(encoding="utf-8")
        trash = (ROOT / "app" / "templates" / "duplicate_trash.html").read_text(encoding="utf-8")
        preview = (ROOT / "app" / "templates" / "duplicate_trash_preview.html").read_text(encoding="utf-8")
        self.assertIn('"lockdown_mode": app_settings.get("lockdown_mode") == "1"', routes)
        self.assertIn("Automatic permanent removal is paused", trash)
        self.assertIn("Paused by Lockdown Mode", preview)
'''
text = text.replace('\n    def test_system_settings_expose_standard_and_lockdown_modes(self):\n', insert + '\n    def test_system_settings_expose_standard_and_lockdown_modes(self):\n', 1)
path.write_text(text, encoding="utf-8")

print("Final safety/UI stabilization cleanup applied")

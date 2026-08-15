from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Anchor missing in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Aggregate media totals independently from the intentionally bounded file preview.
replace_once(
    "app/routes/library.py",
    '''            file_rows = conn.execute(
                """SELECT * FROM files WHERE title_id=?
                   ORDER BY version_preferred DESC,identity_confirmed DESC,id
                   LIMIT 12""",
                (title_id,),
            ).fetchall()
            tags = conn.execute(
''',
    '''            file_rows = conn.execute(
                """SELECT * FROM files WHERE title_id=?
                   ORDER BY version_preferred DESC,identity_confirmed DESC,id
                   LIMIT 12""",
                (title_id,),
            ).fetchall()
            file_totals = conn.execute(
                """SELECT COUNT(*) file_count,
                          COALESCE(SUM(size_bytes),0) total_size,
                          COALESCE(SUM(runtime_seconds),0) total_runtime
                   FROM files WHERE title_id=?""",
                (title_id,),
            ).fetchone()
            tags = conn.execute(
''',
)
replace_once(
    "app/routes/library.py",
    '''            "file_count": len(files),
            "total_size_display": size_label(total_size),
            "total_runtime_display": runtime_label(total_runtime),
''',
    '''            "file_count": int(file_totals["file_count"] or 0),
            "total_size_display": size_label(file_totals["total_size"]),
            "runtime_display": (
                primary["runtime_display"] if title["kind"] == "movie" and primary
                else runtime_label(file_totals["total_runtime"])
            ),
''',
)

# Make the partial truthful for large series/multiple versions and resilient to unknown source state.
replace_once(
    "app/templates/_workspace_inspector.html",
    '''<span class="workspace-status{% if title.source_health == 'healthy' %} good{% elif title.source_health in ['degraded','offline'] %} warning{% endif %}">Source {{ title.source_health|title }}</span>''',
    '''<span class="workspace-status{% if title.source_health == 'healthy' %} good{% elif title.source_health in ['degraded','offline'] %} warning{% endif %}">Source {{ (title.source_health or 'unknown')|title }}</span>''',
)
replace_once(
    "app/templates/_workspace_inspector.html",
    '''<div><small>Runtime</small><strong>{{ total_runtime_display or primary_file.runtime_display or 'Unknown' }}</strong></div>''',
    '''<div><small>Runtime</small><strong>{{ runtime_display or primary_file.runtime_display or 'Unknown' }}</strong></div>''',
)
replace_once(
    "app/templates/_workspace_inspector.html",
    '''{% if files|length > 5 %}<small class="workspace-inspector-more">+ {{ files|length - 5 }} more indexed files</small>{% endif %}''',
    '''{% if file_count > 5 %}<small class="workspace-inspector-more">+ {{ file_count - 5 }} more indexed files</small>{% endif %}''',
)

# One back action should close the Inspector rather than walk through every title ever clicked.
replace_once(
    "app/static/workspace.js",
    '''        inspectTitle(titleId, item, "push");''',
    '''        inspectTitle(titleId, item, selectedTitleId ? "replace" : "push");''',
)

# Contract assertion for the history behavior.
replace_once(
    "tests/test_workspace_ui.py",
    '''        self.assertIn('event.metaKey || event.ctrlKey', script)
        self.assertIn('infomancer-library-selection:', library)
''',
    '''        self.assertIn('event.metaKey || event.ctrlKey', script)
        self.assertIn('selectedTitleId ? "replace" : "push"', script)
        self.assertIn('infomancer-library-selection:', library)
''',
)

# Functional regression: preview may be capped, totals must not be.
test_path = ROOT / "tests/test_workspace_inspector.py"
test = test_path.read_text(encoding="utf-8")
anchor = '''    def test_missing_inspector_title_is_404(self):
'''
addition = '''    def test_inspector_media_totals_are_not_limited_by_preview_rows(self):
        with self.database.connect() as conn:
            for index in range(13):
                conn.execute(
                    """INSERT INTO files(
                         title_id,path,filename,extension,size_bytes,runtime_seconds,seen_scan
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (self.title_id, f"/movies/inspector-film/extra-{index}.mkv",
                     f"extra-{index}.mkv", ".mkv", 1024, 60, "test"),
                )
        response = self.client.get(f"/library/inspector/{self.title_id}")
        rendered = unescape(response.text)
        self.assertEqual(response.status_code, 200)
        self.assertIn("14 files", rendered)
        self.assertIn("+ 9 more indexed files", rendered)
        self.assertIn("2h 0m", rendered)

'''
if anchor not in test:
    raise RuntimeError("Workspace Inspector final test anchor missing")
test_path.write_text(test.replace(anchor, addition + anchor, 1), encoding="utf-8")

print("W2 finalization patch applied")

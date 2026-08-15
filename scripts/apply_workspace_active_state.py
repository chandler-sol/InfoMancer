from pathlib import Path


base_path = Path("app/templates/base.html")
base = base_path.read_text(encoding="utf-8")

old_library = '''            <a href="/library" title="Library"{% if workspace_library_active %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="M7 5 9 9m4-4 2 4m4-4 2 4M3 10h18"></path></svg><span>Library</span></a>'''
new_library = '''            <a href="/library" title="Library"{% if request.url.path == '/library' %} class="active" aria-current="page"{% elif workspace_library_active %} class="domain-current"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="M7 5 9 9m4-4 2 4m4-4 2 4M3 10h18"></path></svg><span>Library</span></a>'''
if old_library not in base:
    raise RuntimeError("Library primary nav block not found")
base = base.replace(old_library, new_library, 1)

old_review = '''            <a href="/library-health" title="Review"{% if workspace_review_active %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l2-6 4 12 2-6h6"></path><path d="M20 5.8A8.5 8.5 0 0 0 12 3a8.5 8.5 0 0 0-8 2.8"></path><path d="M4.5 17.5A18 18 0 0 0 12 22a18 18 0 0 0 7.5-4.5"></path></svg><span>Review</span></a>'''
new_review = '''            <a href="/library-health" title="Review"{% if workspace_review_active %} class="domain-current"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l2-6 4 12 2-6h6"></path><path d="M20 5.8A8.5 8.5 0 0 0 12 3a8.5 8.5 0 0 0-8 2.8"></path><path d="M4.5 17.5A18 18 0 0 0 12 22a18 18 0 0 0 7.5-4.5"></path></svg><span>Review</span></a>'''
if old_review not in base:
    raise RuntimeError("Review primary nav block not found")
base = base.replace(old_review, new_review, 1)
base_path.write_text(base, encoding="utf-8")

css_path = Path("app/static/workspace.css")
css = css_path.read_text(encoding="utf-8")
active_block = '''.workspace-nav-primary > a.active {
  background: rgba(185, 245, 66, .10);
  color: var(--text);
  box-shadow: inset 3px 0 0 var(--lime);
}
'''
if active_block not in css:
    raise RuntimeError("Primary active CSS block not found")
css = css.replace(
    active_block,
    active_block
    + '''
.workspace-nav-primary > a.domain-current {
  color: #b8c5d2;
}
''',
    1,
)

collapsed_marker = '''  body.has-app-sidebar.sidebar-collapsed .workspace-nav-primary {
    padding-inline: 0;
  }
'''
if collapsed_marker not in css:
    raise RuntimeError("Collapsed primary CSS marker not found")
css = css.replace(
    collapsed_marker,
    collapsed_marker
    + '''
  body.has-app-sidebar.sidebar-collapsed .workspace-nav-primary > a.domain-current {
    background: rgba(185, 245, 66, .10);
    color: var(--text);
    box-shadow: inset 3px 0 0 var(--lime);
  }
''',
    1,
)
css_path.write_text(css, encoding="utf-8")

test_path = Path("tests/test_workspace_ui.py")
tests = test_path.read_text(encoding="utf-8")
anchor = '        self.assertIn("0.8 α", base)\n'
if anchor not in tests:
    raise RuntimeError("Workspace navigation test anchor not found")
tests = tests.replace(
    anchor,
    anchor
    + '''        self.assertIn('class="domain-current"', base)
        self.assertIn("request.url.path == '/library'", base)
        self.assertIn("sidebar-collapsed .workspace-nav-primary > a.domain-current", styles)
''',
    1,
)
test_path.write_text(tests, encoding="utf-8")

print("Workspace active-state polish applied.")

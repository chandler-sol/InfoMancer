from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"Expected block not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Getting Started Sources is optional, so make that explicit instead of
# presenting a disabled Continue button while the stepper quietly allows Finish.
path = ROOT / "app/templates/getting_started.html"
text = path.read_text(encoding="utf-8")
old_sources = '''{% elif step == 'sources' %}
<section class="setup-step panel">
  <div class="setup-step-copy"><p class="eyebrow">STEP 3 OF 4</p><h2>Add your media folders</h2><p>Choose each folder that contains Movies or TV Shows. InfoMancer previews the folder, lets you confirm its type, and begins the first scan after you add it.</p></div>
  {% if roots %}<div class="setup-source-summary">{% for root in roots %}<article><span class="kind {{ root.kind }}">{{ root.kind }}</span><div><strong>{{ root.label or root.path }}</strong><small>{{ root.path }} · {{ root.title_count }} titles · {{ root.file_count }} video files</small></div></article>{% endfor %}</div>{% else %}<div class="setup-empty-source"><strong>No media folders added yet</strong><span>Add at least one Movie or TV Shows folder to continue.</span></div>{% endif %}
  <button class="button primary setup-browse-source" type="button" data-open-source-browser>{{ 'Add another folder' if roots else 'Browse folders' }}</button>
  <div class="source-type-hints" aria-label="Supported library types"><span><b class="kind movie">Movie</b> Movie files, title folders, and A–Z or number buckets</span><span><b class="kind tv">TV</b> Series folders, season folders, and episode files</span></div>
  <div class="setup-step-actions"><a class="button" href="/getting-started/metadata">Back</a><form method="post" action="/getting-started/sources"><button class="button primary" {% if not roots %}disabled aria-disabled="true"{% endif %}>Continue</button></form></div>
</section>
{% set source_return_to = '/getting-started/sources' %}{% include '_source_browser.html' %}
<script src="{{ url_for('static', path='source-browser.js') }}?v={{ static_version }}" defer></script>

{% elif step == 'finish' %}
<section class="setup-step panel setup-finish">
  <div class="setup-finish-mark" aria-hidden="true">✓</div>
  <div class="setup-step-copy"><p class="eyebrow">STEP 4 OF 4</p><h2>Your library is ready for its first scan</h2><p>InfoMancer is connected to your metadata provider and knows where your media lives. Finish setup to open Home and follow scan progress from the task widget.</p></div>
  <div class="setup-review"><div><span>Application</span><strong>InfoMancer</strong></div><div><span>Time zone</span><strong>{{ preferences.timezone.replace('_', ' ') }}</strong></div><div><span>TVDB</span><strong>{{ 'Connected' if tvdb_status.configured else 'Skipped for testing' }}</strong></div><div><span>Media sources</span><strong>{{ roots|length }}</strong></div></div>
  <div class="setup-step-actions"><a class="button" href="/getting-started/sources">Back</a><form method="post" action="/getting-started/complete"><button class="button primary">Finish setup</button></form></div>
</section>'''
new_sources = '''{% elif step == 'sources' %}
<section class="setup-step panel">
  <div class="setup-step-copy"><p class="eyebrow">STEP 3 OF 4</p><h2>Add your media folders</h2><p>Choose each folder that contains Movies or TV Shows. InfoMancer previews the folder, lets you confirm its type, and begins the first scan after you add it. You can also skip this step and add sources later.</p></div>
  {% if roots %}<div class="setup-source-summary">{% for root in roots %}<article><span class="kind {{ root.kind }}">{{ root.kind }}</span><div><strong>{{ root.label or root.path }}</strong><small>{{ root.path }} · {{ root.title_count }} titles · {{ root.file_count }} video files</small></div></article>{% endfor %}</div>{% else %}<div class="setup-empty-source"><strong>No media folders added yet</strong><span>Add a folder now, or skip for now and add your sources later from Settings.</span></div>{% endif %}
  <button class="button primary setup-browse-source" type="button" data-open-source-browser>{{ 'Add another folder' if roots else 'Browse folders' }}</button>
  <div class="source-type-hints" aria-label="Supported library types"><span><b class="kind movie">Movie</b> Movie files, title folders, and A–Z or number buckets</span><span><b class="kind tv">TV</b> Series folders, season folders, and episode files</span></div>
  <div class="setup-step-actions"><a class="button" href="/getting-started/metadata">Back</a><div class="actions">{% if not roots %}<a class="button" href="/getting-started/finish">Skip for now</a>{% endif %}<form method="post" action="/getting-started/sources"><button class="button primary" {% if not roots %}disabled aria-disabled="true"{% endif %}>Continue</button></form></div></div>
</section>
{% set source_return_to = '/getting-started/sources' %}{% include '_source_browser.html' %}
<script src="{{ url_for('static', path='source-browser.js') }}?v={{ static_version }}" defer></script>

{% elif step == 'finish' %}
<section class="setup-step panel setup-finish">
  <div class="setup-finish-mark" aria-hidden="true">✓</div>
  {% if roots %}
  <div class="setup-step-copy"><p class="eyebrow">STEP 4 OF 4</p><h2>Your library is ready for its first scan</h2><p>InfoMancer is connected to your metadata provider and knows where your media lives. Finish setup to open Home and follow scan progress from the task widget.</p></div>
  {% else %}
  <div class="setup-step-copy"><p class="eyebrow">STEP 4 OF 4</p><h2>You're ready to finish setup</h2><p>No media folders are connected yet. Finish setup to open Home, then add Movies or TV Shows whenever you're ready from Settings.</p></div>
  {% endif %}
  <div class="setup-review"><div><span>Application</span><strong>InfoMancer</strong></div><div><span>Time zone</span><strong>{{ preferences.timezone.replace('_', ' ') }}</strong></div><div><span>TVDB</span><strong>{{ 'Connected' if tvdb_status.configured else 'Skipped for testing' }}</strong></div><div><span>Media sources</span><strong>{{ roots|length if roots else 'Not added yet' }}</strong></div></div>
  <div class="setup-step-actions"><a class="button" href="/getting-started/sources">Back</a><form method="post" action="/getting-started/complete"><button class="button primary">Finish setup</button></form></div>
</section>'''
if old_sources not in text:
    raise SystemExit("Getting Started Sources/Finish block changed unexpectedly")
path.write_text(text.replace(old_sources, new_sources, 1), encoding="utf-8")


# 2) Make About read like an About page, not a licensing wall. Preserve all
# attribution and build-identity behavior while tightening copy and hierarchy.
path = ROOT / "app/templates/about.html"
text = path.read_text(encoding="utf-8")
text = text.replace(
    '<p>A local-first catalog for understanding, matching, and carefully organizing the Movies and TV Shows already stored on your server.</p>',
    '<p>A local-first media catalog for understanding, matching, and carefully organizing the Movies and TV Shows you already own.</p>',
    1,
)
text = text.replace(
    '''  <header>
    <p class="eyebrow">METADATA &amp; ATTRIBUTION</p>
    <h2>Information that makes the catalog useful</h2>
    <p>InfoMancer combines information from external metadata providers with details discovered from your local files.</p>
  </header>''',
    '''  <header>
    <p class="eyebrow">WITH THANKS</p>
    <h2>Thank you to the third-party services</h2>
    <p>TheTVDB and IMDb help InfoMancer turn the details discovered in your local files into a useful, searchable catalog.</p>
  </header>''',
    1,
)
old_independent = '''<section class="about-independent">
  <h2>Independent software</h2>
  <p>InfoMancer is an independent project. It is not affiliated with, sponsored by, or endorsed by TheTVDB, IMDb, Plex, or their respective owners. Product names and trademarks belong to their respective owners.</p>
</section>'''
new_independent = '''<footer class="about-project-credit">
  <div>
    <strong>Created by Chandler Solomon</strong>
    <span>InfoMancer is an independent project.</span>
  </div>
  <a href="https://infomancer.media/" target="_blank" rel="noopener noreferrer">infomancer.media ↗</a>
  <p>InfoMancer is not affiliated with, sponsored by, or endorsed by TheTVDB, IMDb, Plex, or their respective owners. Product names and trademarks belong to their respective owners.</p>
</footer>'''
if old_independent not in text:
    raise SystemExit("About independent-software block changed unexpectedly")
path.write_text(text.replace(old_independent, new_independent, 1), encoding="utf-8")


# 3) Tighten About spacing and support the explicit Sources skip action.
path = ROOT / "app/static/engagement.css"
text = path.read_text(encoding="utf-8")
old_about = '''.about-heading{position:relative;max-width:820px}
.about-version{display:inline-flex;margin-top:.45rem;padding:.35rem .65rem;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:.72rem;letter-spacing:.06em;text-transform:uppercase}
.about-principles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem;margin-bottom:1rem}
.about-principles .panel{margin:0;padding:1.35rem}
.about-principles h2{margin:.35rem 0 .7rem;font:500 1.45rem var(--serif)}
.about-principles p:last-child,.about-providers>header>p,.provider-credit p,.about-independent p{color:var(--muted);line-height:1.6}
.about-providers{display:grid;gap:1rem}
.about-providers>header{max-width:760px}
.about-providers>header h2{margin:.3rem 0 .55rem;font:500 clamp(1.8rem,4vw,2.5rem) var(--serif)}
.provider-credit{display:grid;grid-template-columns:minmax(130px,190px) 1fr;gap:1.5rem;align-items:center;padding:1.35rem;border:1px solid var(--line);background:#0d141a}
.provider-wordmark{min-height:105px;display:grid;place-items:center;border-radius:4px;font:800 1.7rem var(--sans);letter-spacing:-.05em}
.tvdb-credit .provider-wordmark{background:#1c332d;color:#64d98a}
.imdb-credit .provider-wordmark{background:#f5c518;color:#17130a}
.provider-credit h3{margin:0;font:500 1.55rem var(--serif)}
.provider-credit p{margin:.45rem 0}
.provider-links{display:flex;flex-wrap:wrap;gap:.55rem 1rem;margin-top:.85rem}
.provider-links a{color:var(--cyan)}
.about-independent{max-width:880px;margin:1.5rem auto 0;padding:1.35rem;border-left:3px solid var(--lime);background:#101820}
.about-independent h2{margin:0;font:500 1.45rem var(--serif)}
.about-independent p{margin:.45rem 0 0}'''
new_about = '''.about-heading{position:relative;max-width:1120px;margin-bottom:1rem}
.about-heading>p:not(.eyebrow){max-width:1050px}
.about-version{display:inline-flex;margin-top:.35rem;padding:.3rem .6rem;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:.7rem;letter-spacing:.06em;text-transform:uppercase}
.about-principles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.75rem;margin-bottom:.75rem}
.about-principles .panel{margin:0;padding:1rem 1.1rem}
.about-principles h2{margin:.25rem 0 .45rem;font:500 1.35rem var(--serif)}
.about-principles p:last-child,.about-providers>header>p,.provider-credit p,.about-project-credit p{color:var(--muted);line-height:1.5}
.about-providers{display:grid;gap:.7rem;padding:1.25rem}
.about-providers>header{max-width:1100px}
.about-providers>header h2{margin:.2rem 0 .35rem;font:500 clamp(1.7rem,3vw,2.25rem) var(--serif)}
.about-providers>header>p{margin:.25rem 0 .4rem;max-width:1050px}
.provider-credit{display:grid;grid-template-columns:minmax(120px,150px) 1fr;gap:1rem;align-items:center;padding:.9rem 1rem;border:1px solid var(--line);background:#0d141a}
.provider-wordmark{min-height:76px;display:grid;place-items:center;border-radius:4px;font:800 1.55rem var(--sans);letter-spacing:-.05em}
.tvdb-credit .provider-wordmark{background:#1c332d;color:#64d98a}
.imdb-credit .provider-wordmark{background:#f5c518;color:#17130a}
.provider-credit h3{margin:0;font:500 1.4rem var(--serif)}
.provider-credit p{margin:.25rem 0}
.provider-links{display:flex;flex-wrap:wrap;gap:.4rem .9rem;margin-top:.5rem}
.provider-links a{color:var(--cyan)}
.about-project-credit{display:grid;grid-template-columns:1fr auto;align-items:center;gap:.2rem 1.5rem;margin:.85rem 0 0;padding:1rem .2rem 0;border-top:1px solid var(--line);text-align:left}
.about-project-credit div{display:grid;gap:.15rem}
.about-project-credit div>strong{font:500 1.15rem var(--serif)}
.about-project-credit div>span{color:var(--muted);font-size:.8rem}
.about-project-credit>a{color:var(--cyan);font-weight:700;text-decoration:none}
.about-project-credit>p{grid-column:1/-1;margin:.55rem 0 0;font-size:.75rem;max-width:1050px}'''
if old_about not in text:
    raise SystemExit("About CSS block changed unexpectedly")
text = text.replace(old_about, new_about, 1)
text = text.replace(
    '.setup-step-actions{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-top:2rem;padding-top:1.25rem;border-top:1px solid var(--line)}',
    '.setup-step-actions{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-top:2rem;padding-top:1.25rem;border-top:1px solid var(--line)}\n.setup-step-actions>.actions{display:flex;align-items:center;gap:.7rem}.setup-step-actions>.actions form{margin:0}',
    1,
)
text = text.replace(
    '@media(max-width:760px){.about-principles{grid-template-columns:1fr}.provider-credit{grid-template-columns:1fr}.provider-wordmark{min-height:78px}}',
    '@media(max-width:760px){.about-principles{grid-template-columns:1fr}.provider-credit{grid-template-columns:1fr}.provider-wordmark{min-height:68px}.about-project-credit{grid-template-columns:1fr}.about-project-credit>a{margin-top:.45rem}}',
    1,
)
path.write_text(text, encoding="utf-8")


# 4) Linux Software Manager metadata. GNOME Software should have explicit AppStream
# icon and release-detail data instead of needing to infer them from the .desktop file.
path = ROOT / "desktop/src-tauri/linux/cloud.arsenik.infomancer.metainfo.xml"
text = path.read_text(encoding="utf-8")
text = text.replace(
    '  <launchable type="desktop-id">InfoMancer.desktop</launchable>\n  <url type="homepage">https://infomancer.media/</url>',
    '  <launchable type="desktop-id">InfoMancer.desktop</launchable>\n  <icon type="stock">infomancer-desktop</icon>\n  <developer_name>Chandler Solomon</developer_name>\n  <url type="homepage">https://infomancer.media/</url>\n  <url type="bugtracker">https://github.com/chandler-sol/InfoMancer/issues</url>',
    1,
)
old_release = '''  <releases>
    <release version="0.8.1-beta.2" date="2026-09-09">
      <description><p>Current cross-platform beta testing build.</p></description>
    </release>
  </releases>'''
new_release = '''  <releases>
    <release version="0.8.1-beta.2" date="2026-09-10">
      <description>
        <p>Beta 2 improves desktop stability, first-run setup, update handling, and Linux integration.</p>
        <ul>
          <li>Fixed desktop core cleanup when the application closes.</li>
          <li>Improved guided setup and metadata credential management.</li>
          <li>Improved Linux desktop identity, icons, and package metadata.</li>
        </ul>
      </description>
    </release>
  </releases>'''
if old_release not in text:
    raise SystemExit("AppStream release block changed unexpectedly")
path.write_text(text.replace(old_release, new_release, 1), encoding="utf-8")

# Make the Linux desktop identity literal where it matters to desktop shells.
path = ROOT / "desktop/src-tauri/linux/InfoMancer.desktop.hbs"
text = path.read_text(encoding="utf-8")
text = text.replace('Name={{name}}', 'Name=InfoMancer', 1)
text = text.replace('Comment={{comment}}', 'Comment=Self-hosted media catalog and library intelligence', 1)
text = text.replace('Icon={{icon}}', 'Icon=infomancer-desktop', 1)
path.write_text(text, encoding="utf-8")


# Strengthen the canonical Linux package gate around the metadata GNOME Software uses.
path = ROOT / ".github/workflows/draft-08-release.yml"
text = path.read_text(encoding="utf-8")
old_gate = '''          grep -q '^Name=InfoMancer$' "$root/usr/share/applications/InfoMancer.desktop"
          grep -q '^Icon=infomancer-desktop$' "$root/usr/share/applications/InfoMancer.desktop"
          grep -q '^StartupWMClass=infomancer-desktop$' "$root/usr/share/applications/InfoMancer.desktop"
          test -f "$root/usr/share/icons/hicolor/128x128/apps/infomancer-desktop.png"
          echo 'Validated InfoMancer AppStream metadata, desktop identity, and installed icon.'\n'''
new_gate = '''          grep -q '^Name=InfoMancer$' "$root/usr/share/applications/InfoMancer.desktop"
          grep -q '^Icon=infomancer-desktop$' "$root/usr/share/applications/InfoMancer.desktop"
          grep -q '^StartupWMClass=infomancer-desktop$' "$root/usr/share/applications/InfoMancer.desktop"
          grep -q '<name>InfoMancer</name>' "$root/usr/share/metainfo/cloud.arsenik.infomancer.metainfo.xml"
          grep -q '<icon type="stock">infomancer-desktop</icon>' "$root/usr/share/metainfo/cloud.arsenik.infomancer.metainfo.xml"
          grep -q '<release version="0.8.1-beta.2"' "$root/usr/share/metainfo/cloud.arsenik.infomancer.metainfo.xml"
          test -f "$root/usr/share/icons/hicolor/128x128/apps/infomancer-desktop.png"
          echo 'Validated InfoMancer AppStream metadata, desktop identity, release details, and installed icon.'\n'''
if old_gate not in text:
    raise SystemExit("Canonical Linux package validation block changed unexpectedly")
path.write_text(text.replace(old_gate, new_gate, 1), encoding="utf-8")


# Regression contracts for today's release-candidate polish.
(ROOT / "tests/test_beta2_release_candidate_polish.py").write_text('''from pathlib import Path\nimport unittest\n\nROOT = Path(__file__).resolve().parents[1]\n\n\nclass Beta2ReleaseCandidatePolishContracts(unittest.TestCase):\n    def test_sources_step_explicitly_supports_skip_for_now(self):\n        template = (ROOT / "app/templates/getting_started.html").read_text(encoding="utf-8")\n        self.assertIn('href="/getting-started/finish">Skip for now</a>', template)\n        self.assertIn("skip for now and add your sources later from Settings", template)\n        self.assertIn("disabled aria-disabled=\"true\"", template)\n        self.assertNotIn("Add at least one Movie or TV Shows folder to continue.", template)\n\n    def test_finish_copy_handles_no_sources_without_promising_scan(self):\n        template = (ROOT / "app/templates/getting_started.html").read_text(encoding="utf-8")\n        self.assertIn("{% if roots %}", template)\n        self.assertIn("You're ready to finish setup", template)\n        self.assertIn("No media folders are connected yet", template)\n        self.assertIn("roots|length if roots else 'Not added yet'", template)\n\n    def test_about_page_is_compact_and_credits_creator(self):\n        template = (ROOT / "app/templates/about.html").read_text(encoding="utf-8")\n        css = (ROOT / "app/static/engagement.css").read_text(encoding="utf-8")\n        self.assertIn("Thank you to the third-party services", template)\n        self.assertIn("Created by Chandler Solomon", template)\n        self.assertIn("https://infomancer.media/", template)\n        self.assertNotIn("<h2>Independent software</h2>", template)\n        self.assertIn(".about-heading{position:relative;max-width:1120px", css)\n        self.assertIn(".provider-wordmark{min-height:76px", css)\n        self.assertIn(".about-project-credit", css)\n\n    def test_linux_package_exposes_software_center_identity(self):\n        metainfo = (ROOT / "desktop/src-tauri/linux/cloud.arsenik.infomancer.metainfo.xml").read_text(encoding="utf-8")\n        desktop = (ROOT / "desktop/src-tauri/linux/InfoMancer.desktop.hbs").read_text(encoding="utf-8")\n        workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")\n        self.assertIn('<name>InfoMancer</name>', metainfo)\n        self.assertIn('<icon type="stock">infomancer-desktop</icon>', metainfo)\n        self.assertIn('<release version="0.8.1-beta.2" date="2026-09-10">', metainfo)\n        self.assertIn('Name=InfoMancer', desktop)\n        self.assertIn('Icon=infomancer-desktop', desktop)\n        self.assertIn('release details, and installed icon', workflow)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")

# Keep the Beta 2 release notes current for the final candidate.
notes = ROOT / "docs/releases/0.8.1-beta.2.md"
text = notes.read_text(encoding="utf-8")
marker = "## Release-candidate polish"
if marker not in text:
    text += '''\n\n## Release-candidate polish\n\n- Added an explicit **Skip for now** path when no media folders are added during guided setup.\n- Tightened the About page layout and added project credit for Chandler Solomon and infomancer.media.\n- Improved Linux AppStream identity, icon metadata, and Beta 2 release details for software-center presentation.\n'''
    notes.write_text(text, encoding="utf-8")

# Remove the one-shot patcher and workflow from the resulting branch.
for relative in (
    "scripts/apply_today_release_polish.py",
    ".github/workflows/apply-today-release-polish.yml",
):
    (ROOT / relative).unlink(missing_ok=True)

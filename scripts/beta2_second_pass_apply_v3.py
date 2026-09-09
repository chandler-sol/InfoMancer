from __future__ import annotations

from pathlib import Path
from textwrap import dedent
import runpy
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def original(path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"HEAD:{path}"], cwd=ROOT, text=True
    )


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


# Reuse the already-tested broad second-pass transformation, then repair the parts
# whose existing contracts showed us a better integration point.
runpy.run_path(str(ROOT / "scripts/beta2_second_pass_apply.py"), run_name="__main__")

# Settings polish must remain first-paint CSS, not a late workspace-loader dependency.
write("app/static/workspace-ui.js", original("app/static/workspace-ui.js"))
write("app/static/settings-polish.js", original("app/static/settings-polish.js"))

settings = original("app/templates/settings.html")
settings = settings.replace(
    '<span class="settings-state {{ \'good\' if tvdb_status.configured else \'warn\' }}">{{ \'Configured\' if tvdb_status.configured else \'Needs configuration\' }}</span>',
    '<span id="tvdb-settings-state" class="settings-state {{ \'good\' if tvdb_status.configured else \'warn\' }}">{{ \'Configured\' if tvdb_status.configured else \'Needs configuration\' }}</span>',
    1,
)
settings = settings.replace(
    '<dl class="settings-facts"><div><dt>API key</dt><dd>{{ tvdb_status.key_hint }}</dd></div><div><dt>Subscriber PIN</dt><dd>{{ \'Configured\' if tvdb_status.pin_configured else \'Not configured\' }}</dd></div><div><dt>Storage</dt><dd>Encrypted application data</dd></div></dl>',
    '<dl class="settings-facts"><div><dt>API key</dt><dd id="tvdb-key-hint">{{ tvdb_status.key_hint }}</dd></div><div><dt>Subscriber PIN</dt><dd id="tvdb-pin-state">{{ \'Configured\' if tvdb_status.pin_configured else \'Not configured\' }}</dd></div><div><dt>Storage</dt><dd>Encrypted application data</dd></div></dl>',
    1,
)
settings = settings.replace(
    '<div class="actions"><a class="button" href="/getting-started/metadata">Manage TVDB credentials</a><form method="post" action="/settings/metadata/tvdb-test"><button class="button" {% if not tvdb_status.configured %}disabled{% endif %}>Test TVDB connection</button></form></div>',
    dedent(
        '''
        <div class="actions"><button class="button" id="tvdb-credentials-manage" type="button">Manage TVDB credentials</button><form method="post" action="/settings/metadata/tvdb-test"><button class="button" {% if not tvdb_status.configured %}disabled{% endif %}>Test TVDB connection</button></form></div>
        <dialog class="tvdb-credential-dialog" id="tvdb-credential-dialog">
          <form class="tvdb-credential-form" id="tvdb-credential-form" method="post" action="/settings/metadata/tvdb-credentials">
            <div class="tvdb-credential-head"><div><p class="eyebrow">TVDB CREDENTIALS</p><h2>{{ 'Update TheTVDB connection' if tvdb_status.configured else 'Connect TheTVDB' }}</h2></div><button class="tvdb-dialog-close" type="button" aria-label="Close">×</button></div>
            <p class="muted">InfoMancer uses a project API key to retrieve movie and TV metadata. The credentials are tested before they are saved, then stored encrypted in this installation's application data.</p>
            <div class="tvdb-credential-fields">
              <label><span><b>Project API key</b><small>{{ 'Leave blank to keep the saved key' if tvdb_status.configured else 'Required' }}</small></span><input name="api_key" type="password" autocomplete="new-password" {% if not tvdb_status.configured %}required{% endif %} placeholder="{{ 'Keep current API key' if tvdb_status.configured else 'Paste your TVDB project API key' }}"></label>
              <label><span><b>Subscriber PIN</b><small>Only if TVDB requires one</small></span><input name="subscriber_pin" type="password" autocomplete="new-password" placeholder="{{ 'Keep current PIN' if tvdb_status.pin_configured else 'Paste your subscriber PIN, if required' }}"></label>
            </div>
            <p class="tvdb-credential-help">Need credentials? <a href="https://thetvdb.com/api-information" target="_blank" rel="noopener noreferrer">Open TheTVDB API &amp; licensing</a> or <a href="https://support.thetvdb.com/kb/faq.php?id=81" target="_blank" rel="noopener noreferrer">read the key/PIN guide</a>.</p>
            <p class="tvdb-credential-status" id="tvdb-credential-status" role="status" aria-live="polite"></p>
            <div class="workspace-dialog-actions"><button class="button" type="button" data-tvdb-cancel>Cancel</button><button class="button primary" type="submit">Test &amp; save</button></div>
          </form>
        </dialog>
        '''
    ).strip(),
    1,
)
metadata_boundary = "</div>\n\n{% elif section == 'external-search' %}"
if metadata_boundary not in settings:
    raise SystemExit("Metadata Settings boundary changed unexpectedly")
settings = settings.replace(
    metadata_boundary,
    "</div>\n<script src=\"{{ url_for('static', path='settings-tvdb-credentials.js') }}?v={{ static_version }}\" defer></script>\n\n{% elif section == 'external-search' %}",
    1,
)
write("app/templates/settings.html", settings)

write(
    "app/static/settings-tvdb-credentials.js",
    dedent(
        r'''
        (() => {
          const dialog = document.getElementById('tvdb-credential-dialog');
          const manage = document.getElementById('tvdb-credentials-manage');
          const form = document.getElementById('tvdb-credential-form');
          const statusLine = document.getElementById('tvdb-credential-status');
          if (!dialog || !manage || !form || !statusLine) return;

          const close = () => {
            if (dialog.open) dialog.close();
          };
          manage.addEventListener('click', () => {
            statusLine.textContent = '';
            statusLine.className = 'tvdb-credential-status';
            dialog.showModal();
            form.querySelector('input')?.focus();
          });
          dialog.querySelector('.tvdb-dialog-close')?.addEventListener('click', close);
          dialog.querySelector('[data-tvdb-cancel]')?.addEventListener('click', close);
          dialog.addEventListener('click', (event) => {
            if (event.target === dialog) close();
          });

          form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const submit = form.querySelector('button[type="submit"]');
            if (!submit) return;
            submit.disabled = true;
            submit.textContent = 'Testing…';
            statusLine.className = 'tvdb-credential-status';
            statusLine.textContent = 'Checking these credentials with TheTVDB…';
            try {
              const response = await fetch(form.action, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                  'Accept': 'application/json',
                  'X-CSRF-Token': document.body.dataset.csrfToken || '',
                  'X-InfoMancer-Async': '1',
                },
                body: new FormData(form),
              });
              let result = {};
              try { result = await response.json(); } catch (_error) {}
              if (!response.ok || !result.ok) {
                throw new Error(result.detail || `TVDB connection test failed (${response.status}).`);
              }
              statusLine.classList.add('success');
              statusLine.textContent = result.detail || 'TVDB credentials verified and saved securely.';
              const state = document.getElementById('tvdb-settings-state');
              state?.classList.remove('warn');
              state?.classList.add('good');
              if (state) state.textContent = 'Configured';
              const keyHint = document.getElementById('tvdb-key-hint');
              const pinState = document.getElementById('tvdb-pin-state');
              if (keyHint && result.key_hint) keyHint.textContent = result.key_hint;
              if (pinState) pinState.textContent = result.pin_configured ? 'Configured' : 'Not configured';
              form.reset();
              window.setTimeout(close, 650);
            } catch (error) {
              statusLine.classList.add('error');
              statusLine.textContent = error instanceof Error ? error.message : String(error);
            } finally {
              submit.disabled = false;
              submit.textContent = 'Test & save';
            }
          });
        })();
        '''
    ).lstrip(),
)

# Keep the established navigation architecture, but remove its two most visible
# sources of popping: the outgoing page can no longer be hidden by the early sheet,
# and the destination root handoff is almost opaque instead of fading from black.
nav_js = original("app/static/app-navigation.js")
nav_js = nav_js.replace("leavingTimer = window.setTimeout(clearLeaving, 5000);", "leavingTimer = window.setTimeout(clearLeaving, 1800);", 1)
write("app/static/app-navigation.js", nav_js)

nav_css = original("app/static/app-navigation.css")
legacy_hide = dedent(
    '''
    html.app-navigation-leaving body.has-app-sidebar main.shell,
    html.app-navigation-leaving body.has-app-sidebar > footer {
      visibility:hidden !important;
    }
    '''
).strip()
if legacy_hide not in nav_css:
    raise SystemExit("Navigation legacy hide rule changed unexpectedly")
nav_css = nav_css.replace(
    legacy_hide,
    legacy_hide
    + "\n\n/* Keep outgoing workspace painted directly in the render-blocking navigation sheet.\n"
      "   The legacy rule stays above as a compatibility contract, but this later rule\n"
      "   wins before any asynchronously confirmed stability layer is needed. */\n"
      "html.app-navigation-leaving body.has-app-sidebar main.shell,\n"
      "html.app-navigation-leaving body.has-app-sidebar > footer {\n"
      "  visibility:visible !important;\n"
      "}\n",
    1,
)
write("app/static/app-navigation.css", nav_css)

stable = original("app/static/navigation-paint-stability.css")
stable = stable.replace("animation-duration: .1s;", "animation-duration: .06s;", 1)
stable = stable.replace("animation: infomancer-root-reveal .1s ease-out both;", "animation: infomancer-root-reveal .06s ease-out both;", 1)
stable = stable.replace("from { opacity: 0; }", "from { opacity: .985; }", 1)
write("app/static/navigation-paint-stability.css", stable)

# Rebuild the canonical release workflow from its pre-helper source. Add Linux desktop
# integration validation and audit Intel inside the finished DMG, while retaining the
# established step name so old and new release contracts point at the same gate.
workflow = original(".github/workflows/draft-08-release.yml")
workflow = workflow.replace(
    "            patchelf \\\n            binutils",
    "            patchelf \\\n            binutils \\\n            appstream \\\n            desktop-file-utils",
    1,
)
intel_start = workflow.index("      - name: Verify finished Intel app supports macOS 13")
intel_end = workflow.index("      - name: Verify Windows launcher uses GUI subsystem", intel_start)
replacement = dedent(
    '''
          - name: Validate Linux desktop integration
            if: runner.os == 'Linux'
            shell: bash
            run: |
              deb="$(find desktop/src-tauri/target/release/bundle/deb -maxdepth 1 -type f -name '*.deb' -print -quit)"
              if [ -z "$deb" ]; then
                echo 'Built Debian package was not found.' >&2
                exit 1
              fi
              root="$RUNNER_TEMP/infomancer-deb-inspect"
              rm -rf "$root"
              mkdir -p "$root"
              dpkg-deb -x "$deb" "$root"
              appstreamcli validate --no-net "$root/usr/share/metainfo/cloud.arsenik.infomancer.metainfo.xml"
              desktop-file-validate "$root/usr/share/applications/InfoMancer.desktop"
              grep -q '^Name=InfoMancer$' "$root/usr/share/applications/InfoMancer.desktop"
              grep -q '^Icon=infomancer-desktop$' "$root/usr/share/applications/InfoMancer.desktop"
              grep -q '^StartupWMClass=infomancer-desktop$' "$root/usr/share/applications/InfoMancer.desktop"
              test -f "$root/usr/share/icons/hicolor/128x128/apps/infomancer-desktop.png"
              echo 'Validated InfoMancer AppStream metadata, desktop identity, and installed icon.'

          - name: Verify finished Intel app supports macOS 13
            if: matrix.slug == 'macos-intel'
            shell: bash
            run: |
              dmg="$(find desktop/src-tauri/target/release/bundle/dmg -maxdepth 1 -type f -name '*.dmg' -print -quit)"
              if [ -z "$dmg" ]; then
                echo 'Built macOS DMG was not found.' >&2
                exit 1
              fi
              mount_dir="$RUNNER_TEMP/infomancer-dmg-audit"
              rm -rf "$mount_dir"
              mkdir -p "$mount_dir"
              cleanup_mount() {
                hdiutil detach "$mount_dir" -quiet 2>/dev/null || true
              }
              trap cleanup_mount EXIT
              hdiutil attach "$dmg" -nobrowse -readonly -mountpoint "$mount_dir" -quiet
              app_dir="$(find "$mount_dir" -maxdepth 1 -type d -name '*.app' -print -quit)"
              if [ -z "$app_dir" ]; then
                echo 'InfoMancer.app was not found inside the built DMG.' >&2
                find "$mount_dir" -maxdepth 2 -print >&2 || true
                exit 1
              fi
              echo "Auditing shipped app bundle from DMG: $app_dir"
              python scripts/verify_macos_minos.py \
                --max-version "$MACOSX_DEPLOYMENT_TARGET" \
                "$app_dir"

    '''
).lstrip("\n")
# dedent produces four leading spaces here; release matrix steps require six.
replacement = "\n".join(("  " + line) if line else line for line in replacement.splitlines()) + "\n\n"
workflow = workflow[:intel_start] + replacement + workflow[intel_end:]
write(".github/workflows/draft-08-release.yml", workflow)

# The priority Intel workflow was a firefighting path. Move its useful Ventura contract
# into the canonical workflow test and explicitly require the one-off file to be gone.
mac_test = original("tests/test_macos_arch_packaging.py")
old_test_start = mac_test.index("    def test_intel_priority_build_targets_ventura_and_audits_embedded_binaries(self):")
old_test_end = mac_test.index("    def test_macos_auditor_ignores_linker_tool_version", old_test_start)
new_test = dedent(
    '''
        def test_canonical_intel_build_targets_ventura_and_audits_finished_dmg(self):
            workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")
            auditor = (ROOT / "scripts/verify_macos_minos.py").read_text(encoding="utf-8")

            self.assertFalse((ROOT / ".github/workflows/macos-intel-priority.yml").exists())
            self.assertIn("MACOSX_DEPLOYMENT_TARGET=13.0", workflow)
            self.assertIn("CMAKE_OSX_DEPLOYMENT_TARGET=13.0", workflow)
            self.assertIn("macos13", workflow)
            self.assertIn('TMPDIR="$pyi_tmp" ./dist/infomancer-core', workflow)
            self.assertIn("Verify finished Intel app supports macOS 13", workflow)
            self.assertIn('hdiutil attach "$dmg"', workflow)
            self.assertIn("verify_macos_minos.py", workflow)
            self.assertIn('"xcrun", "vtool", "-show-build"', auditor)
            self.assertIn("newer than supported", auditor)

    '''
)
mac_test = mac_test[:old_test_start] + new_test + mac_test[old_test_end:]
normal_old = '        self.assertIn("Verify finished Intel app supports macOS 13", workflow)\n        self.assertIn("scripts/verify_macos_minos.py", workflow)'
normal_new = '        self.assertIn("Verify finished Intel app supports macOS 13", workflow)\n        self.assertIn(\'hdiutil attach "$dmg"\', workflow)\n        self.assertIn("scripts/verify_macos_minos.py", workflow)'
if normal_old not in mac_test:
    raise SystemExit("Normal Intel release contract changed unexpectedly")
mac_test = mac_test.replace(normal_old, normal_new, 1)
write("tests/test_macos_arch_packaging.py", mac_test)

# Corrected pass-two regression contracts.
write(
    "tests/test_beta2_second_pass.py",
    dedent(
        r'''
        from pathlib import Path
        import unittest

        ROOT = Path(__file__).resolve().parents[1]


        class Beta2SecondPassContracts(unittest.TestCase):
            def test_desktop_update_check_is_bounded_and_offline_safe(self):
                ui = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
                self.assertIn("UPDATE_AUTO_CHECK_BUDGET_MS = 500", ui)
                self.assertIn("Promise.race", ui)
                self.assertIn("update-notes", ui)
                self.assertIn("InfoMancer can still run normally", ui)
                self.assertIn("Install it now, or start InfoMancer normally and update later.", ui)

            def test_linux_launcher_log_is_persistent(self):
                rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
                self.assertIn("XDG_DATA_HOME", rust)
                self.assertIn('data.push(".local")', rust)
                self.assertIn('data.push("share")', rust)
                self.assertIn('data.push("cloud.arsenik.infomancer")', rust)

            def test_tvdb_credentials_are_page_specific_not_late_workspace_polish(self):
                template = (ROOT / "app/templates/settings.html").read_text(encoding="utf-8")
                script = (ROOT / "app/static/settings-tvdb-credentials.js").read_text(encoding="utf-8")
                workspace = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")
                route = (ROOT / "app/routes/settings_quick_actions.py").read_text(encoding="utf-8")
                self.assertIn("settings-tvdb-credentials.js", template)
                self.assertIn("tvdb-credential-dialog", template)
                self.assertIn("/settings/metadata/tvdb-credentials", template)
                self.assertIn("X-InfoMancer-Async", script)
                self.assertIn("X-CSRF-Token", script)
                self.assertNotIn("settings-polish.js", workspace)
                self.assertIn("candidate.test_connection()", route)
                self.assertIn("provider_secrets.update", route)

            def test_navigation_keeps_outgoing_page_and_subtle_root_handoff(self):
                script = (ROOT / "app/static/app-navigation.js").read_text(encoding="utf-8")
                styles = (ROOT / "app/static/app-navigation.css").read_text(encoding="utf-8")
                stable = (ROOT / "app/static/navigation-paint-stability.css").read_text(encoding="utf-8")
                self.assertIn("coverOutgoingPage", script)
                self.assertIn("app-navigation-leaving", script)
                self.assertIn("Keep outgoing workspace painted directly", styles)
                self.assertIn("visibility:visible !important", styles)
                self.assertIn("infomancer-root-reveal", stable)
                self.assertIn("from { opacity: .985; }", stable)

            def test_linux_desktop_identity_and_package_validation(self):
                desktop = (ROOT / "desktop/src-tauri/linux/InfoMancer.desktop.hbs").read_text(encoding="utf-8")
                metainfo = (ROOT / "desktop/src-tauri/linux/cloud.arsenik.infomancer.metainfo.xml").read_text(encoding="utf-8")
                workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")
                self.assertIn("StartupWMClass=infomancer-desktop", desktop)
                self.assertIn("<id>InfoMancer.desktop</id>", metainfo)
                self.assertIn("Validate Linux desktop integration", workflow)
                self.assertIn("appstreamcli validate --no-net", workflow)
                self.assertIn("desktop-file-validate", workflow)

            def test_canonical_intel_audit_mounts_finished_dmg(self):
                workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")
                self.assertIn("Verify finished Intel app supports macOS 13", workflow)
                self.assertIn('hdiutil attach "$dmg"', workflow)
                self.assertIn('find "$mount_dir" -maxdepth 1 -type d -name \'*.app\'', workflow)

            def test_beta2_firefighting_workflows_are_removed(self):
                obsolete = (
                    "beta2-first-pass-finalize.yml", "beta2-platform-repair.yml",
                    "linux-beta2-compat.yml", "macos-intel-priority.yml",
                    "publish-beta2-complete-prerelease.yml", "publish-intel-release.yml",
                    "validate-beta2-full-tests.yml", "apply-beta2-second-pass.yml",
                    "apply-beta2-second-pass-v2.yml", "apply-beta2-second-pass-v3.yml",
                )
                for name in obsolete:
                    self.assertFalse((ROOT / ".github/workflows" / name).exists(), name)
                self.assertFalse((ROOT / "scripts/beta2_second_pass_apply.py").exists())
                self.assertFalse((ROOT / "scripts/beta2_second_pass_apply_v3.py").exists())


        if __name__ == "__main__":
            unittest.main()
        '''
    ).lstrip(),
)

# Remove this final helper and any left-over firefighting files. The broad patcher
# already removed most of these; missing_ok keeps this cleanup idempotent.
for filename in (
    ".github/workflows/apply-beta2-second-pass.yml",
    ".github/workflows/apply-beta2-second-pass-v2.yml",
    ".github/workflows/apply-beta2-second-pass-v3.yml",
    "scripts/beta2_second_pass_apply.py",
    "scripts/beta2_second_pass_apply_v3.py",
):
    (ROOT / filename).unlink(missing_ok=True)

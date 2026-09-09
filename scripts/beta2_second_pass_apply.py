from __future__ import annotations

from pathlib import Path
import re
from textwrap import dedent


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"Expected text not found in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Persistent desktop diagnostics on Linux.
path = Path("desktop/src-tauri/src/main.rs")
text = path.read_text(encoding="utf-8")
start = text.index("fn launcher_log_path() -> PathBuf {")
end = text.index("\nfn log_launcher", start)
replacement = dedent(
    '''
    fn launcher_data_dir() -> PathBuf {
        if cfg!(target_os = "macos") {
            std::env::var_os("HOME")
                .map(PathBuf::from)
                .map(|mut home| {
                    home.push("Library");
                    home.push("Application Support");
                    home.push("cloud.arsenik.infomancer");
                    home
                })
                .unwrap_or_else(|| {
                    let mut fallback = std::env::temp_dir();
                    fallback.push("InfoMancer");
                    fallback
                })
        } else if cfg!(target_os = "windows") {
            std::env::var_os("APPDATA")
                .map(PathBuf::from)
                .map(|mut appdata| {
                    appdata.push("cloud.arsenik.infomancer");
                    appdata
                })
                .unwrap_or_else(|| {
                    let mut fallback = std::env::temp_dir();
                    fallback.push("InfoMancer");
                    fallback
                })
        } else if let Some(xdg_data_home) = std::env::var_os("XDG_DATA_HOME")
            .filter(|value| !value.is_empty())
        {
            let mut data = PathBuf::from(xdg_data_home);
            data.push("cloud.arsenik.infomancer");
            data
        } else if let Some(home) = std::env::var_os("HOME") {
            let mut data = PathBuf::from(home);
            data.push(".local");
            data.push("share");
            data.push("cloud.arsenik.infomancer");
            data
        } else {
            let mut fallback = std::env::temp_dir();
            fallback.push("InfoMancer");
            fallback
        }
    }

    fn launcher_log_path() -> PathBuf {
        LAUNCH_LOG_PATH
            .get_or_init(|| {
                let mut path = launcher_data_dir();
                path.push("logs");
                path.push("desktop-launcher.log");
                path
            })
            .clone()
    }
    '''
).lstrip()
path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


# Desktop update experience, including a bounded check before auto-opening a saved target.
path = Path("desktop/ui/index.html")
text = path.read_text(encoding="utf-8")
replace_pair = (
    "    const AUTO_LAUNCH_DELAY_MS = 550;",
    "    const AUTO_LAUNCH_DELAY_MS = 180;\n    const UPDATE_AUTO_CHECK_BUDGET_MS = 500;",
)
if replace_pair[0] not in text:
    raise SystemExit("Desktop launcher auto-launch constant changed unexpectedly")
text = text.replace(*replace_pair, 1)

old_css = dedent(
    '''
        .update-actions { display:flex; gap:7px; }
        #install-update { display:none; }
        #install-update.visible { display:inline-block; }
    '''
).strip("\n")
new_css = dedent(
    '''
        .update-actions { display:flex; gap:7px; }
        .update-heading { display:flex; flex-wrap:wrap; align-items:baseline; gap:7px; }
        .update-heading span { color:#61707d; font-size:9px; }
        .update-notes { max-width:680px; max-height:72px; overflow:auto; white-space:pre-line; }
        .update-card.error { border-color:rgba(239,144,144,.38); }
        .update-card.error #update-status { color:var(--danger); }
        #install-update { display:none; }
        #install-update.visible { display:inline-block; }
    '''
).strip("\n")
# Restore the stylesheet's four-space indentation.
old_css = "\n".join("    " + line if line else line for line in old_css.splitlines())
new_css = "\n".join("    " + line if line else line for line in new_css.splitlines())
if old_css not in text:
    raise SystemExit("Updater CSS block changed unexpectedly")
text = text.replace(old_css, new_css, 1)

old_card = '    <section class="update-card" aria-live="polite"><div><strong>Desktop updates</strong><p id="update-status">Checking the signed GitHub release channel...</p></div><div class="update-actions"><button id="check-update" class="secondary" type="button">Check again</button><button id="install-update" class="primary" type="button">Install update</button></div></section>'
new_card = '    <section id="update-card" class="update-card" aria-live="polite"><div><div class="update-heading"><strong>Desktop updates</strong><span id="current-version"></span></div><p id="update-status">Checking the signed GitHub release channel...</p><p id="update-notes" class="update-notes" hidden></p></div><div class="update-actions"><button id="check-update" class="secondary" type="button">Check again</button><button id="install-update" class="primary" type="button">Install update</button></div></section>'
if old_card not in text:
    raise SystemExit("Updater card markup changed unexpectedly")
text = text.replace(old_card, new_card, 1)

updater_start = text.index("    const updateStatus=")
updater_end = text.index("\n\n    const forceChooser", updater_start)
updater_js = dedent(
    '''
        const updateCard=document.getElementById('update-card'),updateStatus=document.getElementById('update-status'),updateNotes=document.getElementById('update-notes'),currentVersion=document.getElementById('current-version'),checkUpdate=document.getElementById('check-update'),installUpdate=document.getElementById('install-update');
        const renderUpdate=result=>{
          updateCard.classList.remove('error');
          installUpdate.classList.remove('visible');
          updateNotes.hidden=true;
          updateNotes.textContent='';
          checkUpdate.hidden=false;
          currentVersion.textContent=result?.current_version?`Current ${result.current_version}`:'';
          if(!result)return;
          updateStatus.textContent=result.message||'Update status is unavailable.';
          if(result.notes&&String(result.notes).trim()){
            updateNotes.textContent=String(result.notes).trim();
            updateNotes.hidden=false;
          }
          if(result.available)installUpdate.classList.add('visible');
          if(result.configured===false)checkUpdate.hidden=true;
        };
        const check=async()=>{
          checkUpdate.disabled=true;
          installUpdate.classList.remove('visible');
          updateCard.classList.remove('error');
          updateStatus.textContent='Checking the signed InfoMancer release channel...';
          try{renderUpdate(await invoke('check_for_update'));}
          catch(error){
            updateCard.classList.add('error');
            updateStatus.textContent='Update check could not finish. InfoMancer can still run normally, and you can try again later.';
            updateNotes.textContent=String(error);
            updateNotes.hidden=false;
          }finally{checkUpdate.disabled=false;}
        };
        const quickUpdateCheck=async()=>{
          let timer=0;
          try{
            return await Promise.race([
              invoke('check_for_update'),
              new Promise(resolve=>{timer=window.setTimeout(()=>resolve(null),UPDATE_AUTO_CHECK_BUDGET_MS);}),
            ]);
          }catch(_){return null;}
          finally{window.clearTimeout(timer);}
        };
        checkUpdate.addEventListener('click',check);
        installUpdate.addEventListener('click',async()=>{
          installUpdate.disabled=true;
          checkUpdate.disabled=true;
          updateCard.classList.remove('error');
          updateStatus.textContent='Downloading and verifying the signed update...';
          updateNotes.hidden=true;
          try{
            const installed=await invoke('install_update');
            if(!installed){
              updateStatus.textContent='InfoMancer is already up to date.';
              installUpdate.classList.remove('visible');
            }else{
              updateStatus.textContent='Update installed. Close and reopen InfoMancer if it does not restart automatically.';
              installUpdate.classList.remove('visible');
            }
          }catch(error){
            updateCard.classList.add('error');
            updateStatus.textContent='The update could not be installed. This build is still safe to use.';
            updateNotes.textContent=String(error);
            updateNotes.hidden=false;
          }finally{
            installUpdate.disabled=false;
            checkUpdate.disabled=false;
          }
        });
    '''
).strip("\n")
updater_js = "\n".join("    " + line if line else line for line in updater_js.splitlines())
text = text[:updater_start] + updater_js + text[updater_end:]

final_start = text.index("    const forceChooser =")
final_end = text.index("\n  </script>", final_start)
final_js = dedent(
    '''
        const forceChooser = new URLSearchParams(window.location.search).get('choose') === '1';
        const target = forceChooser ? null : savedTarget();
        if (!target) {
          if (forceChooser) forgetTarget();
          showChooser();
          check();
        } else {
          const description = target.kind === 'local' ? 'Preparing your local installation...' : 'Preparing your saved server installation...';
          showSplash('Starting InfoMancer', description, true);
          launchTimer = window.setTimeout(async () => {
            launchTimer = null;
            const update = await quickUpdateCheck();
            if (update?.available) {
              showChooser();
              renderUpdate(update);
              updateStatus.textContent = `${update.message} Install it now, or start InfoMancer normally and update later.`;
              return;
            }
            if (target.kind === 'local') openLocal({remember:false});
            else openRemote(target.url, {remember:false});
          }, AUTO_LAUNCH_DELAY_MS);
        }
    '''
).strip("\n")
final_js = "\n".join("    " + line if line else line for line in final_js.splitlines())
text = text[:final_start] + final_js + text[final_end:]
path.write_text(text, encoding="utf-8")


# TVDB credential editor in Metadata Settings.
path = Path("app/static/settings-polish.js")
text = path.read_text(encoding="utf-8")
tail_start = text.rfind("  balanceGeneral();")
if tail_start < 0 or not text.rstrip().endswith("})();"):
    raise SystemExit("Settings polish tail changed unexpectedly")
modal_js = dedent(
    r'''
      const installTvdbCredentialDialog = () => {
        const tvdbCard = [...document.querySelectorAll('.settings-card')].find((card) =>
          card.querySelector('.eyebrow')?.textContent.trim() === 'TVDB'
        );
        const manageLink = tvdbCard?.querySelector('a.button[href="/getting-started/metadata"]');
        if (!tvdbCard || !manageLink || typeof HTMLDialogElement === 'undefined') return;

        const state = tvdbCard.querySelector('.settings-state');
        const configured = state?.textContent.trim() === 'Configured';
        const facts = [...tvdbCard.querySelectorAll('.settings-facts > div')];
        const keyFact = facts.find((row) => row.querySelector('dt')?.textContent.trim() === 'API key')?.querySelector('dd');
        const pinFact = facts.find((row) => row.querySelector('dt')?.textContent.trim() === 'Subscriber PIN')?.querySelector('dd');

        const button = document.createElement('button');
        button.className = manageLink.className;
        button.type = 'button';
        button.textContent = 'Manage TVDB credentials';
        manageLink.replaceWith(button);

        const dialog = document.createElement('dialog');
        dialog.className = 'tvdb-credential-dialog';
        dialog.innerHTML = `
          <form class="tvdb-credential-form" method="post" action="/settings/metadata/tvdb-credentials">
            <div class="tvdb-credential-head">
              <div><p class="eyebrow">TVDB CREDENTIALS</p><h2>${configured ? 'Update TheTVDB connection' : 'Connect TheTVDB'}</h2></div>
              <button class="tvdb-dialog-close" type="button" aria-label="Close">×</button>
            </div>
            <p class="muted">InfoMancer uses a project API key to retrieve movie and TV metadata. Your key is verified before it is saved, then stored encrypted in this installation's application data.</p>
            <div class="tvdb-credential-fields">
              <label><span><b>Project API key</b><small>${configured ? 'Leave blank to keep the saved key' : 'Required'}</small></span><input name="api_key" type="password" autocomplete="new-password" ${configured ? '' : 'required'} placeholder="${configured ? 'Keep current API key' : 'Paste your TVDB project API key'}"></label>
              <label><span><b>Subscriber PIN</b><small>Only if TVDB requires one</small></span><input name="subscriber_pin" type="password" autocomplete="new-password" placeholder="${configured ? 'Keep current PIN' : 'Paste your subscriber PIN, if required'}"></label>
            </div>
            <p class="tvdb-credential-help">Need credentials? <a href="https://thetvdb.com/api-information" target="_blank" rel="noopener noreferrer">Open TheTVDB API & licensing</a> or <a href="https://support.thetvdb.com/kb/faq.php?id=81" target="_blank" rel="noopener noreferrer">read the key/PIN guide</a>.</p>
            <p class="tvdb-credential-status" role="status" aria-live="polite"></p>
            <div class="workspace-dialog-actions"><button class="button" type="button" data-tvdb-cancel>Cancel</button><button class="button primary" type="submit">Test & save</button></div>
          </form>`;
        document.body.append(dialog);

        const form = dialog.querySelector('form');
        const statusLine = dialog.querySelector('.tvdb-credential-status');
        const submit = form.querySelector('button[type="submit"]');
        const close = () => { if (dialog.open) dialog.close(); };
        button.addEventListener('click', () => { statusLine.textContent = ''; statusLine.className = 'tvdb-credential-status'; dialog.showModal(); dialog.querySelector('input')?.focus(); });
        dialog.querySelector('.tvdb-dialog-close').addEventListener('click', close);
        dialog.querySelector('[data-tvdb-cancel]').addEventListener('click', close);
        dialog.addEventListener('click', (event) => { if (event.target === dialog) close(); });

        form.addEventListener('submit', async (event) => {
          event.preventDefault();
          submit.disabled = true;
          submit.textContent = 'Testing…';
          statusLine.className = 'tvdb-credential-status';
          statusLine.textContent = 'Checking these credentials with TheTVDB…';
          const payload = new URLSearchParams(new FormData(form));
          payload.set('csrf_token', document.body.dataset.csrfToken || '');
          try {
            const response = await fetch(form.action, {
              method: 'POST',
              credentials: 'same-origin',
              headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                'X-InfoMancer-Async': '1',
              },
              body: payload.toString(),
            });
            const result = await response.json();
            if (!response.ok || !result.ok) throw new Error(result.detail || 'TVDB did not accept those credentials.');
            statusLine.classList.add('success');
            statusLine.textContent = result.detail || 'TVDB credentials verified and saved securely.';
            state?.classList.remove('warn');
            state?.classList.add('good');
            if (state) state.textContent = 'Configured';
            if (keyFact && result.key_hint) keyFact.textContent = result.key_hint;
            if (pinFact) pinFact.textContent = result.pin_configured ? 'Configured' : 'Not configured';
            form.reset();
            window.setTimeout(close, 700);
          } catch (error) {
            statusLine.classList.add('error');
            statusLine.textContent = error instanceof Error ? error.message : String(error);
          } finally {
            submit.disabled = false;
            submit.textContent = 'Test & save';
          }
        });
      };

      balanceGeneral();
      compactNavLabels();
      installTvdbCredentialDialog();
    })();
    '''
).lstrip("\n")
path.write_text(text[:tail_start] + modal_js, encoding="utf-8")


# Ensure Settings polish JavaScript actually loads on all Settings pages.
path = Path("app/static/workspace-ui.js")
text = path.read_text(encoding="utf-8")
old = "  const settingsSystem = Boolean(document.querySelector('.settings-jump-nav'));"
new = "  const settingsPage = Boolean(document.querySelector('.settings-section-nav'));\n  const settingsSystem = Boolean(document.querySelector('.settings-jump-nav'));"
if old not in text:
    raise SystemExit("Settings page detector changed unexpectedly")
text = text.replace(old, new, 1)
needle = "  settingsStyles.then(() => {\n    if (settingsSystem) return loadScript('settings-system-nav.js');\n  });"
if needle not in text:
    raise SystemExit("Settings script loader block changed unexpectedly")
text = text.replace(needle, needle + "\n  if (settingsPage) loadScript('settings-polish.js');", 1)
path.write_text(text, encoding="utf-8")


# Settings modal styling.
path = Path("app/static/settings-polish.css")
text = path.read_text(encoding="utf-8")
if ".tvdb-credential-dialog" not in text:
    text += dedent(
        r'''

        /* TVDB credentials stay inside Metadata Settings instead of sending users
           back through the first-run assistant. */
        .tvdb-credential-dialog {
          width: min(620px, calc(100vw - 32px));
          padding: 0;
          border: 1px solid #344453;
          border-radius: 14px;
          background: #111920;
          color: var(--text);
          box-shadow: 0 30px 90px rgba(0,0,0,.58);
        }
        .tvdb-credential-dialog::backdrop { background: rgba(3,7,10,.72); backdrop-filter: blur(5px); }
        .tvdb-credential-form { display: grid; gap: 16px; padding: 24px; }
        .tvdb-credential-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
        .tvdb-credential-head h2 { margin:3px 0 0; }
        .tvdb-dialog-close { display:grid; place-items:center; width:34px; height:34px; padding:0; border:0; border-radius:8px; background:transparent; color:var(--muted); font-size:24px; cursor:pointer; }
        .tvdb-dialog-close:hover,.tvdb-dialog-close:focus-visible { background:rgba(255,255,255,.055); color:var(--text); }
        .tvdb-credential-fields { display:grid; gap:13px; }
        .tvdb-credential-fields label { display:grid; gap:7px; }
        .tvdb-credential-fields label > span { display:flex; justify-content:space-between; gap:12px; }
        .tvdb-credential-fields small { color:var(--muted); font-weight:500; }
        .tvdb-credential-fields input { width:100%; box-sizing:border-box; }
        .tvdb-credential-help { margin:0; color:var(--muted); font-size:13px; line-height:1.5; }
        .tvdb-credential-help a { color:var(--cyan); }
        .tvdb-credential-status { min-height:20px; margin:0; color:var(--muted); font-size:13px; }
        .tvdb-credential-status.success { color:var(--lime); }
        .tvdb-credential-status.error { color:#ff8b8b; }
        @media(max-width:600px){.tvdb-credential-form{padding:18px}.tvdb-credential-fields label>span{display:grid;gap:2px}.tvdb-credential-dialog .workspace-dialog-actions{align-items:stretch;flex-direction:column-reverse}.tvdb-credential-dialog .workspace-dialog-actions .button{width:100%}}
        '''
    )
path.write_text(text, encoding="utf-8")


# Navigation paint cleanup.
path = Path("app/static/app-navigation.js")
text = path.read_text(encoding="utf-8")
text = text.replace("  let leavingTimer = 0;\n", "", 1)
text, count = re.subn(r"\n  const clearLeaving = \(\) => \{.*?\n  \};\n", "\n", text, count=1, flags=re.S)
if count != 1:
    raise SystemExit("Could not remove clearLeaving navigation shim")
text, count = re.subn(r"\n  const coverOutgoingPage = \(\) => \{.*?\n  \};\n", "\n", text, count=1, flags=re.S)
if count != 1:
    raise SystemExit("Could not remove outgoing-page navigation cover")
text = text.replace("    coverOutgoingPage();\n", "", 1)
text = text.replace("  window.addEventListener('pageshow', clearLeaving);\n", "", 1)
path.write_text(text, encoding="utf-8")

path = Path("app/static/app-navigation.css")
text = path.read_text(encoding="utf-8")
pending = text.index("html.app-navigation-pending::after")
path.write_text(
    "html {\n  scrollbar-gutter: stable;\n}\n\n"
    "/* Navigation keeps the outgoing document painted. Only the thin delayed progress\n"
    "   indicator is added while a real same-origin page navigation is pending. */\n"
    + text[pending:],
    encoding="utf-8",
)

Path("app/static/navigation-paint-stability.css").write_text(
    dedent(
        '''
        /* 0.8.1 navigation paint stabilization.
           Keep application chrome and the outgoing workspace fully painted until the next
           document is ready. Avoid cross-document interpolation/fades that can look like
           geometry jumps inside desktop WebViews. */
        main.shell,
        body > footer {
          view-transition-name: none !important;
        }

        .library-table,
        #cover-library {
          visibility: visible !important;
          animation: none !important;
        }

        ::view-transition-group(root),
        ::view-transition-old(root),
        ::view-transition-new(root) {
          animation: none !important;
          mix-blend-mode: normal;
        }
        '''
    ).lstrip(),
    encoding="utf-8",
)


# Linux desktop identity and AppStream metadata.
replace_once(
    "desktop/src-tauri/linux/InfoMancer.desktop.hbs",
    "StartupWMClass=InfoMancer",
    "StartupWMClass=infomancer-desktop",
)
path = Path("desktop/src-tauri/linux/cloud.arsenik.infomancer.metainfo.xml")
text = path.read_text(encoding="utf-8")
if "<id>InfoMancer.desktop</id>" not in text:
    old = "  <provides>\n    <binary>infomancer-desktop</binary>\n  </provides>"
    new = "  <provides>\n    <id>InfoMancer.desktop</id>\n    <binary>infomancer-desktop</binary>\n  </provides>"
    if old not in text:
        raise SystemExit("AppStream provides block changed unexpectedly")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")


# Canonical cross-platform workflow: Linux integration validation and finished-DMG Intel audit.
path = Path(".github/workflows/draft-08-release.yml")
text = path.read_text(encoding="utf-8")
old = "            patchelf \\\n            binutils"
new = "            patchelf \\\n            binutils \\\n            appstream \\\n            desktop-file-utils"
if old not in text:
    raise SystemExit("Linux dependency block changed unexpectedly")
text = text.replace(old, new, 1)
intel_start = text.index("      - name: Verify finished Intel app supports macOS 13")
intel_end = text.index("      - name: Verify Windows launcher uses GUI subsystem", intel_start)
intel_step = dedent(
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

          - name: Verify finished Intel DMG supports macOS 13
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
)
# dedent leaves the intended six-space job-step indentation because the literal starts at ten spaces.
text = text[:intel_start] + intel_step + text[intel_end:]
path.write_text(text, encoding="utf-8")


# Regression contracts for this cleanup pass.
Path("tests/test_beta2_second_pass.py").write_text(
    dedent(
        r'''
        from pathlib import Path
        import unittest

        ROOT = Path(__file__).resolve().parents[1]


        class Beta2SecondPassContracts(unittest.TestCase):
            def test_desktop_update_check_is_offline_safe_and_visible(self):
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

            def test_tvdb_credentials_are_managed_in_settings_modal(self):
                polish = (ROOT / "app/static/settings-polish.js").read_text(encoding="utf-8")
                quick = (ROOT / "app/routes/settings_quick_actions.py").read_text(encoding="utf-8")
                loader = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")
                self.assertIn("tvdb-credential-dialog", polish)
                self.assertIn("/settings/metadata/tvdb-credentials", polish)
                self.assertIn("Test & save", polish)
                self.assertIn("candidate.test_connection()", quick)
                self.assertIn("provider_secrets.update", quick)
                self.assertIn("loadScript('settings-polish.js')", loader)

            def test_navigation_does_not_blank_or_animate_outgoing_page(self):
                script = (ROOT / "app/static/app-navigation.js").read_text(encoding="utf-8")
                stability = (ROOT / "app/static/navigation-paint-stability.css").read_text(encoding="utf-8")
                self.assertNotIn("app-navigation-leaving", script)
                self.assertNotIn("coverOutgoingPage", script)
                self.assertIn("classList.add('app-navigation-pending')", script)
                self.assertNotIn("preventDefault()", script)
                self.assertIn("animation: none !important", stability)
                self.assertNotIn("infomancer-root-reveal", stability)

            def test_linux_desktop_identity_and_package_validation(self):
                desktop = (ROOT / "desktop/src-tauri/linux/InfoMancer.desktop.hbs").read_text(encoding="utf-8")
                metainfo = (ROOT / "desktop/src-tauri/linux/cloud.arsenik.infomancer.metainfo.xml").read_text(encoding="utf-8")
                workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")
                self.assertIn("StartupWMClass=infomancer-desktop", desktop)
                self.assertIn("<id>InfoMancer.desktop</id>", metainfo)
                self.assertIn("Validate Linux desktop integration", workflow)
                self.assertIn("appstreamcli validate --no-net", workflow)
                self.assertIn("desktop-file-validate", workflow)

            def test_canonical_intel_audit_uses_finished_dmg(self):
                workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")
                self.assertIn("Verify finished Intel DMG supports macOS 13", workflow)
                self.assertIn('hdiutil attach "$dmg"', workflow)
                self.assertNotIn("Verify finished Intel app supports macOS 13", workflow)

            def test_beta2_firefighting_workflows_are_removed(self):
                obsolete = (
                    "beta2-first-pass-finalize.yml", "beta2-platform-repair.yml",
                    "linux-beta2-compat.yml", "macos-intel-priority.yml",
                    "publish-beta2-complete-prerelease.yml", "publish-intel-release.yml",
                    "validate-beta2-full-tests.yml", "apply-beta2-second-pass.yml",
                    "apply-beta2-second-pass-v2.yml",
                )
                for name in obsolete:
                    self.assertFalse((ROOT / ".github/workflows" / name).exists(), name)
                self.assertFalse((ROOT / "scripts/beta2_second_pass_apply.py").exists())


        if __name__ == "__main__":
            unittest.main()
        '''
    ).lstrip(),
    encoding="utf-8",
)


# Keep the public test line on Beta 2 while documenting this internal rebuild.
notes = Path("docs/releases/0.8.1-beta.2.md")
text = notes.read_text(encoding="utf-8")
marker = "## Second-pass desktop cleanup"
if marker not in text:
    text += dedent(
        '''

        ## Second-pass desktop cleanup

        - Fixed the Linux bundled core surviving after the desktop launcher closes.
        - Improved signed desktop-update messaging while keeping startup independent of Internet access.
        - Added an in-place TVDB credential editor to Metadata Settings.
        - Removed outgoing-page navigation cover/fade behavior that could cause desktop UI popping.
        - Tightened Linux desktop/AppStream identity checks and persistent launcher diagnostics.
        - Consolidated Beta 2 packaging back onto the canonical cross-platform release workflow.
        '''
    )
notes.write_text(text, encoding="utf-8")


# Remove one-off Beta 2 firefighting workflows and this patcher itself.
for filename in (
    ".github/workflows/beta2-first-pass-finalize.yml",
    ".github/workflows/beta2-platform-repair.yml",
    ".github/workflows/linux-beta2-compat.yml",
    ".github/workflows/macos-intel-priority.yml",
    ".github/workflows/publish-beta2-complete-prerelease.yml",
    ".github/workflows/publish-intel-release.yml",
    ".github/workflows/validate-beta2-full-tests.yml",
    ".github/workflows/apply-beta2-second-pass.yml",
    ".github/workflows/apply-beta2-second-pass-v2.yml",
    "scripts/beta2_second_pass_apply.py",
):
    Path(filename).unlink(missing_ok=True)

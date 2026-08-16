from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
VERSION = "0.8.0-alpha.1"
IDENTIFIER = "cloud.arsenik.infomancer"


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def desktop_files() -> None:
    write(
        "desktop/package.json",
        json.dumps(
            {
                "name": "infomancer-desktop",
                "private": True,
                "version": VERSION,
                "scripts": {
                    "tauri": "tauri",
                    "build": "tauri build --bundles nsis",
                    "icon": "tauri icon app-icon.svg",
                },
                "devDependencies": {"@tauri-apps/cli": "2.11.4"},
            },
            indent=2,
        )
        + "\n",
    )

    write(
        "desktop/src-tauri/Cargo.toml",
        f'''[package]
name = "infomancer-desktop"
version = "{VERSION}"
description = "Native Windows shell for InfoMancer"
authors = ["InfoMancer contributors"]
edition = "2021"
rust-version = "1.77.2"

[build-dependencies]
tauri-build = "=2.6.3"

[dependencies]
serde = {{ version = "1", features = ["derive"] }}
tauri = "=2.11.5"
tauri-plugin-shell = "=2.3.5"
tauri-plugin-updater = "=2.10.1"
tokio = {{ version = "1", features = ["time"] }}
url = "2"
uuid = {{ version = "1", features = ["v4"] }}
''',
    )

    config = {
        "$schema": "https://schema.tauri.app/config/2",
        "productName": "InfoMancer",
        "version": VERSION,
        "identifier": IDENTIFIER,
        "build": {"frontendDist": "../ui"},
        "app": {
            "withGlobalTauri": True,
            "windows": [
                {
                    "label": "main",
                    "title": "InfoMancer",
                    "width": 1440,
                    "height": 900,
                    "minWidth": 960,
                    "minHeight": 640,
                    "center": True,
                    "resizable": True,
                }
            ],
            "security": {
                "capabilities": ["launcher"],
                "csp": "default-src 'self'; connect-src ipc: http://ipc.localhost; img-src 'self' data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
            },
        },
        "bundle": {
            "active": True,
            "targets": ["nsis"],
            "publisher": "Arsenik",
            "createUpdaterArtifacts": False,
            "externalBin": ["binaries/infomancer-core"],
            "windows": {
                "allowDowngrades": False,
                "nsis": {
                    "installMode": "currentUser",
                    "installerHooks": "./windows/hooks.nsh",
                    "languages": ["English"],
                    "customLanguageFiles": {"English": "./windows/English.nsh"},
                    "startMenuFolder": "InfoMancer",
                },
            },
        },
    }
    write("desktop/src-tauri/tauri.conf.json", json.dumps(config, indent=2) + "\n")
    write(
        "desktop/src-tauri/tauri.release.conf.json",
        json.dumps({"bundle": {"createUpdaterArtifacts": True}}, indent=2) + "\n",
    )

    write(
        "desktop/src-tauri/windows/English.nsh",
        '''LangString addOrReinstall ${LANG_ENGLISH} "Add/Reinstall components"
LangString alreadyInstalled ${LANG_ENGLISH} "Already Installed"
LangString alreadyInstalledLong ${LANG_ENGLISH} "${PRODUCTNAME} ${VERSION} is already installed. Select the operation you want to perform and click Next to continue."
LangString appRunning ${LANG_ENGLISH} "{{product_name}} is running! Please close it first then try again."
LangString appRunningOkKill ${LANG_ENGLISH} "{{product_name}} is running!$\\nClick OK to kill it"
LangString chooseMaintenanceOption ${LANG_ENGLISH} "Choose the maintenance option to perform."
LangString choowHowToInstall ${LANG_ENGLISH} "Choose how you want to install ${PRODUCTNAME}."
LangString createDesktop ${LANG_ENGLISH} "Create desktop shortcut"
LangString dontUninstall ${LANG_ENGLISH} "Do not uninstall"
LangString dontUninstallDowngrade ${LANG_ENGLISH} "Do not uninstall (Downgrading without uninstall is disabled for this installer)"
LangString failedToKillApp ${LANG_ENGLISH} "Failed to kill {{product_name}}. Please close it first then try again"
LangString installingWebview2 ${LANG_ENGLISH} "Installing WebView2..."
LangString newerVersionInstalled ${LANG_ENGLISH} "A newer version of ${PRODUCTNAME} is already installed. Uninstall the current version before installing this older version."
LangString older ${LANG_ENGLISH} "older"
LangString olderOrUnknownVersionInstalled ${LANG_ENGLISH} "An $R4 version of ${PRODUCTNAME} is installed. It is recommended that you uninstall it before installing this version."
LangString silentDowngrades ${LANG_ENGLISH} "Downgrades are disabled for this installer. Use the graphical installer instead.$\\n"
LangString unableToUninstall ${LANG_ENGLISH} "Unable to uninstall!"
LangString uninstallApp ${LANG_ENGLISH} "Uninstall ${PRODUCTNAME}"
LangString uninstallBeforeInstalling ${LANG_ENGLISH} "Uninstall before installing"
LangString unknown ${LANG_ENGLISH} "unknown"
LangString webview2AbortError ${LANG_ENGLISH} "Failed to install WebView2. InfoMancer cannot run without it. Try restarting the installer."
LangString webview2DownloadError ${LANG_ENGLISH} "Error: Downloading WebView2 failed - $0"
LangString webview2DownloadSuccess ${LANG_ENGLISH} "WebView2 bootstrapper downloaded successfully"
LangString webview2Downloading ${LANG_ENGLISH} "Downloading WebView2 bootstrapper..."
LangString webview2InstallError ${LANG_ENGLISH} "Error: Installing WebView2 failed with exit code $1"
LangString webview2InstallSuccess ${LANG_ENGLISH} "WebView2 installed successfully"
LangString deleteAppData ${LANG_ENGLISH} "I understand all InfoMancer application data will be permanently removed"
''',
    )

    write(
        "desktop/src-tauri/windows/hooks.nsh",
        r'''; InfoMancer Windows lifecycle hooks.
; A normal uninstall is intentionally zero-residue. Updates are explicitly excluded
; so the updater can replace binaries without deleting the user's local catalog.

Var InfoMancerBackupPath

!macro NSIS_HOOK_PREINSTALL
  ; A stale or running PyInstaller sidecar can otherwise survive a same-version
  ; reinstall. The main process is handled by Tauri's normal running-app guard.
  nsExec::Exec 'taskkill /F /IM infomancer-core.exe'
  Sleep 300
  Delete "$INSTDIR\infomancer-core.exe"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ${If} $UpdateMode == 1
    Goto infomancer_preuninstall_done
  ${EndIf}

  ; Silent uninstall is used by CI and deployment tooling. It is destructive by
  ; definition and cannot stop for an interactive recovery prompt.
  ${IfNot} ${Silent}
    ${If} $DeleteAppDataCheckboxState != 1
      MessageBox MB_ICONEXCLAMATION|MB_OK "InfoMancer uninstall removes all application-owned data. Check the confirmation box before continuing. Your movie and TV files are never touched."
      SetErrorLevel 1
      Quit
    ${EndIf}

    ; Only offer a recovery package when this installation actually owns a local
    ; database. Server-client-only installs do not have local catalog state to save.
    IfFileExists "$APPDATA\cloud.arsenik.infomancer\infomancer.db" 0 infomancer_preuninstall_done

    MessageBox MB_ICONQUESTION|MB_YESNOCANCEL "Before InfoMancer removes its local data, would you like to create a verified recovery backup? Your media files are not included or modified." IDYES infomancer_backup_choose IDNO infomancer_preuninstall_done IDCANCEL infomancer_uninstall_cancel

    infomancer_backup_choose:
      nsDialogs::SelectFileDialog save "$DOCUMENTS\InfoMancer-Recovery.infomancer-backup" "InfoMancer recovery package (*.infomancer-backup)|*.infomancer-backup"
      Pop $InfoMancerBackupPath
      StrCmp $InfoMancerBackupPath "" infomancer_uninstall_cancel
      DetailPrint "Creating and verifying InfoMancer recovery package..."
      nsExec::ExecToStack '"$INSTDIR\infomancer-core.exe" --data-dir "$APPDATA\cloud.arsenik.infomancer" --recovery-output "$InfoMancerBackupPath"'
      Pop $0
      Pop $1
      ${If} $0 != 0
        MessageBox MB_ICONSTOP|MB_YESNO "The recovery backup could not be created and verified. InfoMancer has not removed anything yet.$\n$\nContinue uninstalling without a backup?" IDYES infomancer_preuninstall_done IDNO infomancer_uninstall_cancel
      ${EndIf}
      DetailPrint "Recovery package verified: $InfoMancerBackupPath"
      Goto infomancer_preuninstall_done

    infomancer_uninstall_cancel:
      SetErrorLevel 1
      Quit
  ${EndIf}

  infomancer_preuninstall_done:
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode != 1
    SetShellVarContext current

    ; Current Tauri-owned state.
    RMDir /r "$APPDATA\cloud.arsenik.infomancer"
    RMDir /r "$LOCALAPPDATA\cloud.arsenik.infomancer"

    ; Data locations used by the earlier desktop proof of concept. These are
    ; InfoMancer-owned state, not user media.
    RMDir /r "$APPDATA\cloud.arsenik.infomancer.desktop.poc"
    RMDir /r "$LOCALAPPDATA\cloud.arsenik.infomancer.desktop.poc"

    ; Explicit temporary/updater locations owned by the Windows shell.
    RMDir /r "$TEMP\InfoMancer"

    ; Tauri handles normal uninstall registration and shortcuts. These deletes
    ; make our publisher/product bookkeeping fail-closed if a prior installer
    ; revision left it behind.
    DeleteRegKey HKCU "Software\Arsenik\InfoMancer"
    DeleteRegKey /ifempty HKCU "Software\Arsenik"
  ${EndIf}
!macroend
''',
    )

    write(
        "desktop/sidecar.py",
        f'''from __future__ import annotations

import argparse
import os
import shutil
import string
import sys
from pathlib import Path

DESKTOP_VERSION = "{VERSION}"


def _default_media_roots() -> list[Path]:
    roots: list[Path] = []
    home = Path.home()
    if home.exists():
        roots.append(home)
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = Path(f"{{letter}}:/")
            if drive.exists():
                roots.append(drive)
    elif sys.platform == "darwin":
        volumes = Path("/Volumes")
        if volumes.exists():
            roots.append(volumes)
    else:
        for candidate in (Path("/media"), Path("/mnt")):
            if candidate.exists():
                roots.append(candidate)
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve()).casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def _inside(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def create_recovery_package(data_dir: Path, output: Path) -> None:
    data_dir = data_dir.expanduser().resolve()
    database = data_dir / "infomancer.db"
    if not database.is_file():
        raise RuntimeError("No local InfoMancer database was found to back up.")

    output = output.expanduser().resolve()
    if _inside(output, data_dir):
        raise RuntimeError(
            "Choose a recovery destination outside InfoMancer's application-data folder."
        )
    if output.suffix.casefold() != ".infomancer-backup":
        output = output.with_name(output.name + ".infomancer-backup")
    output.parent.mkdir(parents=True, exist_ok=True)

    from app.recovery_package import RecoveryPackageService

    service = RecoveryPackageService(database, DESKTOP_VERSION)
    generated = service.create()
    try:
        shutil.copy2(generated, output)
        service.verify(output)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    print(str(output), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="InfoMancer Desktop local core")
    parser.add_argument("--port", type=int)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--bootstrap-token", default="")
    parser.add_argument("--recovery-output")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    if args.recovery_output:
        try:
            create_recovery_package(data_dir, Path(args.recovery_output))
        except Exception as exc:
            print(f"Recovery package failed: {{exc}}", file=sys.stderr, flush=True)
            raise SystemExit(2) from exc
        return

    if args.port is None:
        parser.error("--port is required when starting the local InfoMancer core")

    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / "infomancer.db"

    os.environ["INFOMANCER_DATABASE"] = str(database)
    os.environ["INFOMANCER_AUTH_MODE"] = "local"
    os.environ["INFOMANCER_COOKIE_SECURE"] = "false"
    os.environ["INFOMANCER_PUBLIC_URL"] = f"http://127.0.0.1:{{args.port}}"
    os.environ["INFOMANCER_TRUSTED_HOSTS"] = "127.0.0.1,localhost"
    os.environ["INFOMANCER_TRUST_CLOUDFLARE_PROXY"] = "false"
    if args.bootstrap_token:
        os.environ["INFOMANCER_BOOTSTRAP_TOKEN"] = args.bootstrap_token
    if not os.getenv("MEDIA_BROWSE_ROOTS", "").strip():
        os.environ["MEDIA_BROWSE_ROOTS"] = ",".join(str(path) for path in _default_media_roots())

    from app.main import app
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        proxy_headers=False,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
''',
    )

    write(
        "desktop/src-tauri/src/main.rs",
        r'''use serde::Serialize;
use std::{
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener, TcpStream},
    path::PathBuf,
    sync::Mutex,
    time::Duration,
};
use tauri::Manager;
use tauri_plugin_shell::{process::CommandChild, process::CommandEvent, ShellExt};
use tauri_plugin_updater::UpdaterExt;
use url::Url;
use uuid::Uuid;

const UPDATE_ENDPOINT: &str =
    "https://github.com/chandler-sol/InfoMancer/releases/download/desktop-alpha/latest.json";

#[derive(Clone, Serialize)]
struct LocalStartup {
    url: String,
    setup_url: String,
    bootstrap_token: String,
    first_run: bool,
}

#[derive(Clone, Serialize)]
struct UpdateStatus {
    configured: bool,
    available: bool,
    current_version: String,
    version: Option<String>,
    notes: Option<String>,
    message: String,
}

#[derive(Default)]
struct DesktopState {
    child: Mutex<Option<CommandChild>>,
    startup: Mutex<Option<LocalStartup>>,
}

fn reserve_loopback_port() -> Result<u16, String> {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
        .map_err(|error| format!("Could not reserve a local port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("Could not read the local port: {error}"))
}

async fn wait_for_local_core(port: u16) -> Result<(), String> {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port);
    for _ in 0..120 {
        if TcpStream::connect_timeout(&address, Duration::from_millis(150)).is_ok() {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    Err("The local InfoMancer core did not become ready in time.".into())
}

fn app_data_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_data_dir()
        .map_err(|error| format!("Could not resolve the application data directory: {error}"))
}

fn stop_local_core(state: &DesktopState) {
    let child = state
        .child
        .lock()
        .ok()
        .and_then(|mut child_slot| child_slot.take());
    if let Some(child) = child {
        let _ = child.kill();
    }
}

fn updater_public_key() -> Option<&'static str> {
    option_env!("INFOMANCER_UPDATER_PUBLIC_KEY").and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() { None } else { Some(trimmed) }
    })
}

fn desktop_updater(app: &tauri::AppHandle) -> Result<tauri_plugin_updater::Updater, String> {
    let public_key = updater_public_key().ok_or_else(|| {
        "Updater signing key is not configured for this build.".to_string()
    })?;
    let endpoint = Url::parse(UPDATE_ENDPOINT)
        .map_err(|error| format!("The InfoMancer update endpoint is invalid: {error}"))?;
    let exit_handle = app.clone();
    app.updater_builder()
        .pubkey(public_key)
        .endpoints(vec![endpoint])
        .map_err(|error| format!("Could not configure the updater: {error}"))?
        .on_before_exit(move || {
            let state = exit_handle.state::<DesktopState>();
            stop_local_core(state.inner());
        })
        .build()
        .map_err(|error| format!("Could not initialize the updater: {error}"))
}

#[tauri::command]
async fn start_local(app: tauri::AppHandle) -> Result<LocalStartup, String> {
    let state = app.state::<DesktopState>();
    if let Some(startup) = state
        .startup
        .lock()
        .map_err(|_| "Local desktop state is unavailable.".to_string())?
        .clone()
    {
        return Ok(startup);
    }

    let data_dir = app_data_dir(&app)?;
    std::fs::create_dir_all(&data_dir)
        .map_err(|error| format!("Could not create the application data directory: {error}"))?;
    let first_run = !data_dir.join("infomancer.db").exists();
    let port = reserve_loopback_port()?;
    let bootstrap_token = format!("desktop-{}", Uuid::new_v4().simple());
    let url = format!("http://127.0.0.1:{port}/");
    let setup_url = format!("http://127.0.0.1:{port}/setup");

    let args = vec![
        "--port".to_string(),
        port.to_string(),
        "--data-dir".to_string(),
        data_dir.to_string_lossy().into_owned(),
        "--bootstrap-token".to_string(),
        bootstrap_token.clone(),
    ];
    let sidecar = app
        .shell()
        .sidecar("infomancer-core")
        .map_err(|error| format!("Could not locate the bundled InfoMancer core: {error}"))?;
    let (mut events, child) = sidecar
        .args(args)
        .spawn()
        .map_err(|error| format!("Could not start the bundled InfoMancer core: {error}"))?;

    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stderr(line) => {
                    let message = String::from_utf8_lossy(&line);
                    eprintln!("InfoMancer core: {message}");
                }
                CommandEvent::Error(error) => eprintln!("InfoMancer core error: {error}"),
                _ => {}
            }
        }
    });

    {
        let mut child_slot = state
            .child
            .lock()
            .map_err(|_| "Local desktop process state is unavailable.".to_string())?;
        *child_slot = Some(child);
    }

    let startup = LocalStartup {
        url,
        setup_url,
        bootstrap_token,
        first_run,
    };
    {
        let mut startup_slot = state
            .startup
            .lock()
            .map_err(|_| "Local desktop state is unavailable.".to_string())?;
        *startup_slot = Some(startup.clone());
    }

    if let Err(error) = wait_for_local_core(port).await {
        stop_local_core(state.inner());
        if let Ok(mut startup_slot) = state.startup.lock() {
            *startup_slot = None;
        }
        return Err(error);
    }

    Ok(startup)
}

#[tauri::command]
fn normalize_remote(url: String) -> Result<String, String> {
    let candidate = url.trim();
    if candidate.is_empty() {
        return Err("Enter the URL of an InfoMancer server.".into());
    }
    let candidate = if candidate.contains("://") {
        candidate.to_string()
    } else {
        format!("http://{candidate}")
    };
    let parsed = Url::parse(&candidate).map_err(|_| "That server URL is not valid.".to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return Err("InfoMancer server URLs must use http:// or https://.".into());
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("Put credentials into InfoMancer itself, not in the server URL.".into());
    }
    Ok(parsed.to_string())
}

#[tauri::command]
async fn check_for_update(app: tauri::AppHandle) -> Result<UpdateStatus, String> {
    let current_version = app.package_info().version.to_string();
    if updater_public_key().is_none() {
        return Ok(UpdateStatus {
            configured: false,
            available: false,
            current_version,
            version: None,
            notes: None,
            message: "Secure automatic updates are not configured for this preview build.".into(),
        });
    }
    let updater = desktop_updater(&app)?;
    let update = updater
        .check()
        .await
        .map_err(|error| format!("Could not check GitHub Releases for updates: {error}"))?;
    match update {
        Some(update) => Ok(UpdateStatus {
            configured: true,
            available: true,
            current_version,
            version: Some(update.version.clone()),
            notes: update.body.clone(),
            message: format!("InfoMancer {} is available.", update.version),
        }),
        None => Ok(UpdateStatus {
            configured: true,
            available: false,
            current_version,
            version: None,
            notes: None,
            message: "InfoMancer is up to date.".into(),
        }),
    }
}

#[tauri::command]
async fn install_update(app: tauri::AppHandle) -> Result<bool, String> {
    let updater = desktop_updater(&app)?;
    let Some(update) = updater
        .check()
        .await
        .map_err(|error| format!("Could not check for the update: {error}"))?
    else {
        return Ok(false);
    };
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|error| format!("The signed update could not be installed: {error}"))?;
    Ok(true)
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(DesktopState::default())
        .invoke_handler(tauri::generate_handler![
            start_local,
            normalize_remote,
            check_for_update,
            install_update
        ])
        .build(tauri::generate_context!())
        .expect("failed to build InfoMancer Desktop");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            let state = app_handle.state::<DesktopState>();
            stop_local_core(state.inner());
        }
    });
}
''',
    )

    # Preserve the narrow launcher capability from the PoC. The loaded HTTP app is
    # deliberately not granted native updater/shell IPC permissions.
    write(
        "desktop/src-tauri/capabilities/launcher.json",
        '''{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "launcher",
  "description": "Core permissions for the bundled InfoMancer Desktop launcher only.",
  "windows": ["main"],
  "permissions": ["core:default"]
}
''',
    )


def launcher_html() -> None:
    write(
        "desktop/ui/index.html",
        r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>InfoMancer</title>
  <style>
    :root { color-scheme: dark; --bg:#080c10; --panel:#0d141b; --line:#23313d; --text:#edf3f7; --muted:#8998a6; --cyan:#51d6e6; --lime:#b9f542; --danger:#ef9090; }
    * { box-sizing:border-box; }
    html,body { margin:0; min-height:100%; background:var(--bg); color:var(--text); font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    body { min-height:100vh; display:grid; place-items:center; background:radial-gradient(circle at 25% 15%,rgba(81,214,230,.11),transparent 34rem),radial-gradient(circle at 80% 88%,rgba(185,245,66,.055),transparent 32rem),var(--bg); }
    .shell { width:min(980px,calc(100vw - 48px)); padding:34px 0 42px; }
    .brand { display:flex; align-items:center; gap:13px; margin-bottom:34px; }
    .mark { display:grid; width:44px; height:44px; place-items:center; border:1px solid rgba(81,214,230,.38); border-radius:12px; background:linear-gradient(145deg,rgba(81,214,230,.13),rgba(185,245,66,.045)); color:var(--cyan); font-weight:900; letter-spacing:-.08em; }
    .brand-copy strong { display:block; font-size:18px; letter-spacing:-.02em; }
    .brand-copy span { color:var(--muted); font-size:10px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
    .alpha { margin-left:auto; padding:5px 8px; border:1px solid rgba(81,214,230,.3); border-radius:999px; color:var(--cyan); font-size:9px; font-weight:900; letter-spacing:.1em; }
    header { max-width:690px; margin-bottom:26px; }
    header p:first-child { margin:0 0 7px; color:var(--cyan); font-size:10px; font-weight:900; letter-spacing:.16em; }
    h1 { margin:0; font-size:clamp(30px,4vw,46px); line-height:1.02; letter-spacing:-.045em; }
    header p:last-child { margin:14px 0 0; color:#aab6c0; font-size:14px; line-height:1.6; }
    .modes { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
    .mode { min-height:255px; padding:22px; border:1px solid var(--line); border-radius:16px; background:linear-gradient(180deg,rgba(255,255,255,.025),transparent),var(--panel); box-shadow:0 20px 60px rgba(0,0,0,.18); }
    .mode.recommended { border-color:rgba(81,214,230,.34); }
    .mode-label { display:flex; align-items:center; justify-content:space-between; gap:12px; }
    .mode-label span:first-child { color:var(--muted); font-size:9px; font-weight:900; letter-spacing:.14em; }
    .mode-label b { padding:4px 7px; border-radius:999px; background:rgba(81,214,230,.08); color:var(--cyan); font-size:8px; letter-spacing:.08em; }
    .mode h2 { margin:26px 0 7px; font-size:22px; letter-spacing:-.03em; }
    .mode>p { min-height:62px; margin:0 0 20px; color:var(--muted); font-size:12px; line-height:1.55; }
    button,input { font:inherit; }
    button { border:0; border-radius:9px; padding:10px 13px; cursor:pointer; font-size:11px; font-weight:800; }
    button.primary { width:100%; background:var(--lime); color:#0a0e0c; }
    button.secondary { background:#17232d; color:var(--text); }
    button:disabled { cursor:wait; opacity:.55; }
    .remote-row { display:grid; grid-template-columns:1fr auto; gap:7px; }
    input { min-width:0; border:1px solid #30404d; border-radius:9px; padding:9px 10px; background:#091016; color:var(--text); outline:none; font-size:11px; }
    input:focus { border-color:var(--cyan); box-shadow:0 0 0 2px rgba(81,214,230,.09); }
    .status { min-height:20px; margin-top:14px; color:var(--muted); font-size:10px; }
    .status.error { color:var(--danger); }
    .token-panel { display:none; margin-top:16px; padding:14px; border:1px solid rgba(185,245,66,.25); border-radius:10px; background:rgba(185,245,66,.045); }
    .token-panel.visible { display:grid; gap:9px; }
    .token-panel strong { font-size:11px; }
    .token-panel p { margin:0; color:var(--muted); font-size:10px; line-height:1.45; }
    .token-copy { display:grid; grid-template-columns:1fr auto; gap:6px; }
    .token-copy code { overflow:hidden; padding:8px 9px; border-radius:7px; background:#080d11; color:var(--lime); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
    .token-actions { display:flex; justify-content:flex-end; gap:7px; }
    .update-card { display:grid; gap:10px; grid-template-columns:1fr auto; align-items:center; margin-top:14px; padding:13px 15px; border:1px solid var(--line); border-radius:12px; background:rgba(255,255,255,.018); }
    .update-card strong { display:block; font-size:11px; }
    .update-card p { margin:3px 0 0; color:var(--muted); font-size:10px; line-height:1.4; }
    .update-actions { display:flex; gap:7px; }
    #install-update { display:none; }
    #install-update.visible { display:inline-block; }
    footer { margin-top:18px; color:#61707d; font-size:9px; line-height:1.5; }
    @media(max-width:720px) { .modes{grid-template-columns:1fr}.mode{min-height:auto}.update-card{grid-template-columns:1fr}.update-actions{justify-content:flex-start} }
  </style>
</head>
<body>
  <main class="shell">
    <div class="brand"><div class="mark">IM</div><div class="brand-copy"><strong>InfoMancer</strong><span>Windows Desktop</span></div><span class="alpha">0.8 ALPHA</span></div>
    <header><p>CHOOSE HOW INFOMANCER RUNS</p><h1>Your Workspace, without the browser.</h1><p>Run the InfoMancer core on this PC or use the desktop shell as a client for an existing server.</p></header>
    <section class="modes">
      <article class="mode recommended">
        <div class="mode-label"><span>LOCAL DESKTOP</span><b>STANDALONE</b></div><h2>Run on this computer</h2><p>Starts a bundled InfoMancer core on loopback. Your database stays in Windows application data while media can remain on local disks or network shares.</p>
        <button id="start-local" class="primary" type="button">Start InfoMancer locally</button><div id="local-status" class="status" role="status"></div>
        <div id="token-panel" class="token-panel"><strong>First-run setup token</strong><p>InfoMancer's hardened first-run setup still applies. Copy this one-time token, then continue to setup.</p><div class="token-copy"><code id="bootstrap-token"></code><button id="copy-token" class="secondary" type="button">Copy</button></div><div class="token-actions"><button id="open-setup" class="primary" type="button">Continue to setup</button></div></div>
      </article>
      <article class="mode">
        <div class="mode-label"><span>SERVER CLIENT</span></div><h2>Connect to a server</h2><p>Use this machine as a native InfoMancer client while your catalog, scans, and background work stay on an existing self-hosted server.</p>
        <div class="remote-row"><input id="server-url" type="url" spellcheck="false" placeholder="https://infomancer.example.com" aria-label="InfoMancer server URL"><button id="connect-server" class="secondary" type="button">Connect</button></div><div id="remote-status" class="status" role="status"></div>
      </article>
    </section>
    <section class="update-card" aria-live="polite"><div><strong>Desktop updates</strong><p id="update-status">Checking the signed GitHub release channel...</p></div><div class="update-actions"><button id="check-update" class="secondary" type="button">Check again</button><button id="install-update" class="primary" type="button">Install update</button></div></section>
    <footer>The bundled launcher alone has native Tauri command access. Once it connects to the InfoMancer HTTP application, that remote content is not granted updater or shell IPC permissions.</footer>
  </main>
  <script>
    const invoke = window.__TAURI__.core.invoke;
    const localButton = document.getElementById('start-local'); const localStatus = document.getElementById('local-status'); const tokenPanel = document.getElementById('token-panel'); const tokenValue = document.getElementById('bootstrap-token'); const openSetup = document.getElementById('open-setup'); let startup = null;
    const showError = (element,error) => { element.classList.add('error'); element.textContent = String(error || 'Something went wrong.'); };
    localButton.addEventListener('click', async () => { localButton.disabled=true; localStatus.classList.remove('error'); localStatus.textContent='Starting the local InfoMancer core...'; try { startup=await invoke('start_local'); if(startup.first_run){tokenValue.textContent=startup.bootstrap_token;tokenPanel.classList.add('visible');localStatus.textContent='Local core is ready. Complete first-run setup to continue.';}else{localStatus.textContent='Local core ready. Opening Workspace...';window.location.replace(startup.url);}} catch(error){showError(localStatus,error);localButton.disabled=false;} });
    document.getElementById('copy-token').addEventListener('click', async()=>{ if(!startup)return; await navigator.clipboard.writeText(startup.bootstrap_token); document.getElementById('copy-token').textContent='Copied'; });
    openSetup.addEventListener('click',()=>{if(startup)window.location.replace(startup.setup_url);});
    const connect=async()=>{const input=document.getElementById('server-url'),status=document.getElementById('remote-status'),button=document.getElementById('connect-server');button.disabled=true;status.classList.remove('error');status.textContent='Checking server address...';try{const normalized=await invoke('normalize_remote',{url:input.value});status.textContent='Opening InfoMancer...';window.location.replace(normalized);}catch(error){showError(status,error);button.disabled=false;}};
    document.getElementById('connect-server').addEventListener('click',connect); document.getElementById('server-url').addEventListener('keydown',event=>{if(event.key==='Enter')connect();});
    const updateStatus=document.getElementById('update-status'),checkUpdate=document.getElementById('check-update'),installUpdate=document.getElementById('install-update');
    const check=async()=>{checkUpdate.disabled=true;installUpdate.classList.remove('visible');updateStatus.textContent='Checking the signed GitHub release channel...';try{const result=await invoke('check_for_update');updateStatus.textContent=result.message;if(result.available)installUpdate.classList.add('visible');}catch(error){updateStatus.textContent=String(error);}finally{checkUpdate.disabled=false;}};
    checkUpdate.addEventListener('click',check); installUpdate.addEventListener('click',async()=>{installUpdate.disabled=true;updateStatus.textContent='Downloading and verifying the signed update...';try{const installed=await invoke('install_update');if(!installed){updateStatus.textContent='InfoMancer is already up to date.';installUpdate.classList.remove('visible');}else{updateStatus.textContent='Installing update...';}}catch(error){updateStatus.textContent=String(error);installUpdate.disabled=false;}}); check();
  </script>
</body>
</html>
''',
    )


def docs() -> None:
    write(
        "desktop/README.md",
        f'''# InfoMancer Windows Desktop

This directory contains the Tauri v2 Windows shell for InfoMancer {VERSION}.
It can launch a bundled Python core on loopback or connect to an existing
InfoMancer server.

## Security boundary

Only the bundled launcher document has Tauri IPC capability. After the window
navigates to the local or remote InfoMancer HTTP app, that web content is not
granted native shell or updater commands.

## Local data

The standalone core uses Tauri's application-data directory for
`{IDENTIFIER}`. The bundled Python sidecar keeps local auth, Host/Origin/CSRF
protections, loopback-only binding, and disabled proxy-header trust.

## Uninstall contract

A normal interactive uninstall is zero-residue for InfoMancer-owned state. The
confirmation checkbox is mandatory. If a local database exists, the uninstaller
offers one last chance to save a verified `.infomancer-backup` to a location the
user chooses outside InfoMancer data. If backup creation or verification fails,
uninstall stops unless the user explicitly chooses to continue without it.

Updater-driven replacement is not treated as uninstall and therefore preserves
application data. Silent uninstall skips the recovery prompt and is intended for
CI or explicit unattended deployment.

## Updates without InfoMancer-hosted infrastructure

Windows binaries and updater manifests are hosted as GitHub Release assets.
Alpha builds read a rolling manifest at the `desktop-alpha` GitHub release. The
manifest points to versioned release assets. Tauri verifies every update with its
mandatory updater signature before installation.

The updater signing private key is never stored in this repository. Release CI
expects:

- repository variable `TAURI_UPDATER_PUBLIC_KEY`
- Actions secret `TAURI_SIGNING_PRIVATE_KEY`
- optional Actions secret `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`

Generate the key pair once from this directory with:

```powershell
npm run tauri signer generate -- -w $HOME\\.tauri\\infomancer.key
```

Back up the private key somewhere independent of GitHub. Losing it means already
installed clients cannot trust future updater artifacts.

## Build locally

```powershell
python -m pip install -r ..\\requirements.txt
python -m pip install pyinstaller==6.21.0
python -m PyInstaller --noconfirm --clean --onefile --name infomancer-core --add-data "..\\app\\templates;app/templates" --add-data "..\\app\\static;app/static" sidecar.py
$triple = (rustc --print host-tuple).Trim()
New-Item -ItemType Directory -Force src-tauri\\binaries | Out-Null
Copy-Item ..\\dist\\infomancer-core.exe "src-tauri\\binaries\\infomancer-core-$triple.exe"
npm ci
npm run icon
npm run build
```

GitHub's `Windows Desktop` workflow performs the supported reproducible preview
build and also smoke-tests the sidecar recovery mode plus silent zero-residue
uninstall.
''',
    )

    path = "docs/PACKAGING.md"
    text = read(path)
    addition = f'''\n## Windows desktop alpha implementation\n\nThe Windows desktop shell is now carried in `desktop/` and built as an NSIS\ncurrent-user installer. It bundles the current InfoMancer Python core as a\nPyInstaller sidecar and stores standalone state under Tauri's\n`{IDENTIFIER}` application-data directory.\n\nThe NSIS confirmation deliberately treats application-data deletion as mandatory\nfor a normal uninstall. Before destructive removal, an installation with a local\ndatabase offers to create and verify a portable `.infomancer-backup` at a\nuser-selected destination. Updates use Tauri's `/UPDATE` path and explicitly skip\nthe purge so a binary upgrade never erases the catalog. Silent uninstall is\nreserved for unattended/CI use and performs the zero-residue purge without an\ninteractive backup prompt.\n\nThe desktop build has an automated Windows smoke test that seeds known Roaming and\nLocal AppData paths, silently uninstalls InfoMancer, and fails if owned state or\ninstaller registration survives.\n'''
    if "## Windows desktop alpha implementation" not in text:
        text += addition
    write(path, text)

    path = "docs/UPDATES.md"
    text = read(path)
    addition = '''\n## Native Windows updater\n\nThe native Windows shell uses Tauri's signed updater rather than the host-update\nrequest mechanism used by the self-hosted deployment. No InfoMancer-operated file\nserver is required. GitHub Releases stores the NSIS updater bundle, signature, and\n`latest.json` manifest. Alpha clients read a rolling `desktop-alpha/latest.json`\nasset whose download URLs point at immutable versioned releases.\n\nTauri updater signatures are mandatory. The public verification key is compiled\ninto release builds through `TAURI_UPDATER_PUBLIC_KEY`; the private key is supplied\nonly to GitHub Actions through `TAURI_SIGNING_PRIVATE_KEY` (and optional password).\nPreview builds made without the public key remain buildable but report the updater\nas not configured rather than accepting unsigned updates.\n\nBefore the Windows updater launches the replacement installer, the desktop shell\nstops its bundled local core. The NSIS uninstaller hooks detect updater mode and\npreserve application data, so ordinary updates replace binaries without invoking\nthe zero-residue uninstall policy.\n'''
    if "## Native Windows updater" not in text:
        text += addition
    write(path, text)


def tests() -> None:
    write(
        "tests/test_windows_desktop.py",
        r'''import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsDesktopContractTests(unittest.TestCase):
    def test_desktop_version_matches_application_alpha(self):
        main = (ROOT / "app/main.py").read_text(encoding="utf-8")
        match = re.search(r'APP_VERSION = "([^"]+)"', main)
        self.assertIsNotNone(match)
        config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        self.assertEqual(config["version"], match.group(1))
        self.assertEqual(config["productName"], "InfoMancer")
        self.assertEqual(config["identifier"], "cloud.arsenik.infomancer")

    def test_nsis_uninstall_is_zero_residue_but_update_safe(self):
        config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        nsis = config["bundle"]["windows"]["nsis"]
        self.assertEqual(nsis["installerHooks"], "./windows/hooks.nsh")
        self.assertEqual(nsis["customLanguageFiles"]["English"], "./windows/English.nsh")
        hooks = (ROOT / "desktop/src-tauri/windows/hooks.nsh").read_text(encoding="utf-8")
        for path in (
            r'$APPDATA\cloud.arsenik.infomancer',
            r'$LOCALAPPDATA\cloud.arsenik.infomancer',
            r'$TEMP\InfoMancer',
        ):
            self.assertIn(path, hooks)
        self.assertIn("$DeleteAppDataCheckboxState != 1", hooks)
        self.assertIn("$UpdateMode == 1", hooks)
        self.assertIn("$UpdateMode != 1", hooks)
        self.assertIn("--recovery-output", hooks)
        language = (ROOT / "desktop/src-tauri/windows/English.nsh").read_text(encoding="utf-8")
        self.assertIn("all InfoMancer application data will be permanently removed", language)

    def test_uninstaller_recovery_uses_verified_portable_package(self):
        sidecar = (ROOT / "desktop/sidecar.py").read_text(encoding="utf-8")
        self.assertIn("RecoveryPackageService", sidecar)
        self.assertIn("service.verify(output)", sidecar)
        self.assertIn("Choose a recovery destination outside", sidecar)

    def test_updater_uses_signed_github_release_channel(self):
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("tauri_plugin_updater", rust)
        self.assertIn("desktop-alpha/latest.json", rust)
        self.assertIn("INFOMANCER_UPDATER_PUBLIC_KEY", rust)
        self.assertIn("download_and_install", rust)
        capability = json.loads((ROOT / "desktop/src-tauri/capabilities/launcher.json").read_text(encoding="utf-8"))
        self.assertEqual(capability["permissions"], ["core:default"])
        release_config = json.loads((ROOT / "desktop/src-tauri/tauri.release.conf.json").read_text(encoding="utf-8"))
        self.assertTrue(release_config["bundle"]["createUpdaterArtifacts"])

    def test_release_workflow_keeps_private_key_out_of_source(self):
        workflow = (ROOT / ".github/workflows/windows-desktop-release.yml").read_text(encoding="utf-8")
        self.assertIn("secrets.TAURI_SIGNING_PRIVATE_KEY", workflow)
        self.assertIn("vars.TAURI_UPDATER_PUBLIC_KEY", workflow)
        self.assertIn("desktop-alpha", workflow)
        self.assertNotIn("BEGIN PRIVATE KEY", workflow)


if __name__ == "__main__":
    unittest.main()
''',
    )


def workflows() -> None:
    write(
        ".github/workflows/windows-desktop.yml",
        r'''name: Windows Desktop

on:
  workflow_dispatch:
  push:
    branches:
      - testing/0.8-alpha
    paths:
      - desktop/**
      - app/**
      - requirements.txt
      - .github/workflows/windows-desktop.yml

permissions:
  contents: read

jobs:
  build-windows:
    runs-on: windows-latest
    steps:
      - name: Check out source
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          persist-credentials: false

      - name: Set up Python
        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
        with:
          python-version: '3.13'
          cache: pip

      - name: Install Python dependencies
        run: |
          python -m pip install -r requirements.txt
          python -m pip install pyinstaller==6.21.0

      - name: Run application tests
        run: python -m unittest discover -s tests -v

      - name: Build bundled InfoMancer core
        shell: pwsh
        run: |
          python -m PyInstaller --noconfirm --clean --onefile --name infomancer-core --add-data "app/templates;app/templates" --add-data "app/static;app/static" desktop/sidecar.py
          $triple = (rustc --print host-tuple).Trim()
          New-Item -ItemType Directory -Force desktop/src-tauri/binaries | Out-Null
          Copy-Item dist/infomancer-core.exe "desktop/src-tauri/binaries/infomancer-core-$triple.exe"

      - name: Smoke-test recovery helper
        shell: pwsh
        run: |
          $data = Join-Path $env:RUNNER_TEMP 'infomancer-recovery-smoke'
          $backup = Join-Path $env:RUNNER_TEMP 'InfoMancer-Recovery-Smoke.infomancer-backup'
          New-Item -ItemType Directory -Force $data | Out-Null
          $env:SMOKE_DB = Join-Path $data 'infomancer.db'
          python -c "import os; from pathlib import Path; from app.db import Database; Database(Path(os.environ['SMOKE_DB'])).initialize()"
          & .\dist\infomancer-core.exe --data-dir $data --recovery-output $backup
          if ($LASTEXITCODE -ne 0 -or -not (Test-Path $backup)) { throw 'Recovery helper smoke test failed.' }

      - name: Install desktop dependencies
        working-directory: desktop
        run: npm ci

      - name: Generate native icons
        working-directory: desktop
        run: npm run icon

      - name: Check Rust shell
        working-directory: desktop
        run: cargo check --manifest-path src-tauri/Cargo.toml --locked

      - name: Build NSIS installer
        working-directory: desktop
        run: npm run build

      - name: Smoke-test zero-residue silent uninstall
        shell: pwsh
        run: |
          $installer = Get-ChildItem 'desktop/src-tauri/target/release/bundle/nsis/*-setup.exe' | Select-Object -First 1
          if (-not $installer) { throw 'NSIS installer was not produced.' }
          $process = Start-Process -FilePath $installer.FullName -ArgumentList '/S' -Wait -PassThru
          if ($process.ExitCode -ne 0) { throw "Silent install failed: $($process.ExitCode)" }
          $installDir = Join-Path $env:LOCALAPPDATA 'InfoMancer'
          $uninstaller = Join-Path $installDir 'uninstall.exe'
          if (-not (Test-Path $uninstaller)) { throw 'Installed uninstaller was not found.' }
          $owned = @(
            (Join-Path $env:APPDATA 'cloud.arsenik.infomancer'),
            (Join-Path $env:LOCALAPPDATA 'cloud.arsenik.infomancer'),
            (Join-Path $env:APPDATA 'cloud.arsenik.infomancer.desktop.poc'),
            (Join-Path $env:LOCALAPPDATA 'cloud.arsenik.infomancer.desktop.poc'),
            (Join-Path $env:TEMP 'InfoMancer')
          )
          foreach ($path in $owned) { New-Item -ItemType Directory -Force $path | Out-Null; Set-Content (Join-Path $path 'residue-marker.txt') 'owned by InfoMancer' }
          $process = Start-Process -FilePath $uninstaller -ArgumentList '/S' -Wait -PassThru
          if ($process.ExitCode -ne 0) { throw "Silent uninstall failed: $($process.ExitCode)" }
          Start-Sleep -Seconds 2
          foreach ($path in $owned) { if (Test-Path $path) { throw "Uninstall residue remains: $path" } }
          if (Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\InfoMancer') { throw 'Uninstall registry entry remains.' }
          if (Test-Path 'HKCU:\Software\Arsenik\InfoMancer') { throw 'InfoMancer publisher registry state remains.' }

      - name: Upload Windows installer
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
        with:
          name: InfoMancer-Windows-${{ github.sha }}
          path: desktop/src-tauri/target/release/bundle/nsis/*-setup.exe
          if-no-files-found: error
          compression-level: 0
''',
    )

    write(
        ".github/workflows/windows-desktop-release.yml",
        r'''name: Windows Desktop Release

on:
  push:
    tags:
      - 'v*'

permissions:
  contents: write

jobs:
  signed-windows-release:
    runs-on: windows-latest
    steps:
      - name: Check out release tag
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          persist-credentials: false

      - name: Set up Python
        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
        with:
          python-version: '3.13'
          cache: pip

      - name: Verify updater signing configuration
        shell: pwsh
        env:
          INFOMANCER_PUBLIC_KEY: ${{ vars.TAURI_UPDATER_PUBLIC_KEY }}
          INFOMANCER_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
        run: |
          if ([string]::IsNullOrWhiteSpace($env:INFOMANCER_PUBLIC_KEY)) { throw 'Set repository variable TAURI_UPDATER_PUBLIC_KEY before publishing Windows releases.' }
          if ([string]::IsNullOrWhiteSpace($env:INFOMANCER_PRIVATE_KEY)) { throw 'Set Actions secret TAURI_SIGNING_PRIVATE_KEY before publishing Windows releases.' }

      - name: Install Python dependencies
        run: |
          python -m pip install -r requirements.txt
          python -m pip install pyinstaller==6.21.0

      - name: Run application tests
        run: python -m unittest discover -s tests -v

      - name: Build bundled InfoMancer core
        shell: pwsh
        run: |
          python -m PyInstaller --noconfirm --clean --onefile --name infomancer-core --add-data "app/templates;app/templates" --add-data "app/static;app/static" desktop/sidecar.py
          $triple = (rustc --print host-tuple).Trim()
          New-Item -ItemType Directory -Force desktop/src-tauri/binaries | Out-Null
          Copy-Item dist/infomancer-core.exe "desktop/src-tauri/binaries/infomancer-core-$triple.exe"

      - name: Install desktop dependencies
        working-directory: desktop
        run: npm ci

      - name: Generate native icons
        working-directory: desktop
        run: npm run icon

      - name: Build, sign, and publish Windows updater
        uses: tauri-apps/tauri-action@84b9d35b5fc46c1e45415bdb6144030364f7ebc5
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
          INFOMANCER_UPDATER_PUBLIC_KEY: ${{ vars.TAURI_UPDATER_PUBLIC_KEY }}
        with:
          projectPath: desktop
          tagName: ${{ github.ref_name }}
          releaseName: InfoMancer ${{ github.ref_name }}
          releaseBody: Windows desktop alpha with signed in-app updater artifacts.
          releaseDraft: false
          prerelease: true
          args: --bundles nsis --config src-tauri/tauri.release.conf.json
          updaterJsonPreferNsis: true
          uploadUpdaterJson: true
          uploadUpdaterSignatures: true

      - name: Update rolling desktop-alpha manifest
        shell: pwsh
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          $channelDir = Join-Path $env:RUNNER_TEMP 'desktop-alpha-channel'
          New-Item -ItemType Directory -Force $channelDir | Out-Null
          gh release download $env:GITHUB_REF_NAME --pattern latest.json --dir $channelDir
          gh release view desktop-alpha 2>$null
          if ($LASTEXITCODE -ne 0) {
            gh release create desktop-alpha --target $env:GITHUB_SHA --title 'InfoMancer Desktop Alpha Update Channel' --notes 'Rolling signed updater manifest for InfoMancer Windows alpha builds.' --prerelease
          }
          gh release upload desktop-alpha (Join-Path $channelDir 'latest.json') --clobber
''',
    )


def main() -> None:
    if not DESKTOP.exists():
        raise RuntimeError("desktop PoC tree must be copied into the working tree before patching")
    desktop_files()
    launcher_html()
    docs()
    tests()
    workflows()


if __name__ == "__main__":
    main()

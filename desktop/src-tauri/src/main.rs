#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
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
    ];
    let sidecar = app
        .shell()
        .sidecar("infomancer-core")
        .map_err(|error| format!("Could not locate the bundled InfoMancer core: {error}"))?
        .args(args)
        .env("INFOMANCER_BOOTSTRAP_TOKEN", &bootstrap_token);
    let (mut events, child) = sidecar
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

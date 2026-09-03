#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod external_links;

use serde::Serialize;
use std::{
    fs::OpenOptions,
    io::{Read, Write},
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener, TcpStream},
    path::PathBuf,
    sync::{Mutex, OnceLock},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::Manager;
use tauri_plugin_shell::{process::CommandChild, process::CommandEvent, ShellExt};
use tauri_plugin_updater::UpdaterExt;
use url::Url;
use uuid::Uuid;

const UPDATE_ENDPOINT: &str =
    "https://github.com/chandler-sol/InfoMancer/releases/download/desktop-alpha/latest.json";
const LOCAL_CORE_STARTUP_TIMEOUT: Duration = Duration::from_secs(60);
const LOCAL_CORE_POLL_INTERVAL: Duration = Duration::from_millis(150);

static LAUNCH_LOG_PATH: OnceLock<PathBuf> = OnceLock::new();

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

fn launcher_log_path() -> PathBuf {
    LAUNCH_LOG_PATH
        .get_or_init(|| {
            let mut path = if cfg!(target_os = "macos") {
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
            } else if let Some(appdata) = std::env::var_os("APPDATA") {
                let mut appdata = PathBuf::from(appdata);
                appdata.push("cloud.arsenik.infomancer");
                appdata
            } else {
                let mut fallback = std::env::temp_dir();
                fallback.push("InfoMancer");
                fallback
            };
            path.push("logs");
            path.push("desktop-launcher.log");
            path
        })
        .clone()
}

fn log_launcher(message: &str) {
    let path = launcher_log_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs())
        .unwrap_or_default();
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "[{timestamp}] {message}");
    }
}

fn install_panic_logger() {
    std::panic::set_hook(Box::new(|panic_info| {
        log_launcher(&format!("fatal panic: {panic_info}"));
    }));
}

#[cfg(target_os = "windows")]
fn show_startup_error(message: &str) {
    use std::ffi::c_void;
    use std::ptr::null_mut;

    #[link(name = "user32")]
    extern "system" {
        fn MessageBoxW(
            hwnd: *mut c_void,
            text: *const u16,
            caption: *const u16,
            kind: u32,
        ) -> i32;
    }

    let text: Vec<u16> = message.encode_utf16().chain(std::iter::once(0)).collect();
    let caption: Vec<u16> = "InfoMancer startup error"
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        let _ = MessageBoxW(null_mut(), text.as_ptr(), caption.as_ptr(), 0x00000010);
    }
}

#[cfg(not(target_os = "windows"))]
fn show_startup_error(message: &str) {
    eprintln!("{message}");
}

fn reserve_loopback_port() -> Result<u16, String> {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
        .map_err(|error| format!("Could not reserve a local port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("Could not read the local port: {error}"))
}

fn probe_setup_pending(port: u16) -> Result<bool, String> {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port);
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(350))
        .map_err(|error| format!("Local core is not accepting HTTP requests yet: {error}"))?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
    stream
        .write_all(b"GET /setup HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .map_err(|error| format!("Could not ask the local core for setup status: {error}"))?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| format!("Could not read local setup status: {error}"))?;
    let status_line = response
        .lines()
        .next()
        .ok_or_else(|| "The local core returned an empty setup-status response.".to_string())?;
    let code = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| format!("The local core returned an invalid HTTP status: {status_line}"))?;
    match code {
        200 => Ok(true),
        301 | 302 | 303 | 307 | 308 => Ok(false),
        _ => Err(format!("The local setup-status check returned HTTP {code}.")),
    }
}

async fn wait_for_local_core(port: u16) -> Result<bool, String> {
    let started = Instant::now();
    let mut last_error = String::new();
    while started.elapsed() < LOCAL_CORE_STARTUP_TIMEOUT {
        match probe_setup_pending(port) {
            Ok(setup_pending) => {
                log_launcher(&format!(
                    "Local core became HTTP-ready after {} ms; setup_pending={setup_pending}.",
                    started.elapsed().as_millis()
                ));
                return Ok(setup_pending);
            }
            Err(error) => last_error = error,
        }
        tokio::time::sleep(LOCAL_CORE_POLL_INTERVAL).await;
    }
    Err(format!(
        "The local InfoMancer core did not become ready within {} seconds. {} Check {} for startup details.",
        LOCAL_CORE_STARTUP_TIMEOUT.as_secs(),
        last_error,
        launcher_log_path().display()
    ))
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
    let port = reserve_loopback_port()?;
    let bootstrap_token = format!("desktop-{}", Uuid::new_v4().simple());
    let url = format!("http://127.0.0.1:{port}/");
    let setup_url = format!("http://127.0.0.1:{port}/setup");

    log_launcher(&format!(
        "Launching bundled InfoMancer core on 127.0.0.1:{port}."
    ));
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
                    log_launcher(&format!("InfoMancer core stderr: {message}"));
                }
                CommandEvent::Error(error) => {
                    log_launcher(&format!("InfoMancer core process error: {error}"));
                }
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

    let first_run = match wait_for_local_core(port).await {
        Ok(value) => value,
        Err(error) => {
            log_launcher(&format!("Local core startup failed: {error}"));
            stop_local_core(state.inner());
            return Err(error);
        }
    };

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

    log_launcher(&format!(
        "Local InfoMancer core is ready on 127.0.0.1:{port}; setup_pending={first_run}."
    ));
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
    install_panic_logger();
    log_launcher("InfoMancer Desktop launcher starting.");

    let result = tauri::Builder::default()
        .setup(external_links::setup)
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(DesktopState::default())
        .invoke_handler(tauri::generate_handler![
            start_local,
            normalize_remote,
            check_for_update,
            install_update
        ])
        .build(tauri::generate_context!());

    let app = match result {
        Ok(app) => app,
        Err(error) => {
            let log_path = launcher_log_path();
            let message = format!(
                "InfoMancer could not start.\n\n{error}\n\nA diagnostic log was written to:\n{}",
                log_path.display()
            );
            log_launcher(&format!("Tauri startup failed: {error}"));
            show_startup_error(&message);
            return;
        }
    };

    log_launcher("Tauri application built successfully; entering the desktop event loop.");
    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            log_launcher("InfoMancer Desktop is exiting.");
            let state = app_handle.state::<DesktopState>();
            stop_local_core(state.inner());
        }
    });
}

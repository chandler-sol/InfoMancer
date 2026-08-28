use std::process::Command;

use tauri::{webview::NewWindowResponse, WebviewUrl, WebviewWindowBuilder};
use url::Url;

fn safe_external_url(url: &Url) -> bool {
    matches!(url.scheme(), "http" | "https")
        && url.host_str().is_some()
        && url.username().is_empty()
        && url.password().is_none()
}

#[cfg(target_os = "windows")]
fn launch(url: &Url) -> Result<(), String> {
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x08000000;
    Command::new("rundll32.exe")
        .arg("url.dll,FileProtocolHandler")
        .arg(url.as_str())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Could not open the system browser: {error}"))
}

#[cfg(target_os = "macos")]
fn launch(url: &Url) -> Result<(), String> {
    Command::new("open")
        .arg(url.as_str())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Could not open the system browser: {error}"))
}

#[cfg(all(unix, not(target_os = "macos")))]
fn launch(url: &Url) -> Result<(), String> {
    Command::new("xdg-open")
        .arg(url.as_str())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Could not open the system browser: {error}"))
}

#[cfg(not(any(target_os = "windows", target_os = "macos", unix)))]
fn launch(_url: &Url) -> Result<(), String> {
    Err("Opening external links is not supported on this platform.".into())
}

pub fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("InfoMancer")
        .inner_size(1440.0, 900.0)
        .min_inner_size(960.0, 640.0)
        .center()
        .resizable(true)
        .on_new_window(|url, _features| {
            if safe_external_url(&url) {
                // Remote InfoMancer content intentionally has no shell IPC access.
                // Handle target=_blank at the native webview boundary instead, and
                // pass only validated HTTP(S) URLs to the operating system browser.
                if let Err(error) = launch(&url) {
                    eprintln!("InfoMancer external link error: {error}");
                }
            }
            NewWindowResponse::Deny
        })
        .build()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn external_link_filter_accepts_only_credential_free_http_urls() {
        assert!(safe_external_url(&"https://www.thetvdb.com/search?query=Alien".parse().unwrap()));
        assert!(safe_external_url(&"http://example.test/".parse().unwrap()));
        assert!(!safe_external_url(&"file:///tmp/test".parse().unwrap()));
        assert!(!safe_external_url(&"https://user:pass@example.test/".parse().unwrap()));
    }
}

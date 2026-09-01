use std::process::Command;

use tauri::{webview::NewWindowResponse, window::Color, WebviewUrl, WebviewWindowBuilder};
use url::Url;

const DESKTOP_EXTERNAL_LINK_BRIDGE: &str = r#"
// WebView2's default document/window paint is white. Give every desktop document a
// dark root before page CSS arrives so startup and top-level navigation never expose
// a white intermediate frame between the launcher and the InfoMancer HTTP app.
document.documentElement.style.backgroundColor = '#080c10';
document.documentElement.style.colorScheme = 'dark';

window.__INFOMANCER_DESKTOP__ = true;
document.addEventListener('click', (event) => {
  const link = event.target?.closest?.('a[target="_blank"]');
  if (!link) return;
  try {
    const url = new URL(link.href, window.location.href);
    const host = url.hostname.toLowerCase();
    if (url.protocol === 'https:' && (host === 'thetvdb.com' || host === 'www.thetvdb.com')) {
      // WebView2 has not reliably surfaced target=_blank requests through
      // on_new_window for hosted InfoMancer pages. Convert this one trusted
      // external destination into a top-level navigation; Rust intercepts that
      // navigation below, opens the OS browser, and cancels the WebView move.
      event.preventDefault();
      window.location.assign(url.href);
    }
  } catch (_) {}
}, true);
"#;

fn safe_external_url(url: &Url) -> bool {
    matches!(url.scheme(), "http" | "https")
        && url.host_str().is_some()
        && url.username().is_empty()
        && url.password().is_none()
}

fn is_tvdb_external_url(url: &Url) -> bool {
    if !safe_external_url(url) || url.scheme() != "https" {
        return false;
    }
    matches!(
        url.host_str().map(str::to_ascii_lowercase).as_deref(),
        Some("thetvdb.com") | Some("www.thetvdb.com")
    )
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
        // Paint the native window and WebView dark before index.html exists. This
        // prevents the first white frame WebView2 otherwise shows while booting.
        .background_color(Color(8, 12, 16, 255))
        .initialization_script(DESKTOP_EXTERNAL_LINK_BRIDGE)
        .on_navigation(|url| {
            if is_tvdb_external_url(url) {
                if let Err(error) = launch(url) {
                    eprintln!("InfoMancer external link error: {error}");
                }
                return false;
            }
            true
        })
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

    #[test]
    fn top_level_external_bridge_is_limited_to_tvdb_https() {
        assert!(is_tvdb_external_url(
            &"https://www.thetvdb.com/search?query=Jackass+3.5".parse().unwrap()
        ));
        assert!(is_tvdb_external_url(
            &"https://thetvdb.com/movies/jackass-35".parse().unwrap()
        ));
        assert!(!is_tvdb_external_url(
            &"http://www.thetvdb.com/search?query=Alien".parse().unwrap()
        ));
        assert!(!is_tvdb_external_url(
            &"https://example.test/".parse().unwrap()
        ));
    }

    #[test]
    fn desktop_bridge_primes_a_dark_document_before_page_css() {
        assert!(DESKTOP_EXTERNAL_LINK_BRIDGE.contains("backgroundColor = '#080c10'"));
        assert!(DESKTOP_EXTERNAL_LINK_BRIDGE.contains("colorScheme = 'dark'"));
    }
}

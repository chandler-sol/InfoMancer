use std::{process::Command, sync::{Arc, Mutex}};

use tauri::{
    webview::{NewWindowResponse, PageLoadEvent},
    window::Color,
    WebviewUrl, WebviewWindowBuilder,
};
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

#[derive(Clone, Debug, PartialEq, Eq)]
struct TrustedOrigin {
    scheme: String,
    host: String,
    explicit_port: Option<u16>,
}

impl TrustedOrigin {
    fn from_url(url: &Url) -> Option<Self> {
        if !safe_external_url(url) {
            return None;
        }
        Some(Self {
            scheme: url.scheme().to_ascii_lowercase(),
            host: url.host_str()?.to_ascii_lowercase(),
            explicit_port: url.port(),
        })
    }

    fn effective_port(&self) -> Option<u16> {
        self.explicit_port.or(match self.scheme.as_str() {
            "http" => Some(80),
            "https" => Some(443),
            _ => None,
        })
    }

    fn matches(&self, url: &Url) -> bool {
        let Some(candidate) = Self::from_url(url) else {
            return false;
        };
        self.scheme == candidate.scheme
            && self.host == candidate.host
            && self.effective_port() == candidate.effective_port()
    }

    fn can_upgrade_to(&self, url: &Url) -> bool {
        let Some(candidate) = Self::from_url(url) else {
            return false;
        };
        if self.scheme != "http" || candidate.scheme != "https" || self.host != candidate.host {
            return false;
        }
        match (self.explicit_port, candidate.explicit_port) {
            (None, None) => true,
            (Some(current), Some(next)) => current == next,
            _ => false,
        }
    }

    fn is_loopback(&self) -> bool {
        self.host == "localhost"
            || self.host.parse::<std::net::IpAddr>().is_ok_and(|address| address.is_loopback())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum NavigationDecision {
    Allow,
    External,
    Deny,
}

#[derive(Default)]
struct NavigationPolicy {
    expected: Option<TrustedOrigin>,
    trusted: Option<TrustedOrigin>,
}

impl NavigationPolicy {
    fn decide(&mut self, url: &Url) -> NavigationDecision {
        if is_launcher_url(url) {
            return NavigationDecision::Allow;
        }
        if is_tvdb_external_url(url) {
            return NavigationDecision::External;
        }
        let Some(candidate) = TrustedOrigin::from_url(url) else {
            return NavigationDecision::Deny;
        };

        if let Some(trusted) = self.trusted.as_ref() {
            if trusted.matches(url) {
                return NavigationDecision::Allow;
            }
            if trusted.can_upgrade_to(url) {
                self.trusted = Some(candidate.clone());
                self.expected = Some(candidate);
                return NavigationDecision::Allow;
            }
            return NavigationDecision::External;
        }

        if let Some(expected) = self.expected.as_ref() {
            if expected.matches(url) {
                return NavigationDecision::Allow;
            }
            if expected.can_upgrade_to(url) {
                self.expected = Some(candidate);
                return NavigationDecision::Allow;
            }
            // A remote server may traverse an HTTPS identity-provider chain before
            // returning to the selected InfoMancer origin. Keep that bootstrap flow
            // inside the webview until the expected origin itself finishes loading.
            // Local mode has no such requirement and therefore fails closed sooner.
            if !expected.is_loopback() && url.scheme() == "https" {
                return NavigationDecision::Allow;
            }
            return NavigationDecision::External;
        }

        self.expected = Some(candidate);
        NavigationDecision::Allow
    }

    fn page_loaded(&mut self, url: &Url) {
        let Some(expected) = self.expected.as_ref() else {
            return;
        };
        if expected.matches(url) {
            self.trusted = TrustedOrigin::from_url(url);
            return;
        }
        if expected.can_upgrade_to(url) {
            let upgraded = TrustedOrigin::from_url(url);
            self.expected = upgraded.clone();
            self.trusted = upgraded;
        }
    }
}

fn safe_external_url(url: &Url) -> bool {
    matches!(url.scheme(), "http" | "https")
        && url.host_str().is_some()
        && url.username().is_empty()
        && url.password().is_none()
}

fn is_launcher_url(url: &Url) -> bool {
    if !url.username().is_empty() || url.password().is_some() {
        return false;
    }
    let host = url.host_str().map(str::to_ascii_lowercase);
    match url.scheme() {
        "tauri" => matches!(host.as_deref(), Some("localhost") | Some("tauri.localhost")),
        "http" | "https" => matches!(host.as_deref(), Some("tauri.localhost")),
        _ => false,
    }
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
    let navigation_policy = Arc::new(Mutex::new(NavigationPolicy::default()));
    let page_policy = Arc::clone(&navigation_policy);
    let navigation_policy_for_navigation = Arc::clone(&navigation_policy);

    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("InfoMancer")
        .inner_size(1440.0, 900.0)
        .min_inner_size(960.0, 640.0)
        .center()
        .resizable(true)
        // Keep the native shell hidden until the launcher's HTML is actually ready.
        // This avoids exposing an empty GTK/WebView window before the InfoMancer
        // startup splash can paint.
        .visible(false)
        // Paint the native window and WebView dark before index.html exists. This
        // prevents the first white frame WebView2 otherwise shows while booting.
        .background_color(Color(8, 12, 16, 255))
        .initialization_script(DESKTOP_EXTERNAL_LINK_BRIDGE)
        .on_page_load(move |window, payload| {
            if matches!(payload.event(), PageLoadEvent::Finished) {
                if let Ok(mut policy) = page_policy.lock() {
                    policy.page_loaded(payload.url());
                }
                if let Err(error) = window.show() {
                    eprintln!("InfoMancer window show error: {error}");
                }
                let _ = window.set_focus();
            }
        })
        .on_navigation(move |url| {
            let decision = navigation_policy_for_navigation
                .lock()
                .map(|mut policy| policy.decide(url))
                .unwrap_or(NavigationDecision::Deny);
            match decision {
                NavigationDecision::Allow => true,
                NavigationDecision::External => {
                    if let Err(error) = launch(url) {
                        eprintln!("InfoMancer external link error: {error}");
                    }
                    false
                }
                NavigationDecision::Deny => false,
            }
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
    fn launcher_navigation_does_not_claim_the_server_origin() {
        let mut policy = NavigationPolicy::default();
        assert_eq!(
            policy.decide(&"tauri://localhost/index.html".parse().unwrap()),
            NavigationDecision::Allow
        );
        assert!(policy.expected.is_none());
        assert!(policy.trusted.is_none());
    }

    #[test]
    fn selected_server_becomes_trusted_only_after_it_finishes_loading() {
        let mut policy = NavigationPolicy::default();
        let server: Url = "http://127.0.0.1:8787/library".parse().unwrap();
        assert_eq!(policy.decide(&server), NavigationDecision::Allow);
        assert!(policy.trusted.is_none());
        policy.page_loaded(&server);
        assert_eq!(policy.trusted, TrustedOrigin::from_url(&server));
        assert_eq!(
            policy.decide(&"http://127.0.0.1:8787/movies".parse().unwrap()),
            NavigationDecision::Allow
        );
    }

    #[test]
    fn trusted_server_sends_cross_origin_navigation_to_system_browser() {
        let mut policy = NavigationPolicy::default();
        let server: Url = "https://media.example.test/library".parse().unwrap();
        assert_eq!(policy.decide(&server), NavigationDecision::Allow);
        policy.page_loaded(&server);
        assert_eq!(
            policy.decide(&"https://elsewhere.example.test/".parse().unwrap()),
            NavigationDecision::External
        );
        assert_eq!(
            policy.decide(&"https://media.example.test:8443/".parse().unwrap()),
            NavigationDecision::External
        );
    }

    #[test]
    fn remote_bootstrap_can_traverse_https_identity_redirects_before_pin() {
        let mut policy = NavigationPolicy::default();
        let server: Url = "https://media.example.test/".parse().unwrap();
        assert_eq!(policy.decide(&server), NavigationDecision::Allow);
        assert_eq!(
            policy.decide(&"https://identity.example.test/login".parse().unwrap()),
            NavigationDecision::Allow
        );
        assert!(policy.trusted.is_none());
        policy.page_loaded(&server);
        assert_eq!(
            policy.decide(&"https://identity.example.test/login".parse().unwrap()),
            NavigationDecision::External
        );
    }

    #[test]
    fn local_bootstrap_does_not_allow_cross_origin_redirects() {
        let mut policy = NavigationPolicy::default();
        let server: Url = "http://localhost:8787/".parse().unwrap();
        assert_eq!(policy.decide(&server), NavigationDecision::Allow);
        assert_eq!(
            policy.decide(&"https://identity.example.test/login".parse().unwrap()),
            NavigationDecision::External
        );
    }

    #[test]
    fn same_host_http_to_https_upgrade_is_allowed_and_pinned() {
        let mut policy = NavigationPolicy::default();
        let http: Url = "http://media.example.test/".parse().unwrap();
        let https: Url = "https://media.example.test/".parse().unwrap();
        assert_eq!(policy.decide(&http), NavigationDecision::Allow);
        assert_eq!(policy.decide(&https), NavigationDecision::Allow);
        policy.page_loaded(&https);
        assert_eq!(policy.trusted, TrustedOrigin::from_url(&https));
    }

    #[test]
    fn unsafe_navigation_fails_closed() {
        let mut policy = NavigationPolicy::default();
        assert_eq!(
            policy.decide(&"file:///tmp/test".parse().unwrap()),
            NavigationDecision::Deny
        );
        assert_eq!(
            policy.decide(&"https://user:pass@example.test/".parse().unwrap()),
            NavigationDecision::Deny
        );
    }

    #[test]
    fn desktop_bridge_primes_a_dark_document_before_page_css() {
        assert!(DESKTOP_EXTERNAL_LINK_BRIDGE.contains("backgroundColor = '#080c10'"));
        assert!(DESKTOP_EXTERNAL_LINK_BRIDGE.contains("colorScheme = 'dark'"));
    }
}

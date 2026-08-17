# Security Policy

InfoMancer is pre-release software that can inspect and, after explicit approval, modify files inside configured media roots. Security-sensitive deployments should track the newest supported alpha rather than older preview builds.

## Deployment boundaries

- Do not expose the InfoMancer origin port directly to the public Internet. Keep the origin private and place an authenticated reverse proxy such as Cloudflare Access in front of any public hostname.
- Local authentication is the normal server default. `INFOMANCER_AUTH_MODE=disabled` deliberately removes account authentication and is intended only for a trusted private or loopback deployment. Do not use disabled authentication on an untrusted network.
- The dedicated desktop build starts its bundled local core on loopback only. Remote-server mode should use HTTPS whenever the server leaves a trusted private network.
- Keep media mounts, the application-data directory, backup destinations, and updater credentials accessible only to the operating-system accounts that need them.
- Keep Read-Only Mode enabled whenever you are evaluating an unfamiliar installation or do not intend InfoMancer to change media files.

## Update and dependency trust

Release automation pins third-party GitHub Actions by immutable commit SHA. Published desktop updates are expected to be signed, and the host updater verifies signed Git tags against explicitly trusted signing-key fingerprints before changing a checkout.

CI audits Python, Rust, and desktop JavaScript dependencies. A passing audit reduces known dependency risk but is not a guarantee that a build is vulnerability-free.

## Reporting a vulnerability

Do not publish exploit details, credentials, private media paths, databases, recovery packages, or production logs in a public issue. If private vulnerability reporting is available for this repository, use it. Otherwise contact the repository owner privately before disclosing details publicly.

Useful reports include:

- the affected InfoMancer version or commit;
- deployment model, such as Docker, desktop, LAN-only, or reverse-proxied;
- concise reproduction steps;
- the security impact and required attacker access;
- sanitized logs or examples that contain no credentials or private library data.

High-value areas include authentication and session boundaries, CSRF and origin handling, filesystem containment, rename and undo safety, recovery and restore integrity, provider-secret storage, desktop native boundaries, and dependency or update integrity.

## Secrets and diagnostic data

Never commit or attach API keys, tunnel tokens, signing keys, `.env` files, databases, recovery packages, or provider credential stores. Diagnostic and event data can still contain filenames, media paths, hostnames, IP addresses, or other installation-specific details even when credential fields are redacted. Review diagnostic material before sharing it.

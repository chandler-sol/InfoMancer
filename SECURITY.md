# Security

InfoMancer can rename files in configured media roots. Do not expose it
directly to the Internet. Keep its origin port loopback-only and place a
restrictive Cloudflare Access policy in front of any public hostname.

Never include API keys, tunnel tokens, environment files, databases, media
paths containing private information, or production logs in an issue or
commit. If this repository is later made public, report suspected
vulnerabilities privately through GitHub's private vulnerability reporting
feature rather than a public issue.

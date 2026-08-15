from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new, 1))


def replace_count(path: str, old: str, new: str, expected: int) -> None:
    content = read(path)
    count = content.count(old)
    if count != expected:
        raise RuntimeError(
            f"Expected {expected} matches in {path}, found {count}: {old[:120]!r}"
        )
    write(path, content.replace(old, new))


# GitHub Actions should never persist the checkout token for normal test jobs.
replace_count(
    ".github/workflows/tests.yml",
    "        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4\n",
    "        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4\n"
    "        with:\n"
    "          persist-credentials: false\n",
    2,
)

# Shared request-security helpers: Unicode-safe constant-time comparisons,
# production Host handling, and a clear no-session rule for cookie-less API use.
replace_once(
    "app/request_security.py",
    "from __future__ import annotations\n\nfrom urllib.parse import parse_qs, urlsplit\n",
    "from __future__ import annotations\n\nimport hmac\nfrom urllib.parse import parse_qs, urlsplit\n",
)
replace_once(
    "app/request_security.py",
    'LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}\n',
    'LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}\n',
)
replace_once(
    "app/request_security.py",
    "class RequestBodyTooLarge(ValueError):\n    pass\n\n",
    "class RequestBodyTooLarge(ValueError):\n    pass\n\n\n"
    "def constant_time_equal(left: str, right: str) -> bool:\n"
    "    \"\"\"Compare arbitrary text tokens without ASCII-only compare_digest failures.\"\"\"\n"
    "    return hmac.compare_digest(left.encode(\"utf-8\"), right.encode(\"utf-8\"))\n\n\n"
    "def should_issue_session_cookie(path: str) -> bool:\n"
    "    \"\"\"Avoid durable DB sessions for cookie-less API/service-token traffic.\"\"\"\n"
    "    return not path.startswith(\"/api/\")\n\n",
)
replace_once(
    "app/request_security.py",
    "    if not enforce:\n        return True\n    return _hostname(request.headers.get(\"host\", \"\")) in allowed_hosts(settings)\n",
    "    if not enforce:\n        return True\n"
    "    host = _hostname(request.headers.get(\"host\", \"\"))\n"
    "    # Starlette's TestClient uses these two sentinels. Neither value is\n"
    "    # accepted from a real network peer solely because Host says testserver.\n"
    "    if (\n"
    "        host == \"testserver\" and request.client\n"
    "        and request.client.host == \"testclient\"\n"
    "    ):\n"
    "        return True\n"
    "    return host in allowed_hosts(settings)\n",
)

# Bootstrap token comparisons must reject Unicode input rather than raising.
replace_once(
    "app/bootstrap.py",
    "import hmac\nimport os\n",
    "import os\n",
)
replace_once(
    "app/bootstrap.py",
    "from pathlib import Path\n\n\nclass BootstrapTokenManager:\n",
    "from pathlib import Path\n\nfrom .request_security import constant_time_equal\n\n\nclass BootstrapTokenManager:\n",
)
replace_once(
    "app/bootstrap.py",
    "        return bool(submitted and hmac.compare_digest(submitted, expected))\n",
    "        return bool(submitted and constant_time_equal(submitted, expected))\n",
)

# Cookie decisions must describe the current request, not merely the configured
# public URL. Also keep actively locked login rows outside the retention cap.
replace_once(
    "app/auth.py",
    'ROLES = {"member", "librarian"}\n\npassword_hasher',
    'ROLES = {"member", "librarian"}\nLOGIN_ATTEMPT_ROW_CAP = 5000\n\npassword_hasher',
)
replace_once(
    "app/auth.py",
    '''def secure_cookie_for(request, settings: Settings) -> bool:
    if settings.cookie_secure == "true":
        return True
    if settings.cookie_secure == "false":
        return False
    if settings.public_url:
        try:
            if urlsplit(settings.public_url).scheme.casefold() == "https":
                return True
        except ValueError:
            pass
    if getattr(getattr(request, "url", None), "scheme", "") == "https":
        return True
    if _trusted_cloudflare_proxy(request, settings):
        forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        return forwarded == "https"
    return False
''',
    '''def secure_cookie_for(request, settings: Settings) -> bool:
    if settings.cookie_secure == "true":
        return True
    if settings.cookie_secure == "false":
        return False
    if getattr(getattr(request, "url", None), "scheme", "") == "https":
        return True
    if settings.public_url:
        try:
            public = urlsplit(settings.public_url)
            request_host = urlsplit(
                f"//{request.headers.get('host', '')}"
            ).hostname
            public_host = public.hostname
            if (
                public.scheme.casefold() == "https"
                and request_host and public_host
                and request_host.casefold().rstrip(".")
                == public_host.casefold().rstrip(".")
            ):
                return True
        except ValueError:
            pass
    if _trusted_cloudflare_proxy(request, settings):
        forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        return forwarded == "https"
    return False
''',
)
replace_once(
    "app/auth.py",
    '''def normalize_email(value: str) -> str:
    return value.strip().casefold()


def safe_next''',
    '''def normalize_email(value: str) -> str:
    return value.strip().casefold()


def _prune_login_attempts(conn: sqlite3.Connection) -> None:
    """Bound stale login-attempt storage without deleting active lockouts."""
    conn.execute(
        "DELETE FROM login_attempts WHERE datetime(last_attempt_at)<datetime('now','-1 day')"
    )
    conn.execute(
        """DELETE FROM login_attempts WHERE rowid IN (
             SELECT rowid FROM login_attempts
             WHERE locked_until IS NULL
                OR datetime(locked_until)<=CURRENT_TIMESTAMP
             ORDER BY datetime(last_attempt_at) DESC,rowid DESC
             LIMIT -1 OFFSET ?
           )""",
        (LOGIN_ATTEMPT_ROW_CAP,),
    )


def safe_next''',
)
replace_once(
    "app/auth.py",
    '''        with self.database.connect() as conn:
            conn.execute(
                "DELETE FROM login_attempts WHERE datetime(last_attempt_at)<datetime('now','-1 day')"
            )
            conn.execute(
                """DELETE FROM login_attempts WHERE rowid IN (
                     SELECT rowid FROM login_attempts
                     ORDER BY datetime(last_attempt_at) DESC,rowid DESC
                     LIMIT -1 OFFSET 5000
                   )"""
            )
            attempt = conn.execute(''',
    '''        with self.database.connect() as conn:
            _prune_login_attempts(conn)
            attempt = conn.execute(''',
)
replace_once(
    "app/auth.py",
    '''                conn.execute(
                    """DELETE FROM login_attempts WHERE rowid IN (
                         SELECT rowid FROM login_attempts
                         ORDER BY datetime(last_attempt_at) DESC,rowid DESC
                         LIMIT -1 OFFSET 5000
                       )"""
                )
                failure = True''',
    '''                _prune_login_attempts(conn)
                failure = True''',
)

# Main middleware: do not create sessions for cookie-less API traffic, use
# Unicode-safe token comparisons, and preserve Member library exports.
replace_once(
    "app/main.py",
    '''from .request_security import (
    LOCAL_CSRF_COOKIE, RequestBodyTooLarge, browser_request_is_same_origin,
    csrf_submission, host_is_allowed, replay_body,
)''',
    '''from .request_security import (
    LOCAL_CSRF_COOKIE, RequestBodyTooLarge, browser_request_is_same_origin,
    constant_time_equal, csrf_submission, host_is_allowed, replay_body,
    should_issue_session_cookie,
)''',
)
replace_once(
    "app/main.py",
    '''                if not existing or existing.user.id != user.id:
                    new_session_token, existing = auth_service.create_session(user, request)
                request.state.user = user''',
    '''                if (
                    (not existing or existing.user.id != user.id)
                    and should_issue_session_cookie(path)
                ):
                    new_session_token, existing = auth_service.create_session(user, request)
                request.state.user = user''',
)
replace_once(
    "app/main.py",
    "                    or not hmac.compare_digest(submitted, local_csrf)\n",
    "                    or not constant_time_equal(submitted, local_csrf)\n",
)
replace_once(
    "app/main.py",
    '''                if not submitted or not hmac.compare_digest(
                    submitted, session.csrf_token
                ):''',
    '''                if not submitted or not constant_time_equal(
                    submitted, session.csrf_token
                ):''',
)
replace_once(
    "app/main.py",
    "    return bool(stored and submitted and hmac.compare_digest(stored, submitted))\n",
    "    return bool(stored and submitted and constant_time_equal(stored, submitted))\n",
)
replace_once(
    "app/main.py",
    '@librarian_get("/exports/library")\n',
    '@app.get("/exports/library")\n',
)

# Fingerprinting must always leave a terminal state on unexpected failure.
replace_once(
    "app/background.py",
    '''        result = self.media_hashes.hash_many(
            ids, progress=progress, cancelled=self.media_hash_cancel.is_set,
            paused=lambda: self.media_hash_pause.is_set() or (
                self.app_settings.get("hash_pause_for_activity") == "1"
                and self.other_background_work_running()
            ),
            intensity=self.app_settings.get("hash_io_intensity"),
        )
        status = "cancelled" if self.media_hash_cancel.is_set() else "complete"
''',
    '''        try:
            result = self.media_hashes.hash_many(
                ids, progress=progress, cancelled=self.media_hash_cancel.is_set,
                paused=lambda: self.media_hash_pause.is_set() or (
                    self.app_settings.get("hash_pause_for_activity") == "1"
                    and self.other_background_work_running()
                ),
                intensity=self.app_settings.get("hash_io_intensity"),
            )
        except Exception as exc:
            error = str(exc)[:1000]
            with self.media_hash_lock:
                self.media_hash_job.update({
                    "status": "error", "current": "", "error": error,
                })
            self.record_event(
                "media",
                "File fingerprinting stopped because of an unexpected error. "
                "Open Logs for details.",
                level="error", context={"reason": reason, "error": error},
            )
            return
        status = "cancelled" if self.media_hash_cancel.is_set() else "complete"
''',
)

# TV enrichment should not claim completeness when the provider title is absent.
replace_once(
    "app/title_metadata.py",
    '''        if all((title["poster_url"], title["imdb_id"], title["metadata_title_language"], title["overview"])):
            return False
''',
    '''        if all((
            title["poster_url"], title["imdb_id"], title["metadata_title"],
            title["metadata_title_language"], title["overview"],
        )):
            return False
''',
)

# Non-root containers should map their runtime identity to the host account that
# owns bind mounts, rather than assuming UID/GID 1000 on every Linux host.
write(
    "Dockerfile",
    '''FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG INFOMANCER_UID=1000
ARG INFOMANCER_GID=1000
RUN apt-get update \\
    && apt-get install -y --no-install-recommends ffmpeg \\
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid "${INFOMANCER_GID}" infomancer \\
    && useradd --uid "${INFOMANCER_UID}" --gid infomancer --create-home --shell /bin/false infomancer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=infomancer:infomancer app app
RUN mkdir -p /app/data && chown -R infomancer:infomancer /app/data
USER infomancer
EXPOSE 8787
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--no-proxy-headers"]
''',
)
replace_once(
    "compose.yaml",
    "    build: .\n",
    '''    build:
      context: .
      args:
        INFOMANCER_UID: ${INFOMANCER_UID:-1000}
        INFOMANCER_GID: ${INFOMANCER_GID:-1000}
''',
)
replace_once(
    "compose.sandbox.yaml",
    "    build: .\n",
    '''    build:
      context: .
      args:
        INFOMANCER_UID: ${INFOMANCER_UID:-1000}
        INFOMANCER_GID: ${INFOMANCER_GID:-1000}
''',
)
replace_once(
    ".env.example",
    "INFOMANCER_DATABASE=data/infomancer.db\n",
    '''INFOMANCER_DATABASE=data/infomancer.db
# Docker Linux builds run the application as this non-root UID/GID. Set these
# to the host account that owns data/ and any media folders InfoMancer may edit.
INFOMANCER_UID=1000
INFOMANCER_GID=1000
''',
)
replace_once(
    "deploy/linux.compose.yaml.example",
    '''      # Change the source paths to mounted folders on this Linux host.
      # The account running Docker must be able to read them.
''',
    '''      # Change the source paths to mounted folders on this Linux host.
      # They must be readable by INFOMANCER_UID/INFOMANCER_GID. Grant write
      # access as well for rename, organize, and managed-trash operations.
''',
)
replace_once(
    "docs/INSTALLATION.md",
    "3. Write access to a media folder only if you want InfoMancer to rename files.\n",
    "3. Write access to a media folder only if you want InfoMancer to rename, organize, or move duplicate files into managed Trash.\n",
)
replace_once(
    "docs/INSTALLATION.md",
    '''4. Replace `/mnt/media/movies` and `/mnt/media/tv` with your mounted storage.
5. Start InfoMancer:
''',
    '''4. Replace `/mnt/media/movies` and `/mnt/media/tv` with your mounted storage.
5. Match the container's non-root account to your Linux user and create the
   writable application-data directory before Docker sees the bind mount:

   ```bash
   sed -i "s/^INFOMANCER_UID=.*/INFOMANCER_UID=$(id -u)/" .env
   sed -i "s/^INFOMANCER_GID=.*/INFOMANCER_GID=$(id -g)/" .env
   mkdir -p data
   ```

   The same UID/GID must be able to read every media mapping. Grant it write
   permission only on locations where you want InfoMancer to rename, organize,
   restore, or move files into managed Trash. Group permissions or ACLs are
   preferable to changing ownership of a shared media library.
6. Start InfoMancer:
''',
)
replace_once(
    "docs/INSTALLATION.md",
    "6. Open `http://127.0.0.1:8787`. When the Linux host has no desktop, create an\n",
    "7. Open `http://127.0.0.1:8787`. When the Linux host has no desktop, create an\n",
)
replace_once(
    "docs/INSTALLATION.md",
    "7. Create the Librarian and finish Guided Setup.\n",
    "8. Create the Librarian and finish Guided Setup.\n",
)

# A valid signature is insufficient unless it belongs to an explicitly trusted
# InfoMancer release key.
replace_once(
    "scripts/host_updater.py",
    '''def verify_release_tag(
    tag: str, repository: Path, trusted_signing_keys: set[str] | None = None,
) -> None:
    completed = subprocess.run(
''',
    '''def normalize_trusted_signing_keys(values: set[str] | None) -> set[str]:
    """Normalize and validate the explicit release-signing trust boundary."""
    trusted = {
        value.replace(" ", "").upper()
        for value in (values or set())
        if value and FINGERPRINT_PATTERN.fullmatch(value.replace(" ", ""))
    }
    if not trusted:
        raise UpdateError(
            "At least one trusted InfoMancer release signing-key fingerprint is required."
        )
    return trusted


def verify_release_tag(
    tag: str, repository: Path, trusted_signing_keys: set[str] | None = None,
) -> None:
    trusted = normalize_trusted_signing_keys(trusted_signing_keys)
    completed = subprocess.run(
''',
)
replace_once(
    "scripts/host_updater.py",
    '''    trusted = {
        value.replace(" ", "").upper()
        for value in (trusted_signing_keys or set())
        if value and FINGERPRINT_PATTERN.fullmatch(value.replace(" ", ""))
    }
    if trusted_signing_keys and not trusted:
        raise UpdateError("No configured release signing-key fingerprint is valid.")
    if trusted and fingerprints.isdisjoint(trusted):
''',
    '''    if fingerprints.isdisjoint(trusted):
''',
)
replace_once(
    "scripts/host_updater.py",
    '        help="Allowed primary or signing-subkey GPG fingerprint. May be supplied more than once.",\n',
    '        help="Required trusted primary or signing-subkey GPG fingerprint. May be supplied more than once.",\n',
)
replace_once(
    "scripts/host_updater.py",
    '''    files = arguments.compose_files or ["compose.yaml"]
    while True:
        handled = process_request(
            repository, data_directory, files,
            arguments.health_url, max(15, arguments.health_timeout),
            {value for value in arguments.trusted_signing_key if value.strip()},
        )
''',
    '''    files = arguments.compose_files or ["compose.yaml"]
    try:
        trusted_signing_keys = normalize_trusted_signing_keys(
            {value for value in arguments.trusted_signing_key if value.strip()}
        )
    except UpdateError as exc:
        print(f"Updater configuration error: {exc}", file=sys.stderr)
        return 2
    while True:
        handled = process_request(
            repository, data_directory, files,
            arguments.health_url, max(15, arguments.health_timeout),
            trusted_signing_keys,
        )
''',
)
replace_once(
    "docs/UPDATES.md",
    '''For an additional trust boundary, start the helper with the full expected
signing-key fingerprint:
''',
    '''The host updater also requires the full expected signing-key fingerprint.
A valid signature from some other key in the service account's GPG keyring is
not sufficient:
''',
)
replace_once(
    "docs/UPDATES.md",
    '''The option may be supplied more than once during a signing-key rotation. When
one or more fingerprints are configured, a cryptographically valid tag is
accepted only when its `VALIDSIG` fingerprint matches that allowlist.
''',
    '''The option may be supplied more than once during a signing-key rotation. The
helper refuses to start without at least one valid fingerprint, and a
cryptographically valid tag is accepted only when its `VALIDSIG` fingerprint
matches that allowlist.
''',
)
replace_once(
    "docs/UPDATES.md",
    '''   `--compose-file` values to match the installation. Add
   `--trusted-signing-key FULL_GPG_FINGERPRINT` when you want to restrict
   updates to an explicit release key.
''',
    '''   `--compose-file` values to match the installation. Replace the example
   `--trusted-signing-key FULL_GPG_FINGERPRINT` value with the verified full
   fingerprint of the InfoMancer release key.
''',
)
replace_once(
    "deploy/infomancer-updater.service.example",
    "ExecStart=/usr/bin/python3 /opt/infomancer/scripts/host_updater.py --watch --compose-file compose.yaml --compose-file compose.media.yaml\n",
    "ExecStart=/usr/bin/python3 /opt/infomancer/scripts/host_updater.py --watch --compose-file compose.yaml --compose-file compose.media.yaml --trusted-signing-key FULL_GPG_FINGERPRINT\n",
)

# Record the Member export decision at route level.
replace_once(
    "tests/test_route_authorization.py",
    '''            ("/account/profile", "GET"), ("/account/profile", "POST"),
            ("/titles/{title_id}/favorite", "POST"),
''',
    '''            ("/account/profile", "GET"), ("/account/profile", "POST"),
            ("/exports/library", "GET"),
            ("/titles/{title_id}/favorite", "POST"),
''',
)

# Host-updater tests now always model the mandatory trust allowlist.
replace_once(
    "tests/test_host_updater.py",
    '''            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary))
''',
    '''            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary), {"A" * 40})
''',
)
replace_once(
    "tests/test_host_updater.py",
    '''    def test_trusted_signature_fingerprint_is_enforced(self):
''',
    '''    def test_valid_signature_without_trusted_fingerprint_is_rejected(self):
        fingerprint = "A" * 40
        completed = subprocess.CompletedProcess(
            ["git"], 0, "",
            f"[GNUPG:] VALIDSIG {fingerprint} 2026-01-01 0 4 0 1 10 00 {fingerprint}",
        )
        with tempfile.TemporaryDirectory() as temporary, patch(
            "scripts.host_updater.subprocess.run", return_value=completed
        ):
            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary))

    def test_trusted_signature_fingerprint_is_enforced(self):
''',
)
replace_once(
    "tests/test_host_updater.py",
    '''            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary))


if __name__ == "__main__":
''',
    '''            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary), {"A" * 40})


if __name__ == "__main__":
''',
)

# Ruff's reported E702.
replace_once(
    "tests/test_migrations.py",
    "            conn.commit(); conn.close()\n",
    "            conn.commit()\n            conn.close()\n",
)

# Extend security and supply-chain contracts.
replace_once(
    "tests/test_security_hardening.py",
    '''from app.request_security import (
    RequestBodyTooLarge, browser_request_is_same_origin, csrf_submission,
    host_is_allowed,
)
''',
    '''from app.request_security import (
    RequestBodyTooLarge, browser_request_is_same_origin, constant_time_equal,
    csrf_submission, host_is_allowed, should_issue_session_cookie,
)
''',
)
replace_once(
    "tests/test_security_hardening.py",
    '''    def test_configured_token_does_not_create_a_file(self):
''',
    '''    def test_non_ascii_token_is_rejected_without_type_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = BootstrapTokenManager(
                Path(temporary) / "bootstrap-token", "configured-secret"
            )
            self.assertFalse(manager.verify("é"))
            self.assertFalse(constant_time_equal("é", "configured-secret"))

    def test_configured_token_does_not_create_a_file(self):
''',
)
replace_once(
    "tests/test_security_hardening.py",
    '''    def test_verified_cloudflare_request_can_use_cloudflare_headers(self):
''',
    '''    def test_https_public_url_does_not_break_plain_lan_cookie(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="local")
            settings = Settings(**{
                **settings.__dict__,
                "public_url": "https://media.example.test",
            })
            lan = request_with(headers={"host": "127.0.0.1:8787"}, scheme="http")
            public = request_with(headers={"host": "media.example.test"}, scheme="http")
            self.assertFalse(secure_cookie_for(lan, settings))
            self.assertTrue(secure_cookie_for(public, settings))

    def test_verified_cloudflare_request_can_use_cloudflare_headers(self):
''',
)
replace_once(
    "tests/test_security_hardening.py",
    '''            self.assertTrue(host_is_allowed(
                request_with(headers={"host": "127.0.0.1:8787"}), settings
            ))
''',
    '''            self.assertTrue(host_is_allowed(
                request_with(headers={"host": "127.0.0.1:8787"}), settings
            ))
            self.assertFalse(host_is_allowed(
                request_with(headers={"host": "testserver"}), settings
            ))
''',
)
replace_once(
    "tests/test_security_hardening.py",
    '''class ContainerHardeningTests(unittest.TestCase):
''',
    '''class SessionIssuanceTests(unittest.TestCase):
    def test_cookie_less_api_requests_do_not_issue_database_sessions(self):
        self.assertFalse(should_issue_session_cookie("/api/tasks"))
        self.assertFalse(should_issue_session_cookie("/api/dashboard-metrics"))
        self.assertTrue(should_issue_session_cookie("/movies"))


class ContainerHardeningTests(unittest.TestCase):
''',
)
replace_once(
    "tests/test_security_hardening.py",
    '''        self.assertNotIn('"--forwarded-allow-ips", "*"', dockerfile)
''',
    '''        self.assertNotIn('"--forwarded-allow-ips", "*"', dockerfile)
        self.assertIn("ARG INFOMANCER_UID=1000", dockerfile)
        self.assertIn("ARG INFOMANCER_GID=1000", dockerfile)
        compose = (Path(__file__).resolve().parent.parent / "compose.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("INFOMANCER_UID: ${INFOMANCER_UID:-1000}", compose)
        self.assertIn("INFOMANCER_GID: ${INFOMANCER_GID:-1000}", compose)
''',
)
replace_once(
    "tests/test_supply_chain.py",
    '''    def test_github_actions_use_full_commit_shas(self):
''',
    '''    def test_checkout_does_not_persist_ci_credentials(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count("persist-credentials: false"), 2)

    def test_github_actions_use_full_commit_shas(self):
''',
)

# Preserve active per-pair lockout rows even when the unlocked-row cap is tiny.
replace_once(
    "tests/test_auth.py",
    "from types import SimpleNamespace\n\nfrom app.auth",
    "from types import SimpleNamespace\nfrom unittest.mock import patch\n\nfrom app.auth",
)
replace_once(
    "tests/test_auth.py",
    '''    def test_sessions_store_only_token_hash_and_can_be_revoked(self):
''',
    '''    def test_row_cap_never_deletes_active_lockout(self):
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO login_attempts
                   (identity,ip_address,failures,last_attempt_at,locked_until)
                   VALUES ('locked','192.0.2.10',5,CURRENT_TIMESTAMP,
                           datetime('now','+15 minutes'))"""
            )
            for index in range(4):
                conn.execute(
                    """INSERT INTO login_attempts
                       (identity,ip_address,failures,last_attempt_at)
                       VALUES (?,?,1,CURRENT_TIMESTAMP)""",
                    (f"other-{index}", f"198.51.100.{index + 1}"),
                )
        with patch("app.auth.LOGIN_ATTEMPT_ROW_CAP", 2):
            with self.assertRaises(AuthenticationError):
                self.auth.authenticate_local("new-user", "wrong", "203.0.113.8")
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT locked_until FROM login_attempts WHERE identity='locked'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(row["locked_until"])

    def test_sessions_store_only_token_hash_and_can_be_revoked(self):
''',
)

# Focused behavior tests for background failure recovery and TV title enrichment.
write(
    "tests/test_coderabbit_followup.py",
    '''from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.background import BackgroundCoordinator
from app.db import Database
from app.title_metadata import TitleMetadataService


class _HashSettings:
    def get(self, key: str) -> str:
        return {
            "hash_pause_for_activity": "0",
            "hash_io_intensity": "balanced",
        }.get(key, "")


class _FailingHashes:
    def hash_many(self, *_args, **_kwargs):
        raise RuntimeError("synthetic hashing failure")


class BackgroundFailureTests(unittest.TestCase):
    def test_hash_exception_publishes_terminal_error_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            events = []
            coordinator = BackgroundCoordinator(
                database, _HashSettings(), _FailingHashes(), object(),
                lambda *args, **kwargs: events.append((args, kwargs)),
            )
            coordinator.run_media_hashing([1], "test")
            self.assertEqual(coordinator.media_hash_job["status"], "error")
            self.assertEqual(coordinator.media_hash_job["current"], "")
            self.assertIn("synthetic hashing failure", coordinator.media_hash_job["error"])
            self.assertTrue(events)
            self.assertEqual(events[-1][1]["level"], "error")


class _TVDB:
    def series(self, _series_id):
        return {"id": 77, "name": "Recovered Provider Title", "overview": "Existing overview"}


class TitleMetadataFollowupTests(unittest.TestCase):
    def test_missing_provider_title_prevents_false_complete_short_circuit(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES ('/tv','tv','TV')"
                ).lastrowid
                title_id = conn.execute(
                    """INSERT INTO titles(
                       root_id,kind,title,folder_path,tvdb_id,poster_url,imdb_id,
                       metadata_title,metadata_title_language,overview
                       ) VALUES (?,'tv','Example','/tv/example',77,'poster','tt0000077',
                                 '','eng','Existing overview')""",
                    (root_id,),
                ).lastrowid
            service = TitleMetadataService(
                database, _TVDB(),
                poster_from=lambda _series: "poster",
                plex_movie_ids=lambda _series: ("", "tt0000077"),
                localized_title=lambda _series, _current: ("Recovered Provider Title", "eng"),
                match_confidence=lambda *_args: {},
            )
            self.assertTrue(service.enrich(title_id))
            with database.connect() as conn:
                title = conn.execute(
                    "SELECT metadata_title FROM titles WHERE id=?", (title_id,)
                ).fetchone()
            self.assertEqual(title["metadata_title"], "Recovered Provider Title")


if __name__ == "__main__":
    unittest.main()
''',
)

print("CodeRabbit follow-up patch applied.")

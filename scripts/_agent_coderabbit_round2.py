from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


# Persist aggregate identity and IP lockouts outside the prunable attempt ledger.
replace_once(
    "app/migrations.py",
    '''def _runtime_lease(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS runtime_leases (
             name TEXT PRIMARY KEY,
             owner TEXT NOT NULL,
             heartbeat_at TEXT NOT NULL
           )"""
    )


MIGRATIONS = (
''',
    '''def _runtime_lease(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS runtime_leases (
             name TEXT PRIMARY KEY,
             owner TEXT NOT NULL,
             heartbeat_at TEXT NOT NULL
           )"""
    )


def _login_lockouts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS login_lockouts (
             scope TEXT NOT NULL CHECK(scope IN ('identity','ip')),
             lock_key TEXT NOT NULL,
             locked_until TEXT NOT NULL,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             PRIMARY KEY(scope,lock_key)
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_login_lockouts_until ON login_lockouts(locked_until)"
    )


MIGRATIONS = (
''',
)
replace_once(
    "app/migrations.py",
    '''    Migration(10, "single-runtime lease", _runtime_lease),
)''',
    '''    Migration(10, "single-runtime lease", _runtime_lease),
    Migration(11, "persistent aggregate login lockouts", _login_lockouts),
)''',
)

replace_once(
    "app/auth.py",
    '''def _prune_login_attempts(conn: sqlite3.Connection) -> None:
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
    '''def _prune_login_attempts(conn: sqlite3.Connection) -> None:
    """Bound stale login-attempt storage without deleting active pair locks."""
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


def _prune_login_lockouts(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM login_lockouts WHERE datetime(locked_until)<=CURRENT_TIMESTAMP"
    )


def _has_login_lock(conn: sqlite3.Connection, scope: str, lock_key: str) -> bool:
    return conn.execute(
        """SELECT 1 FROM login_lockouts
           WHERE scope=? AND lock_key=? AND datetime(locked_until)>CURRENT_TIMESTAMP""",
        (scope, lock_key),
    ).fetchone() is not None


def _set_login_lock(conn: sqlite3.Connection, scope: str, lock_key: str) -> None:
    conn.execute(
        """INSERT INTO login_lockouts(scope,lock_key,locked_until,created_at)
           VALUES (?,?,datetime('now','+15 minutes'),CURRENT_TIMESTAMP)
           ON CONFLICT(scope,lock_key) DO UPDATE SET
             locked_until=excluded.locked_until,created_at=CURRENT_TIMESTAMP""",
        (scope, lock_key),
    )


def safe_next''',
)

start = '''    def authenticate_local(self, identity: str, password: str, ip_address: str) -> AuthUser:
'''
end = '''    def create_session(self, user: AuthUser, request) -> tuple[str, AuthSession]:
'''
text = read("app/auth.py")
start_index = text.index(start)
end_index = text.index(end, start_index)
replacement = '''    def authenticate_local(self, identity: str, password: str, ip_address: str) -> AuthUser:
        identity = identity.strip().casefold()
        if not identity or not password:
            raise AuthenticationError("Incorrect username, email, or password.")

        # Aggregate account/IP locks live outside login_attempts so retention
        # pruning cannot erase a live lockout created by distributed failures.
        precheck_locked = False
        with self.database.connect() as conn:
            _prune_login_attempts(conn)
            _prune_login_lockouts(conn)
            attempt = conn.execute(
                "SELECT * FROM login_attempts WHERE identity=? AND ip_address=?",
                (identity, ip_address),
            ).fetchone()
            pair_locked = False
            if attempt and attempt["locked_until"]:
                try:
                    pair_locked = datetime.fromisoformat(attempt["locked_until"]) > utcnow()
                except ValueError:
                    pair_locked = False
            identity_locked = _has_login_lock(conn, "identity", identity)
            ip_locked = _has_login_lock(conn, "ip", ip_address)
            if pair_locked or identity_locked or ip_locked:
                precheck_locked = True
            else:
                identity_failures = int(conn.execute(
                    """SELECT COALESCE(SUM(failures),0) FROM login_attempts
                       WHERE identity=? AND datetime(last_attempt_at)>=datetime('now','-15 minutes')""",
                    (identity,),
                ).fetchone()[0])
                ip_failures = int(conn.execute(
                    """SELECT COALESCE(SUM(failures),0) FROM login_attempts
                       WHERE ip_address=? AND datetime(last_attempt_at)>=datetime('now','-15 minutes')""",
                    (ip_address,),
                ).fetchone()[0])
                if identity_failures >= 15:
                    _set_login_lock(conn, "identity", identity)
                    precheck_locked = True
                if ip_failures >= 30:
                    _set_login_lock(conn, "ip", ip_address)
                    precheck_locked = True

        if precheck_locked:
            raise LoginLocked("Too many attempts. Try again in a few minutes.")

        failure = False
        with self.database.connect() as conn:
            # Recheck durable aggregate locks after the preflight transaction in
            # case another request crossed a threshold in the meantime.
            _prune_login_lockouts(conn)
            if (
                _has_login_lock(conn, "identity", identity)
                or _has_login_lock(conn, "ip", ip_address)
            ):
                locked_during_auth = True
            else:
                locked_during_auth = False

            if not locked_during_auth:
                attempt = conn.execute(
                    "SELECT * FROM login_attempts WHERE identity=? AND ip_address=?",
                    (identity, ip_address),
                ).fetchone()
                row = conn.execute(
                    """SELECT * FROM users
                       WHERE LOWER(username)=? OR LOWER(COALESCE(email,''))=?""",
                    (identity, identity),
                ).fetchone()
                if row and not row["active"]:
                    raise AuthenticationError(
                        "This account is disabled. Ask a Librarian to enable it before signing in."
                    )
                if row and not row["password_hash"]:
                    raise AuthenticationError(
                        "This account is waiting for setup. Ask a Librarian for a fresh one-time setup link."
                    )

                verified = False
                if row and row["password_hash"]:
                    try:
                        verified = password_hasher.verify(row["password_hash"], password)
                        if verified and password_hasher.check_needs_rehash(row["password_hash"]):
                            conn.execute(
                                "UPDATE users SET password_hash=? WHERE id=?",
                                (password_hasher.hash(password), row["id"]),
                            )
                    except (VerifyMismatchError, VerificationError, InvalidHashError):
                        verified = False

                if not verified:
                    failures = int(attempt["failures"] if attempt else 0) + 1
                    locked_until = (
                        iso_timestamp(utcnow() + timedelta(minutes=15)) if failures >= 5 else None
                    )
                    conn.execute(
                        """INSERT INTO login_attempts
                           (identity,ip_address,failures,last_attempt_at,locked_until)
                           VALUES (?,?,?,CURRENT_TIMESTAMP,?)
                           ON CONFLICT(identity,ip_address) DO UPDATE SET
                             failures=excluded.failures,last_attempt_at=CURRENT_TIMESTAMP,
                             locked_until=excluded.locked_until""",
                        (identity, ip_address, failures, locked_until),
                    )
                    identity_failures = int(conn.execute(
                        """SELECT COALESCE(SUM(failures),0) FROM login_attempts
                           WHERE identity=? AND datetime(last_attempt_at)>=datetime('now','-15 minutes')""",
                        (identity,),
                    ).fetchone()[0])
                    ip_failures = int(conn.execute(
                        """SELECT COALESCE(SUM(failures),0) FROM login_attempts
                           WHERE ip_address=? AND datetime(last_attempt_at)>=datetime('now','-15 minutes')""",
                        (ip_address,),
                    ).fetchone()[0])
                    if identity_failures >= 15:
                        _set_login_lock(conn, "identity", identity)
                    if ip_failures >= 30:
                        _set_login_lock(conn, "ip", ip_address)
                    _prune_login_attempts(conn)
                    failure = True
                else:
                    conn.execute("DELETE FROM login_attempts WHERE identity=?", (identity,))
                    conn.execute(
                        "DELETE FROM login_lockouts WHERE scope='identity' AND lock_key=?",
                        (identity,),
                    )
                    conn.execute(
                        "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?",
                        (row["id"],),
                    )
                    refreshed = conn.execute(
                        "SELECT * FROM users WHERE id=?", (row["id"],)
                    ).fetchone()

        if locked_during_auth:
            raise LoginLocked("Too many attempts. Try again in a few minutes.")
        if failure:
            raise AuthenticationError("Incorrect username, email, or password.")
        return user_from_row(refreshed)

'''
write("app/auth.py", text[:start_index] + replacement + text[end_index:])

# Regression: a distributed identity lock remains enforceable after its source
# attempt rows are pushed out by the retention cap.
replace_once(
    "tests/test_auth.py",
    '''    def test_row_cap_never_deletes_active_lockout(self):
''',
    '''    def test_distributed_identity_lock_survives_attempt_row_cap(self):
        self.auth.create_user(
            "durablelock", "durable@example.com", "Durable Lock",
            "a long durable lock password",
        )
        for index in range(15):
            with self.assertRaises(AuthenticationError):
                self.auth.authenticate_local(
                    "durablelock", "wrong password", f"198.18.0.{index + 1}"
                )
        with self.database.connect() as conn:
            self.assertIsNotNone(conn.execute(
                """SELECT 1 FROM login_lockouts
                   WHERE scope='identity' AND lock_key='durablelock'
                     AND datetime(locked_until)>CURRENT_TIMESTAMP"""
            ).fetchone())
        with patch("app.auth.LOGIN_ATTEMPT_ROW_CAP", 2):
            for index in range(6):
                with self.assertRaises(AuthenticationError):
                    self.auth.authenticate_local(
                        f"noise-{index}", "wrong", f"203.0.113.{index + 1}"
                    )
        with self.database.connect() as conn:
            source_rows = conn.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE identity='durablelock'"
            ).fetchone()[0]
            self.assertLessEqual(source_rows, 2)
        from app.auth import LoginLocked
        with self.assertRaises(LoginLocked):
            self.auth.authenticate_local(
                "durablelock", "a long durable lock password", "192.0.2.200"
            )

    def test_row_cap_never_deletes_active_lockout(self):
''',
)

replace_once(
    "tests/test_migrations.py",
    '''                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=10").fetchone())
''',
    '''                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=11").fetchone())
                lockout_columns = {
                    row["name"] for row in upgraded.execute("PRAGMA table_info(login_lockouts)")
                }
                self.assertEqual(
                    {"scope", "lock_key", "locked_until", "created_at"},
                    lockout_columns,
                )
''',
)

# Linux permissions need directory search/execute rights, and the helper must
# never translate a root shell into an invalid UID/GID 0 image user.
replace_once(
    "deploy/linux.compose.yaml.example",
    '''      # They must be readable by INFOMANCER_UID/INFOMANCER_GID. Grant write
      # access as well for rename, organize, and managed-trash operations.
''',
    '''      # They and every parent directory must be searchable (execute permission)
      # by INFOMANCER_UID/INFOMANCER_GID; media files must also be readable.
      # Grant directory write access for rename, organize, and managed-trash
      # operations. Ownership, group permissions, or ACLs can provide this access.
''',
)
replace_once(
    "docs/INSTALLATION.md",
    '''   ```bash
   sed -i "s/^INFOMANCER_UID=.*/INFOMANCER_UID=$(id -u)/" .env
   sed -i "s/^INFOMANCER_GID=.*/INFOMANCER_GID=$(id -g)/" .env
   mkdir -p data
   ```

   The same UID/GID must be able to read every media mapping. Grant it write
   permission only on locations where you want InfoMancer to rename, organize,
   restore, or move files into managed Trash. Group permissions or ACLs are
   preferable to changing ownership of a shared media library.
''',
    '''   ```bash
   if [ "$(id -u)" -eq 0 ]; then
     echo "Run this step as the non-root account that will own InfoMancer data." >&2
     exit 1
   fi
   sed -i "s/^INFOMANCER_UID=.*/INFOMANCER_UID=$(id -u)/" .env
   sed -i "s/^INFOMANCER_GID=.*/INFOMANCER_GID=$(id -g)/" .env
   mkdir -p data
   ```

   Do not use UID or GID `0`. The same non-root UID/GID needs directory
   search/execute permission on every parent of each media mapping and read
   permission on the media itself. Grant directory write permission only where
   you want InfoMancer to rename, organize, restore, or move files into managed
   Trash. Ownership, group permissions, or ACLs can provide this access; ACLs or
   group permissions are preferable to changing ownership of a shared library.
''',
)
replace_once(
    "Dockerfile",
    '''ARG INFOMANCER_UID=1000
ARG INFOMANCER_GID=1000
RUN apt-get update \\
''',
    '''ARG INFOMANCER_UID=1000
ARG INFOMANCER_GID=1000
RUN test "${INFOMANCER_UID}" != "0" && test "${INFOMANCER_GID}" != "0" \\
    || (echo "INFOMANCER_UID and INFOMANCER_GID must be non-root values" >&2; exit 1)
RUN apt-get update \\
''',
)
replace_once(
    "tests/test_security_hardening.py",
    '''        self.assertIn("ARG INFOMANCER_GID=1000", dockerfile)
''',
    '''        self.assertIn("ARG INFOMANCER_GID=1000", dockerfile)
        self.assertIn('test "${INFOMANCER_UID}" != "0"', dockerfile)
        self.assertIn('test "${INFOMANCER_GID}" != "0"', dockerfile)
''',
)

print("Second CodeRabbit follow-up applied.")

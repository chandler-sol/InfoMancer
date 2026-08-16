import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.auth import (
    AuthService, AuthenticationError, LoginLocked, password_hasher, safe_next,
)
from app.config import Settings
from app.db import Database


class AuthServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(
            database=Path(self.temporary.name) / "auth.db",
            tvdb_api_key="", tvdb_pin="", search_url_template="",
            media_browse_roots=(Path(self.temporary.name),), auth_mode="local",
            session_days=14, cookie_secure="false",
            cloudflare_team_domain="", cloudflare_audience="",
        )
        self.database = Database(self.settings.database)
        self.database.initialize()
        self.auth = AuthService(self.database, self.settings)
        self.request = SimpleNamespace(
            headers={"user-agent": "InfoMancer test"},
            client=SimpleNamespace(host="127.0.0.1"),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_safe_next_rejects_external_and_backslash_redirects(self):
        self.assertEqual(safe_next("/movies?sort=title"), "/movies?sort=title")
        for unsafe in ("https://example.test", "//example.test", "/\\example.test", "/ok\nLocation: bad"):
            with self.subTest(destination=unsafe):
                self.assertEqual(safe_next(unsafe), "/")

    def test_password_is_argon2id_and_login_accepts_email(self):
        user = self.auth.create_user(
            "librarian", "person@example.com", "Person",
            "a long test password", role="librarian",
        )
        with self.database.connect() as conn:
            stored = conn.execute(
                "SELECT password_hash FROM users WHERE id=?", (user.id,)
            ).fetchone()[0]
        self.assertTrue(stored.startswith("$argon2id$"))
        self.assertNotIn("a long test password", stored)
        self.assertTrue(password_hasher.verify(stored, "a long test password"))
        signed_in = self.auth.authenticate_local(
            "PERSON@EXAMPLE.COM", "a long test password", "127.0.0.1"
        )
        self.assertEqual(signed_in.id, user.id)

    def test_sandbox_accepts_a_one_character_password_only_in_sandbox(self):
        with self.assertRaisesRegex(AuthenticationError, "at least 12"):
            self.auth.create_user(
                "shortpass", "", "Short Password", "x", role="member"
            )

        sandbox_auth = AuthService(
            self.database, replace(self.settings, sandbox=True)
        )
        user = sandbox_auth.create_user(
            "sandboxpass", "", "Sandbox Password", "x", role="member"
        )
        self.assertEqual(user.username, "sandboxpass")

    def test_initial_librarian_creation_is_atomic_and_single_use(self):
        user = self.auth.create_initial_librarian(
            "firstadmin", "first@example.com", "First Admin",
            "a strong initial password", provider="cloudflare",
            subject="cf-subject", require_password=True,
        )
        self.assertTrue(user.is_librarian)
        self.assertEqual(
            self.auth.user_for_identity("cloudflare", "cf-subject").id, user.id
        )
        with self.assertRaisesRegex(AuthenticationError, "already been completed"):
            self.auth.create_initial_librarian(
                "secondadmin", "second@example.com", "Second Admin",
                "another strong password",
            )

    def test_initial_external_identity_failure_does_not_leave_user(self):
        with self.assertRaisesRegex(AuthenticationError, "identity is incomplete"):
            self.auth.create_initial_librarian(
                "firstadmin", "first@example.com", "First Admin", "",
                require_password=False, provider="cloudflare", subject="",
            )
        self.assertEqual(self.auth.user_count(), 0)

    def test_distributed_failures_lock_an_identity_and_old_attempts_are_pruned(self):
        self.auth.create_user(
            "ratelimit", "rate@example.com", "Rate Limit",
            "a long rate limit password",
        )
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO login_attempts(identity,ip_address,failures,last_attempt_at)
                   VALUES ('stale','192.0.2.1',1,'2000-01-01 00:00:00')"""
            )
        for index in range(15):
            with self.assertRaises(AuthenticationError):
                self.auth.authenticate_local(
                    "ratelimit", "wrong password", f"198.51.100.{index + 1}"
                )
        from app.auth import LoginLocked
        with self.assertRaises(LoginLocked):
            self.auth.authenticate_local(
                "ratelimit", "a long rate limit password", "203.0.113.1"
            )
        with self.database.connect() as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM login_attempts WHERE identity='stale'"
            ).fetchone())

    def test_distributed_identity_lock_survives_attempt_row_cap(self):
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
        user = self.auth.create_user(
            "member", "member@example.com", "Member",
            "another long password", role="member",
        )
        raw, session = self.auth.create_session(user, self.request)
        with self.database.connect() as conn:
            stored = conn.execute(
                "SELECT token_hash FROM user_sessions WHERE id=?", (session.id,)
            ).fetchone()[0]
        self.assertNotEqual(raw, stored)
        self.assertEqual(self.auth.session_from_token(raw).user.id, user.id)
        self.auth.revoke_session(session.id, user.id)
        self.assertIsNone(self.auth.session_from_token(raw))

    def test_home_preferences_survive_session_reload(self):
        user = self.auth.create_user(
            "homeprefs", "home@example.com", "Home Preferences",
            "another long password", role="member",
        )
        raw, _ = self.auth.create_session(user, self.request)

        toggled = self.auth.toggle_home_layout(user.id)
        self.assertEqual(toggled.home_layout, "classic")
        self.auth.update_profile(
            user.id, user.display_name, user.email, user.profile_icon, False, True,
        )

        session_user = self.auth.session_from_token(raw).user
        self.assertEqual(session_user.home_layout, "classic")
        self.assertFalse(session_user.show_home_hero)
        self.assertTrue(session_user.high_contrast)

    def test_final_active_librarian_cannot_be_demoted(self):
        librarian = self.auth.create_user(
            "librarian", "admin@example.com", "Admin",
            "a secure admin password", role="librarian",
        )
        with self.assertRaises(AuthenticationError):
            self.auth.update_user_admin(
                librarian.id, "Admin", "admin@example.com", "member", True,
                librarian.id,
            )

    def test_external_identity_requires_preassigned_email(self):
        member = self.auth.create_user(
            "member", "member@example.com", "Member", "",
            role="member", require_password=False,
        )
        self.assertIsNone(self.auth.claim_preassigned_identity(
            "cloudflare", "subject-1", "other@example.com"
        ))
        claimed = self.auth.claim_preassigned_identity(
            "cloudflare", "subject-1", "MEMBER@example.com"
        )
        self.assertEqual(claimed.id, member.id)
        self.assertEqual(
            self.auth.user_for_identity("cloudflare", "subject-1").id, member.id
        )

    def test_invitation_is_single_use_and_only_its_hash_is_stored(self):
        member = self.auth.create_user(
            "invited", "invited@example.com", "Invited Member", "",
            role="member", require_password=False,
        )
        raw_token, _ = self.auth.create_invitation(member.id, None)
        with self.database.connect() as conn:
            stored = conn.execute(
                "SELECT token_hash FROM account_invitations WHERE user_id=?",
                (member.id,),
            ).fetchone()[0]
        self.assertNotEqual(raw_token, stored)
        self.assertEqual(self.auth.invitation_for_token(raw_token)["user_id"], member.id)
        activated = self.auth.accept_invitation(raw_token, "a newly chosen password")
        self.assertEqual(activated.id, member.id)
        self.assertIsNotNone(self.auth.authenticate_local(
            "invited", "a newly chosen password", "127.0.0.1"
        ))
        with self.assertRaisesRegex(AuthenticationError, "already been used"):
            self.auth.invitation_for_token(raw_token)

    def test_new_invitation_revokes_the_previous_link(self):
        member = self.auth.create_user(
            "pending", "pending@example.com", "Pending", "",
            require_password=False,
        )
        first, _ = self.auth.create_invitation(member.id, None)
        second, _ = self.auth.create_invitation(member.id, None)
        with self.assertRaisesRegex(AuthenticationError, "cancelled"):
            self.auth.invitation_for_token(first)
        self.assertEqual(self.auth.invitation_for_token(second)["user_id"], member.id)

    def test_pending_disabled_and_unknown_accounts_share_public_login_error(self):
        pending = self.auth.create_user(
            "waiting", "waiting@example.com", "Waiting", "",
            require_password=False,
        )
        for identity in ("waiting", "missing-user"):
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(
                    AuthenticationError, "Incorrect username, email, or password"
                ):
                    self.auth.authenticate_local(
                        identity, "not relevant", f"127.0.0.{1 if identity == 'waiting' else 2}"
                    )
        self.auth.update_user_admin(
            pending.id, pending.display_name, pending.email, "member", False, 999,
        )
        with self.assertRaisesRegex(
            AuthenticationError, "Incorrect username, email, or password"
        ):
            self.auth.authenticate_local("waiting", "not relevant", "127.0.0.3")

    def test_new_pair_lockout_is_reported_once_with_account_metadata(self):
        user = self.auth.create_user(
            "lockme", "lock@example.com", "Lock Me",
            "a sufficiently long password",
        )
        for _ in range(4):
            with self.assertRaises(AuthenticationError):
                self.auth.authenticate_local("lockme", "wrong password", "192.0.2.55")
        with self.assertRaises(LoginLocked) as created:
            self.auth.authenticate_local("lockme", "wrong password", "192.0.2.55")
        self.assertTrue(created.exception.new_lockout)
        self.assertIn("account_ip", created.exception.scope)
        self.assertEqual(created.exception.user_id, user.id)
        with self.assertRaises(LoginLocked) as repeated:
            self.auth.authenticate_local(
                "lockme", "a sufficiently long password", "192.0.2.55"
            )
        self.assertFalse(repeated.exception.new_lockout)
        self.assertEqual(repeated.exception.user_id, user.id)

    def test_duplicate_username_error_names_the_conflicting_username(self):
        self.auth.create_user(
            "SameName", "first@example.com", "First", "first long password"
        )
        with self.assertRaisesRegex(AuthenticationError, 'username "samename" is already in use'):
            self.auth.create_user(
                "samename", "second@example.com", "Second", "second long password"
            )

    def test_librarian_recovery_reactivates_and_requires_password_change(self):
        librarian = self.auth.create_user(
            "recovery", "recovery@example.com", "Recovery",
            "original long password", role="librarian",
        )
        recovered = self.auth.recover_librarian(
            "RECOVERY", "temporary recovery password"
        )
        self.assertTrue(recovered.active)
        self.assertTrue(recovered.force_password_change)
        self.assertEqual(
            self.auth.authenticate_local(
                "recovery", "temporary recovery password", "127.0.0.1"
            ).id,
            librarian.id,
        )


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from app.auth import AuthService, AuthenticationError, password_hasher
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

    def test_pending_and_disabled_accounts_get_actionable_login_messages(self):
        pending = self.auth.create_user(
            "waiting", "waiting@example.com", "Waiting", "",
            require_password=False,
        )
        with self.assertRaisesRegex(AuthenticationError, "waiting for setup"):
            self.auth.authenticate_local("waiting", "not relevant", "127.0.0.1")
        self.auth.update_user_admin(
            pending.id, pending.display_name, pending.email, "member", False, 999,
        )
        with self.assertRaisesRegex(AuthenticationError, "account is disabled"):
            self.auth.authenticate_local("waiting", "not relevant", "127.0.0.1")

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

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProfileAccountConsolidationContracts(unittest.TestCase):
    def test_profile_owns_password_and_session_entry_points(self):
        source = (ROOT / "app/templates/account_profile.html").read_text(encoding="utf-8")

        self.assertNotIn('{% include "_account_nav.html" %}', source)
        self.assertIn('data-profile-account-dialog="password"', source)
        self.assertIn('href="/account/security"', source)
        self.assertIn('data-profile-account-dialog="sessions"', source)
        self.assertIn('href="/account/sessions"', source)
        self.assertIn('id="profile-account-dialog"', source)
        self.assertIn("profile-account-dialogs.js", source)

    def test_profile_dialogs_reuse_existing_account_endpoints(self):
        source = (ROOT / "app/static/profile-account-dialogs.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("selector: '.settings-form'", source)
        self.assertIn("selector: '.session-panel'", source)
        self.assertIn("body.addEventListener('submit'", source)
        self.assertIn("new FormData(form)", source)
        self.assertNotIn("/api/account/password", source)
        self.assertNotIn("/api/account/sessions", source)

    def test_user_management_belongs_to_application_settings(self):
        account_nav = (ROOT / "app/templates/_account_nav.html").read_text(encoding="utf-8")
        settings_nav = (ROOT / "app/templates/_settings_nav.html").read_text(encoding="utf-8")
        users = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")

        self.assertNotIn("/admin/users", account_nav)
        self.assertIn('href="/admin/users"', settings_nav)
        self.assertIn("section == 'users'", settings_nav)
        self.assertIn("{% set section = 'users' %}", users)
        self.assertIn('{% include "_settings_nav.html" %}', users)
        self.assertNotIn('{% include "_account_nav.html" %}', users)

    def test_standalone_security_and_session_pages_remain_progressive_fallbacks(self):
        security = (ROOT / "app/templates/account_security.html").read_text(encoding="utf-8")
        sessions = (ROOT / "app/templates/account_sessions.html").read_text(encoding="utf-8")

        self.assertIn('action="/account/security"', security)
        self.assertIn('class="panel settings-form"', security)
        self.assertIn('class="panel session-panel"', sessions)
        self.assertIn('/account/sessions/revoke-others', sessions)

    def test_profile_compact_polish_keeps_account_actions_in_header(self):
        source = (ROOT / "app/static/profile-account-dialogs.css").read_text(
            encoding="utf-8"
        )

        self.assertIn(".profile-settings-heading", source)
        self.assertIn("grid-template-columns:minmax(0,1fr) auto", source)
        self.assertIn(".profile-icon-choice", source)
        self.assertIn("min-height:84px", source)
        self.assertIn(".profile-preview-inner", source)
        self.assertIn("min-height:286px", source)


if __name__ == "__main__":
    unittest.main()

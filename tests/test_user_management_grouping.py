from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UserManagementGroupingContracts(unittest.TestCase):
    def test_user_management_groups_accounts_by_role(self):
        template = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")
        self.assertIn("{% set members =", template)
        self.assertIn("{% set librarians =", template)
        self.assertIn('id="members-heading">Members', template)
        self.assertIn('id="librarians-heading">Librarians', template)
        self.assertNotIn("Members &amp; Librarians", template)

    def test_users_use_season_style_disclosure_contract(self):
        template = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/user-management.css").read_text(encoding="utf-8")
        self.assertIn('class="admin-user-chevron"', template)
        self.assertNotIn("admin-user-expand", template)
        self.assertIn(".admin-user>summary", css)
        self.assertIn("border-color:var(--lime)", css)
        self.assertIn(".admin-user[open] .admin-user-chevron", css)

    def test_expanded_user_actions_have_one_footer(self):
        template = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/user-management.css").read_text(encoding="utf-8")
        self.assertIn('class="admin-user-action-footer"', template)
        self.assertIn('class="admin-user-save-row"', template)
        self.assertIn('class="admin-user-delete-toggle"', template)
        self.assertIn(".admin-user-action-footer", css)
        self.assertIn(".admin-user-save-row", css)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UserManagementPolishTests(unittest.TestCase):
    def test_user_list_has_clear_expand_affordance_without_raw_count(self):
        template = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")
        self.assertIn("Members &amp; Librarians", template)
        self.assertNotIn('class="user-count"', template)
        self.assertIn('class="admin-user-expand"', template)
        self.assertIn("<span>Manage</span>", template)
        self.assertIn('class="admin-user-identity"', template)

    def test_user_actions_use_compact_scoped_controls(self):
        template = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/auth.css").read_text(encoding="utf-8")
        self.assertIn('class="button primary admin-user-save">Save changes</button>', template)
        self.assertIn('class="admin-user-delete-toggle">Delete user</summary>', template)
        self.assertIn(".admin-user-edit .admin-user-save", css)
        self.assertIn(".admin-user-actions .button", css)
        self.assertIn(".admin-user-delete>.admin-user-delete-toggle", css)
        self.assertIn(".admin-user>summary", css)


if __name__ == "__main__":
    unittest.main()

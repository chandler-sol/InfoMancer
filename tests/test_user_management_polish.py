from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UserManagementPolishTests(unittest.TestCase):
    def test_user_list_has_grouped_disclosure_without_raw_count(self):
        template = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")
        self.assertIn("People with access", template)
        self.assertIn('id="members-heading">Members', template)
        self.assertIn('id="librarians-heading">Librarians', template)
        self.assertNotIn('class="user-count"', template)
        self.assertIn('class="admin-user-identity"', template)
        self.assertIn('class="admin-user-chevron"', template)
        self.assertNotIn('class="admin-user-expand"', template)
        self.assertNotIn("<span>Manage</span>", template)

    def test_user_actions_use_compact_scoped_controls(self):
        template = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/user-management.css").read_text(encoding="utf-8")
        self.assertIn('class="button primary admin-user-save">Save changes</button>', template)
        self.assertIn('class="admin-user-action-footer"', template)
        self.assertIn('class="admin-user-action-buttons"', template)
        self.assertIn('class="admin-user-delete-toggle"', template)
        self.assertIn('class="admin-user-delete-confirm"', template)
        self.assertIn(".admin-user-save", css)
        self.assertIn(".admin-user-action-buttons .button", css)
        self.assertIn(".admin-user-delete-toggle", css)
        self.assertIn(".admin-user > summary", css)
        self.assertIn("grid-column: 2", css)
        self.assertIn("grid-column: 1 / -1", css)


if __name__ == "__main__":
    unittest.main()

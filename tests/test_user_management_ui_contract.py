from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UserManagementUiContractTests(unittest.TestCase):
    def test_delete_trigger_is_not_a_details_disclosure(self):
        template = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")

        self.assertIn('class="admin-user-delete-toggle"', template)
        self.assertIn('aria-expanded="false"', template)
        self.assertIn('aria-controls="delete-confirm-{{ user.id }}"', template)
        self.assertIn('class="admin-user-delete-confirm"', template)
        self.assertIn('class="button admin-user-delete-cancel"', template)
        self.assertNotIn('<details class="admin-user-delete">', template)

    def test_delete_confirmation_has_fixed_grid_slot_and_cancel_controller(self):
        css = (ROOT / "app/static/user-management.css").read_text(encoding="utf-8")
        script = (ROOT / "app/static/user-management.js").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: minmax(0, 1fr) auto", css)
        self.assertIn(".admin-user-delete-toggle {", css)
        self.assertIn("grid-column: 2", css)
        self.assertIn("grid-row: 1", css)
        self.assertIn(".admin-user-delete-confirm {", css)
        self.assertIn("grid-column: 1 / -1", css)
        self.assertIn("grid-row: 2", css)
        self.assertIn(".admin-user-delete-confirm[hidden]", css)
        self.assertIn("toggle.setAttribute('aria-expanded', 'true')", script)
        self.assertIn("panel.hidden = false", script)
        self.assertIn("panel.hidden = true", script)
        self.assertIn("toggle.focus()", script)


if __name__ == "__main__":
    unittest.main()

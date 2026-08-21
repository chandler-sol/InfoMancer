import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.auth import AuthService
from app.config import Settings
from app.db import Database
from app.routes.context import RouteContext
from app.routes.user_management import build_router


ROOT = Path(__file__).resolve().parents[1]


class UserManagement08Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(
            database=Path(self.temporary.name) / "users.db",
            tvdb_api_key="", tvdb_pin="", search_url_template="",
            media_browse_roots=(Path(self.temporary.name),), auth_mode="local",
            session_days=14, cookie_secure="false",
            cloudflare_team_domain="", cloudflare_audience="",
        )
        self.database = Database(self.settings.database)
        self.database.initialize()
        self.auth = AuthService(self.database, self.settings)
        self.actor = self.auth.create_user(
            "admin", "admin@example.com", "Admin", "a strong admin password",
            role="librarian",
        )
        self.events = []

        def redirect(path, message):
            return path, message

        def record_event(category, message, **kwargs):
            self.events.append((category, message, kwargs))

        context = RouteContext({
            "auth_service": self.auth,
            "db": self.database,
            "redirect": redirect,
            "record_event": record_event,
            "settings": self.settings,
        })
        router, _ = build_router(context)
        self.delete_endpoint = next(
            route.endpoint for route in router.routes
            if route.path == "/admin/users/{user_id}/delete"
        )
        self.request = SimpleNamespace(state=SimpleNamespace(user=self.actor))

    def tearDown(self):
        self.temporary.cleanup()

    def test_delete_other_user_cascades_account_data_and_audits_actor(self):
        target = self.auth.create_user(
            "member", "member@example.com", "Member", "a strong member password",
            role="member",
        )
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO user_sessions
                   (user_id,token_hash,csrf_token,expires_at)
                   VALUES (?,?,?,datetime('now','+1 day'))""",
                (target.id, "test-token-hash", "csrf"),
            )
            conn.execute(
                """INSERT INTO user_saved_views(user_id,name,path,query_string)
                   VALUES (?,?,?,?)""",
                (target.id, "My view", "/library", "genre=Drama"),
            )

        response = self.delete_endpoint(self.request, target.id)

        self.assertEqual(response[0], "/admin/users")
        self.assertIsNone(self.auth.get_user(target.id))
        self.assertIsNotNone(self.auth.get_user(self.actor.id))
        with self.database.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id=?", (target.id,)).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM user_saved_views WHERE user_id=?", (target.id,)).fetchone()[0],
                0,
            )
        self.assertEqual(self.events[-1][0], "authentication")
        self.assertEqual(self.events[-1][2]["user_id"], self.actor.id)
        self.assertEqual(self.events[-1][2]["context"]["deleted_user_id"], target.id)

    def test_current_account_cannot_be_deleted(self):
        response = self.delete_endpoint(self.request, self.actor.id)
        self.assertIn("cannot delete", response[1].lower())
        self.assertIsNotNone(self.auth.get_user(self.actor.id))

    def test_template_groups_other_users_with_disclosure_and_guarded_delete(self):
        source = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")
        self.assertIn("rejectattr('id', 'equalto', current_user.id)", source)
        self.assertIn('class="admin-user"', source)
        self.assertIn('class="admin-user-identity"', source)
        self.assertIn('class="admin-user-chevron"', source)
        self.assertIn("{% set members =", source)
        self.assertIn("{% set librarians =", source)
        self.assertIn('id="members-heading">Members', source)
        self.assertIn('id="librarians-heading">Librarians', source)
        self.assertIn('/admin/users/{{ user.id }}/delete', source)
        self.assertIn('class="admin-user-delete-toggle"', source)
        self.assertIn('class="button admin-user-delete-cancel"', source)
        self.assertIn("personal ratings, favorites, tags, saved views, and search history", source)
        self.assertIn("People with access", source)
        self.assertNotIn("Other people with access", source)

    def test_status_and_disclosure_styles_distinguish_account_states(self):
        source = (ROOT / "app/static/user-management.css").read_text(encoding="utf-8")
        self.assertIn(".account-state.active", source)
        self.assertIn("background: #162014 !important", source)
        self.assertIn("color: var(--lime) !important", source)
        self.assertIn(".account-state.pending", source)
        self.assertIn(".account-state.disabled", source)
        self.assertIn(".admin-user[open] .admin-user-chevron", source)
        self.assertIn("transform: rotate(180deg)", source)
        self.assertIn("grid-column: 2", source)
        self.assertIn(".admin-user-delete-confirm[hidden]", source)


if __name__ == "__main__":
    unittest.main()

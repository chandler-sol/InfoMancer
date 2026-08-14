import os
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
from app.auth import AuthService
from app.app_settings import AppSettings
from app.db import Database
from app.engagement import EngagementService


class AuthenticationFlowTests(unittest.TestCase):
    def test_redirect_helper_rejects_external_destination(self):
        response = main.redirect("//example.test/account", "Not allowed")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")

    def test_first_run_login_csrf_and_profile_flow(self):
        original = (
            main.db, main.settings, main.auth_service, main.app_settings,
            main.engagement,
        )
        with tempfile.TemporaryDirectory() as temporary:
            settings = replace(
                main.settings, database=Path(temporary) / "web.db",
                auth_mode="local", cookie_secure="false", sandbox=True,
                media_browse_roots=(Path(temporary),),
            )
            database = Database(settings.database)
            database.initialize()
            main.db, main.settings = database, settings
            main.auth_service = AuthService(database, settings)
            main.app_settings = AppSettings(database, settings.search_url_template)
            main.engagement = EngagementService(database)
            main.engagement.seed_official()
            try:
                with TestClient(main.app, follow_redirects=False) as client:
                    response = client.get("/")
                    self.assertEqual(response.status_code, 303)
                    self.assertEqual(response.headers["location"], "/setup")

                    api_response = client.get("/api/tasks")
                    self.assertEqual(api_response.status_code, 401)
                    self.assertEqual(api_response.json()["detail"], "Complete first-run setup")

                    setup = client.get("/setup")
                    token = re.search(
                        r'name="preauth_token" value="([^"]+)', setup.text
                    ).group(1)
                    repeated_setup = client.get("/setup")
                    repeated_token = re.search(
                        r'name="preauth_token" value="([^"]+)', repeated_setup.text
                    ).group(1)
                    self.assertEqual(repeated_token, token)

                    expired = client.post("/setup", data={
                        "preauth_token": "not-the-current-token",
                        "username": "librarian", "display_name": "Admin",
                        "password": "a long admin password",
                        "password_confirm": "a long admin password",
                    })
                    self.assertEqual(expired.status_code, 303)
                    explanation = client.get(expired.headers["location"])
                    self.assertIn("Setup form expired. Please try again.", explanation.text)
                    created = client.post("/setup", data={
                        "preauth_token": token, "username": "librarian",
                        "email": "admin@example.com", "display_name": "Admin",
                        "profile_icon": "library", "password": "a long admin password",
                        "password_confirm": "a long admin password",
                    })
                    self.assertEqual(created.status_code, 303)
                    self.assertIn("account_notice=1", created.headers["location"])
                    self.assertIn("Librarian+account+created+successfully", created.headers["location"])
                    self.assertIn("infomancer_session", client.cookies)
                    metrics = client.get("/api/dashboard-metrics")
                    self.assertEqual(metrics.status_code, 200)
                    self.assertEqual(metrics.json()["movies"], {
                        "value": 0, "display": "0",
                    })
                    self.assertEqual(metrics.json()["bytes"], {
                        "value": 0, "display": "0 bytes",
                    })
                    self.assertIn("onboarding-tour", client.get("/").text)
                    empty_library_tour = client.get("/movies?tour=1&tour_step=2")
                    self.assertIn("tour-library-demo", empty_library_tour.text)
                    self.assertIn("EXAMPLE LIBRARY", empty_library_tour.text)
                    self.assertEqual(client.get("/account/profile").status_code, 200)
                    security_page = client.get("/account/security")
                    self.assertEqual(security_page.status_code, 200)
                    self.assertEqual(
                        security_page.text.count('class="password-visibility-toggle"'), 3
                    )
                    self.assertIn("password-visibility.js", security_page.text)

                    rejected = client.post("/account/profile", data={
                        "csrf_token": "wrong", "display_name": "Changed",
                        "email": "admin@example.com", "profile_icon": "star",
                    })
                    self.assertEqual(rejected.status_code, 403)

                    session = main.auth_service.session_from_token(
                        client.cookies["infomancer_session"]
                    )
                    tour_saved = client.post("/engagement/tour", data={
                        "csrf_token": session.csrf_token, "state": "completed",
                    })
                    self.assertEqual(tour_saved.status_code, 200)
                    post_tour_home = client.get("/")
                    self.assertNotIn("onboarding-tour", post_tour_home.text)
                    self.assertIn("How would you like to begin?", post_tour_home.text)
                    setup_choice = client.post("/getting-started/choice", data={
                        "csrf_token": session.csrf_token, "mode": "guided",
                    })
                    self.assertEqual(setup_choice.status_code, 303)
                    self.assertEqual(setup_choice.headers["location"], "/getting-started/general")
                    setup_general = client.get("/getting-started/general")
                    self.assertIn("Set your library name and time zone", setup_general.text)
                    setup_general_saved = client.post("/getting-started/general", data={
                        "csrf_token": session.csrf_token,
                        "installation_name": "InfoMancer", "timezone_name": "UTC",
                    })
                    self.assertEqual(setup_general_saved.status_code, 303)
                    self.assertIn("/getting-started/metadata", setup_general_saved.headers["location"])
                    self.assertIn(
            "TheTVDB is not connected yet",
                        client.get("/getting-started/metadata").text,
                    )
                    setup_metadata = client.post("/getting-started/metadata", data={
                        "csrf_token": session.csrf_token, "testing_skip": "1",
                    })
                    self.assertEqual(setup_metadata.status_code, 303)
                    self.assertIn("No media folders added yet", client.get("/getting-started/sources").text)
                    media_folder = Path(temporary) / "Movies"
                    media_folder.mkdir()
                    source_added = client.post("/roots", data={
                        "csrf_token": session.csrf_token,
                        "path": str(media_folder), "kind": "movie",
                        "label": "Test Movies", "return_to": "/getting-started/sources",
                    })
                    self.assertEqual(source_added.status_code, 303)
                    self.assertEqual(source_added.headers["location"].split("?")[0], "/getting-started/sources")
                    setup_sources = client.post("/getting-started/sources", data={
                        "csrf_token": session.csrf_token,
                    })
                    self.assertEqual(setup_sources.status_code, 303)
                    self.assertIn("ready for its first scan", client.get("/getting-started/finish").text)
                    setup_complete = client.post("/getting-started/complete", data={
                        "csrf_token": session.csrf_token,
                    })
                    self.assertEqual(setup_complete.status_code, 303)
                    completed_home = client.get("/")
                    self.assertIn("Your first scan is the next step", completed_home.text)
                    self.assertNotIn("How would you like to begin?", completed_home.text)
                    saved = client.post("/account/profile", data={
                        "csrf_token": session.csrf_token, "display_name": "Changed",
                        "email": "admin@example.com", "profile_icon": "star",
                    })
                    self.assertEqual(saved.status_code, 303)
                    self.assertEqual(main.auth_service.get_user(1).display_name, "Changed")

                    settings_page = client.get("/settings/general")
                    self.assertEqual(settings_page.status_code, 200)
                    self.assertIn("App Settings", settings_page.text)
                    help_page = client.get("/help")
                    self.assertEqual(help_page.status_code, 200)
                    self.assertIn("How can we help?", help_page.text)
                    self.assertIn("What does a scan change?", help_page.text)
                    self.assertIn('href="/help"', help_page.text)
                    saved_settings = client.post("/settings/general", data={
                        "csrf_token": session.csrf_token,
                        "installation_name": "Family Archive",
                        "timezone_name": "America/New_York",
                        "default_library_view": "covers",
                        "default_cover_size": "220",
                    })
                    self.assertEqual(saved_settings.status_code, 303)
                    self.assertEqual(
                        main.app_settings.get("installation_name"), "Family Archive"
                    )
                    system_settings = client.get("/settings/system")
                    self.assertEqual(system_settings.status_code, 200)
                    self.assertIn("Recent settings changes", system_settings.text)
                    self.assertIn("Changed", system_settings.text)
                    self.assertIn(
                        'href="/help#exports">Compare export formats',
                        system_settings.text,
                    )
                    self.assertNotIn(
                        'class="button primary" href="/exports/library?format=csv"',
                        system_settings.text,
                    )
                    self.assertIn("Which export format should I choose?", help_page.text)
                    self.assertIn(
                        "CSV</strong> is usually best for spreadsheets", help_page.text
                    )
                    self.assertIn(
                        "JSON</strong> is usually best for backups, scripts",
                        help_page.text,
                    )
                    self.assertIn(
                        "XML</strong> is useful when another application", help_page.text
                    )

                    with main.db.connect() as conn:
                        failed_root = conn.execute(
                            """INSERT INTO roots(path,kind,label)
                               VALUES ('/media/test','movie','Test Movies')"""
                        ).lastrowid
                        failed_title = conn.execute(
                            """INSERT INTO titles
                               (root_id,kind,title,folder_path)
                               VALUES (?,'movie','Unreadable Movie','/media/test/Unreadable Movie')""",
                            (failed_root,),
                        ).lastrowid
                        conn.execute(
                            """INSERT INTO files
                               (title_id,path,filename,extension,seen_scan,
                                media_info_at,media_info_error)
                               VALUES (?,?,?,?,?,CURRENT_TIMESTAMP,?)""",
                            (
                                failed_title,
                                "/media/test/Unreadable Movie/broken.mkv",
                                "broken.mkv", ".mkv", "test-scan",
                                "This MKV appears incomplete or damaged. "
                                "Try playing the file and replace or recopy it. "
                                "Technical detail: EBML header parsing failed",
                            ),
                        )
                    system_with_failure = client.get("/settings/system")
                    self.assertIn("1 file needs attention", system_with_failure.text)
                    self.assertIn(
                        'href="/media-info/failures">Review affected files',
                        system_with_failure.text,
                    )
                    failures = client.get("/media-info/failures")
                    self.assertEqual(failures.status_code, 200)
                    self.assertIn("Files needing attention", failures.text)
                    self.assertIn("Unreadable Movie", failures.text)
                    self.assertIn("broken.mkv", failures.text)
                    self.assertIn("This MKV appears incomplete or damaged", failures.text)
                    self.assertNotIn("EBML header parsing failed", failures.text)
                    self.assertIn(
                        f'href="/titles/{failed_title}">Open title', failures.text
                    )
                    self.assertIn("Related logs", failures.text)

                    published = client.post("/admin/announcements", data={
                        "csrf_token": session.csrf_token,
                        "title": "Library maintenance",
                        "body": "The library will be briefly unavailable tonight.",
                        "category": "important", "audience": "members",
                        "starts_at": "2020-01-01T00:00",
                        "ends_at": "", "recurrence": "once",
                    })
                    self.assertEqual(published.status_code, 303)
                    announcement_center = client.get("/announcements")
                    self.assertEqual(announcement_center.status_code, 200)
                    self.assertIn("Publish an announcement", announcement_center.text)
                    self.assertIn("Library maintenance", announcement_center.text)

                    invitation_page = client.post("/admin/users", data={
                        "csrf_token": session.csrf_token,
                        "username": "viewer", "email": "viewer@example.com",
                        "display_name": "Library Viewer", "role": "member",
                    })
                    self.assertEqual(invitation_page.status_code, 200)
                    self.assertIn("one-time setup link", invitation_page.text.lower())
                    invitation_url = re.search(
                        r'id="invitation-url" value="([^"]+)', invitation_page.text
                    ).group(1)
                    invitation_path = invitation_url.split("/activate/", 1)[1]
                    activation = client.get(f"/activate/{invitation_path}")
                    self.assertEqual(
                        activation.text.count('class="password-visibility-toggle"'), 2
                    )
                    activation_csrf = re.search(
                        r'name="preauth_token" value="([^"]+)', activation.text
                    ).group(1)
                    accepted = client.post(f"/activate/{invitation_path}", data={
                        "preauth_token": activation_csrf,
                        "password": "viewer chosen password",
                        "password_confirm": "viewer chosen password",
                    })
                    self.assertEqual(accepted.status_code, 303)
                    self.assertEqual(client.get("/movies").status_code, 200)
                    self.assertEqual(client.get("/sources").status_code, 403)
                    self.assertEqual(client.get("/settings/general").status_code, 403)
                    self.assertEqual(client.get("/getting-started").status_code, 403)
                    member_announcements = client.get("/announcements")
                    self.assertEqual(member_announcements.status_code, 200)
                    self.assertIn("Library maintenance", member_announcements.text)
                    self.assertNotIn("Publish an announcement", member_announcements.text)
                    member_help = client.get("/help")
                    self.assertEqual(member_help.status_code, 200)
                    self.assertIn("Members can browse", member_help.text)
                    member_session = main.auth_service.session_from_token(
                        client.cookies["infomancer_session"]
                    )
                    rejected_announcement = client.post(
                        "/admin/announcements", data={
                            "csrf_token": member_session.csrf_token,
                            "title": "Not allowed", "body": "No",
                            "starts_at": "2020-01-01T00:00",
                        },
                    )
                    self.assertEqual(rejected_announcement.status_code, 403)
                    reused = client.get(f"/activate/{invitation_path}")
                    self.assertEqual(reused.status_code, 400)
                    self.assertIn("already been used", reused.text)
            finally:
                (
                    main.db, main.settings, main.auth_service, main.app_settings,
                    main.engagement,
                ) = original


if __name__ == "__main__":
    unittest.main()

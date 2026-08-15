from __future__ import annotations

import re
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
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


# Database initialization now delegates legacy upgrades to numbered migrations.
db = read("app/db.py")
db = db.replace("import json\n", "")
db = db.replace("from typing import Iterator\n", "from typing import Iterator\n\nfrom .migrations import apply_migrations\n")
start = db.index("    def initialize(self) -> None:\n")
end = db.index("    @contextmanager\n", start)
db = db[:start] + '''    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            apply_migrations(conn)

''' + db[end:]
write("app/db.py", db)

# Main application composition: explicit access dependencies, centralized task
# registry/runtime lease, and explicit metadata enrichment writes.
main = read("app/main.py")
main = main.replace(
    "from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile\n",
    "from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile\n",
)
main = main.replace(
    "from .config import BASE_DIR, get_settings\n",
    "from .config import BASE_DIR, get_settings\nfrom .access import LibrarianAccessRequired, require_librarian\n",
)
main = main.replace(
    "from .request_security import (\n",
    "from .runtime import JobRegistry, RuntimeLease\nfrom .title_metadata import TitleMetadataService\nfrom .request_security import (\n",
)
main = main.replace(
    'app = FastAPI(title="InfoMancer", version=APP_VERSION)\n',
    '''app = FastAPI(title="InfoMancer", version=APP_VERSION)


def _librarian_route(method: str, path: str, **kwargs):
    dependencies = list(kwargs.pop("dependencies", ()))
    dependencies.append(Depends(require_librarian))
    return getattr(app, method)(path, dependencies=dependencies, **kwargs)


def librarian_get(path: str, **kwargs):
    return _librarian_route("get", path, **kwargs)


def librarian_post(path: str, **kwargs):
    return _librarian_route("post", path, **kwargs)

''',
)
old_jobs = '''scan_jobs: dict[int, dict] = {}
scan_lock = threading.Lock()
scan_all_job: dict = {"status": "idle"}
scan_all_lock = threading.Lock()
title_scan_jobs: dict[int, dict] = {}
title_scan_lock = threading.Lock()
imdb_genre_job: dict = {"status": "idle"}
imdb_genre_lock = threading.Lock()
movie_match_job: dict = {"status": "idle"}
movie_match_lock = threading.Lock()
tv_match_job: dict = {"status": "idle"}
tv_match_lock = threading.Lock()
media_info_job: dict = {"status": "idle"}
media_info_lock = threading.Lock()
duplicate_verify_job: dict = {"status": "idle"}
duplicate_verify_lock = threading.Lock()
media_hash_job: dict = {"status": "idle"}
media_hash_lock = threading.Lock()
media_hash_pause = threading.Event()
media_hash_cancel = threading.Event()
background_scheduler_stop = threading.Event()
hash_schedule_last_check = 0.0
trash_cleanup_job: dict = {"status": "idle"}
trash_cleanup_lock = threading.Lock()
trash_cleanup_last_check = 0.0
'''
new_jobs = '''job_registry = JobRegistry()
runtime_lease = RuntimeLease(db)
scan_jobs: dict[int, dict] = job_registry.mapping("scan")
scan_lock = job_registry.lock("scan")
scan_all_job: dict = job_registry.job("scan-all")
scan_all_lock = job_registry.lock("scan-all")
title_scan_jobs: dict[int, dict] = job_registry.mapping("title-scan")
title_scan_lock = job_registry.lock("title-scan")
imdb_genre_job: dict = job_registry.job("imdb-metadata")
imdb_genre_lock = job_registry.lock("imdb-metadata")
movie_match_job: dict = job_registry.job("movie-match")
movie_match_lock = job_registry.lock("movie-match")
tv_match_job: dict = job_registry.job("tv-match")
tv_match_lock = job_registry.lock("tv-match")
media_info_job: dict = job_registry.job("media-info")
media_info_lock = job_registry.lock("media-info")
duplicate_verify_job: dict = job_registry.job("duplicate-verify")
duplicate_verify_lock = job_registry.lock("duplicate-verify")
media_hash_job: dict = job_registry.job("media-hash")
media_hash_lock = job_registry.lock("media-hash")
media_hash_pause = job_registry.event("media-hash-pause")
media_hash_cancel = job_registry.event("media-hash-cancel")
background_scheduler_stop = job_registry.event("scheduler-stop")
hash_schedule_last_check = 0.0
trash_cleanup_job: dict = job_registry.job("trash-cleanup")
trash_cleanup_lock = job_registry.lock("trash-cleanup")
trash_cleanup_last_check = 0.0
'''
if old_jobs not in main:
    raise RuntimeError("job global block changed")
main = main.replace(old_jobs, new_jobs, 1)
main = main.replace(
    '''@app.on_event("startup")
def start_background_scheduler() -> None:
    background_scheduler_stop.clear()
    threading.Thread(
        target=run_background_scheduler, name="infomancer-scheduler", daemon=True,
    ).start()
''',
    '''@app.on_event("startup")
def start_background_scheduler() -> None:
    runtime_lease.start()
    background_scheduler_stop.clear()
    threading.Thread(
        target=run_background_scheduler, name="infomancer-scheduler", daemon=True,
    ).start()
''',
    1,
)
main = main.replace(
    '''@app.on_event("shutdown")
def stop_background_scheduler() -> None:
    background_scheduler_stop.set()
''',
    '''@app.on_event("shutdown")
def stop_background_scheduler() -> None:
    background_scheduler_stop.set()
    runtime_lease.release()
''',
    1,
)
# Remove path/regex authorization policy. Route decorators below become the policy.
policy_start = main.index('PUBLIC_PATHS = {"/health", "/login", "/setup", "/forgot-password"}\n')
policy_end = main.index("\ndef public_path(path: str) -> bool:\n", policy_start)
main = main[:policy_start] + 'PUBLIC_PATHS = {"/health", "/login", "/setup", "/forgot-password"}\n\n' + main[policy_end + 1:]
# Add a human-friendly exception handler after auth_error_response is defined.
needle = '''def set_session_cookie(response, request: Request, raw_token: str) -> None:
'''
handler = '''@app.exception_handler(LibrarianAccessRequired)
async def librarian_access_required(request: Request, _exc: LibrarianAccessRequired):
    return auth_error_response(
        request, 403, "Librarian access required",
        "Your Member account can browse the library, but this operation requires a Librarian.",
    )


def set_session_cookie(response, request: Request, raw_token: str) -> None:
'''
main = main.replace(needle, handler, 1)
# Remove old GET role check and unsafe-request role allowlist.
main = re.sub(
    r'''    if user and request\.method == "GET" and librarian_only_path\(path\) and not user\.is_librarian:\n        return await finish\(auth_error_response\(\n            request, 403, "Librarian access required",\n            "Your Member account can browse the library, but this operation requires a Librarian\.",\n        \)\)\n''',
    "", main, count=1,
)
main = re.sub(
    r'''            if path not in \{\n                "/logout", "/account/profile", "/account/security",\n                "/account/sessions/revoke-others",\n            \} and not re\.match\(r"\^/titles/\\d\+/\(\?:favorite\|organize\)\$", path\) \\\n              and not re\.match\(r"\^/files/\\d\+/favorite\$", path\) \\\n              and path not in \{\n                  "/titles/organize-bulk", "/tags/create",\n              \} and not re\.match\(r"\^/tags/\\d\+/\(\?:rename\|delete\)\$", path\) \\\n              and not path\.startswith\("/account/sessions/"\) \\\n              and not path\.startswith\("/engagement/"\) and not user\.is_librarian:\n                return await finish\(auth_error_response\(\n                    request, 403, "Librarian access required",\n                    "Your Member account cannot make administrative or filesystem changes\.",\n                \)\)\n''',
    "", main, count=1,
)

# Explicit route-level authorization classification.
GET_PREFIXES = (
    "/sources", "/intake", "/bulk-match", "/movies/bulk-match", "/shows/bulk-match",
    "/admin", "/api/source-", "/api/scans", "/api/scan-all", "/api/movie-match-analysis",
    "/api/tv-match-analysis", "/api/logs", "/settings", "/getting-started", "/logs",
    "/exports", "/media-info/failures", "/maintenance", "/duplicates",
)

def librarian_get_path(path: str) -> bool:
    if path.startswith(GET_PREFIXES):
        return True
    return bool(re.match(
        r"^/(?:titles/\{title_id\}/(?:tvdb|rename|restore|cover|collections)|"
        r"files/\{file_id\}/(?:rename|collections|edition-version))", path
    ))


def member_safe_post(path: str) -> bool:
    if path in {"/login", "/setup", "/forgot-password", "/logout", "/titles/organize-bulk", "/tags/create"}:
        return True
    if path.startswith("/activate/") or path.startswith("/account/") or path.startswith("/engagement/"):
        return True
    if re.fullmatch(r"/titles/\{title_id\}/(?:favorite|organize)", path):
        return True
    if re.fullmatch(r"/files/\{file_id\}/favorite", path):
        return True
    if re.fullmatch(r"/tags/\{tag_id\}/(?:rename|delete)", path):
        return True
    return False

pattern = re.compile(r'@app\.(get|post)\("([^"]+)"')
def route_replace(match: re.Match) -> str:
    method, path = match.group(1), match.group(2)
    restricted = librarian_get_path(path) if method == "get" else not member_safe_post(path)
    if not restricted:
        return match.group(0)
    return f'@librarian_{method}("{path}"'

main = pattern.sub(route_replace, main)

# Remove all provider I/O and writes from title-detail GET.
block_pattern = re.compile(
    r'''        if \(title\["kind"\] == "tv".*?            except TVDBError:\n                # Synopsis enrichment is optional and must not block local details\.\n                pass\n''',
    re.DOTALL,
)
main, replaced = block_pattern.subn("", main, count=1)
if replaced != 1:
    raise RuntimeError(f"title detail enrichment block replacement count={replaced}")

# Add explicit Librarian POST enrichment directly before the read-only detail route.
detail_marker = '@app.get("/titles/{title_id}", response_class=HTMLResponse)\ndef title_detail'
enrichment_route = '''@librarian_post("/titles/{title_id}/metadata/enrich")
def enrich_title_metadata(title_id: int):
    service = TitleMetadataService(
        db, tvdb, poster_from=poster_from, plex_movie_ids=plex_movie_ids,
        localized_title=localized_tvdb_title, match_confidence=match_confidence,
    )
    try:
        changed = service.enrich(title_id)
    except TVDBError as exc:
        record_event(
            "metadata", "Title metadata enrichment could not reach TVDB.",
            level="warning", detail=str(exc), context={"title_id": title_id},
        )
        return redirect(f"/titles/{title_id}", "TVDB metadata refresh could not finish. Try again later.")
    except ValueError:
        raise HTTPException(404, "Title not found")
    if changed:
        record_event("metadata", "Title metadata was refreshed from TVDB.", context={"title_id": title_id})
    return redirect(
        f"/titles/{title_id}",
        "Metadata refreshed." if changed else "No missing TVDB metadata needed to be refreshed.",
    )


@app.get("/titles/{title_id}", response_class=HTMLResponse)
def title_detail'''
if detail_marker not in main:
    raise RuntimeError("title detail marker not found")
main = main.replace(detail_marker, enrichment_route, 1)
write("app/main.py", main)

# Host updater: cryptographically verify an annotated tag, optionally restrict
# the valid signature fingerprint, before resolving/checking out its commit.
updater = read("scripts/host_updater.py")
updater = updater.replace(
    "def compose_command(files: list[str]) -> list[str]:\n",
    '''def verify_release_tag(
    tag: str, repository: Path, trusted_signing_keys: set[str] | None = None,
) -> None:
    completed = subprocess.run(
        ["git", "verify-tag", "--raw", tag], cwd=repository, text=True,
        capture_output=True, check=False,
    )
    status = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    if completed.returncode:
        raise UpdateError(
            "The release tag does not have a valid cryptographic signature. "
            "The update was stopped before any checkout occurred."
        )
    fingerprints = {
        match.upper() for match in re.findall(r"\[GNUPG:\] VALIDSIG ([0-9A-Fa-f]{40,64})", status)
    }
    trusted = {value.replace(" ", "").upper() for value in (trusted_signing_keys or set()) if value}
    if trusted and fingerprints.isdisjoint(trusted):
        raise UpdateError(
            "The release tag was signed, but not by a configured trusted InfoMancer release key."
        )


def compose_command(files: list[str]) -> list[str]:
''',
    1,
)
updater = updater.replace(
    "    health_url: str, health_timeout: int,\n) -> bool:\n",
    "    health_url: str, health_timeout: int, trusted_signing_keys: set[str] | None = None,\n) -> bool:\n",
    1,
)
updater = updater.replace(
    '        run(["git", "fetch", "--tags", "origin"], repository)\n        target_commit = run(\n',
    '        run(["git", "fetch", "--tags", "origin"], repository)\n        verify_release_tag(tag, repository, trusted_signing_keys)\n        target_commit = run(\n',
    1,
)
updater = updater.replace(
    '    value.add_argument("--health-timeout", type=int, default=120)\n',
    '''    value.add_argument("--health-timeout", type=int, default=120)
    value.add_argument(
        "--trusted-signing-key", action="append", default=[],
        help="Allowed GPG signing-key fingerprint. May be supplied more than once.",
    )
''',
    1,
)
updater = updater.replace(
    '            arguments.health_url, max(15, arguments.health_timeout),\n',
    '            arguments.health_url, max(15, arguments.health_timeout),\n            {value for value in arguments.trusted_signing_key if value.strip()},\n',
    1,
)
write("scripts/host_updater.py", updater)

# Replace the GET-side-effect regression with explicit POST behavior.
tests = read("tests/test_editions.py")
old_start = tests.index("    def test_movie_detail_recovers_synopsis_only_from_matching_external_id(self):\n")
old_end = tests.index("    def test_preview_and_typed_confirmation_precede_catalog_change", old_start)
new_test = '''    def test_movie_detail_get_is_read_only_and_explicit_enrichment_recovers_synopsis(self):
        class FakeTVDB:
            searches = 0

            def search_movies(self, query, year=None):
                self.searches += 1
                return [{"id": 77, "name": "Example Movie", "year": "2020"}]

            def movie(self, movie_id):
                return {
                    "id": movie_id,
                    "overview": "A recovered movie synopsis.",
                    "remoteIds": [
                        {"sourceName": "TheMovieDB.com", "id": "500"},
                        {"sourceName": "IMDB", "id": "tt0000500"},
                    ],
                }

        with self.database.connect() as conn:
            conn.execute(
                """UPDATE titles SET metadata_title='Example Movie',
                   metadata_year=2020,tmdb_id='500',imdb_id='tt0000500'
                   WHERE id=?""", (self.title_id,),
            )
        fake = FakeTVDB()
        original_tvdb = main.tvdb
        main.tvdb = fake
        try:
            page = self.client.get(f"/titles/{self.title_id}")
            self.assertEqual(page.status_code, 200)
            self.assertEqual(fake.searches, 0)
            with self.database.connect() as conn:
                before = conn.execute(
                    "SELECT overview,tvdb_movie_id FROM titles WHERE id=?", (self.title_id,),
                ).fetchone()
            self.assertIsNone(before["overview"])
            self.assertIsNone(before["tvdb_movie_id"])

            response = self.client.post(
                f"/titles/{self.title_id}/metadata/enrich", follow_redirects=False,
            )
        finally:
            main.tvdb = original_tvdb

        self.assertEqual(response.status_code, 303)
        self.assertEqual(fake.searches, 1)
        with self.database.connect() as conn:
            title = conn.execute(
                "SELECT overview,tvdb_movie_id FROM titles WHERE id=?", (self.title_id,),
            ).fetchone()
        self.assertEqual(title["overview"], "A recovered movie synopsis.")
        self.assertEqual(title["tvdb_movie_id"], 77)

'''
tests = tests[:old_start] + new_test + tests[old_end:]
write("tests/test_editions.py", tests)

write("tests/test_migrations.py", '''from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.migrations import MIGRATIONS


class MigrationTests(unittest.TestCase):
    def test_fresh_database_records_all_numbered_migrations_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            database.initialize()
            with database.connect() as conn:
                versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
            self.assertEqual(versions, [migration.version for migration in MIGRATIONS])

    def test_legacy_database_receives_missing_columns(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.db"
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE roots(id INTEGER PRIMARY KEY,path TEXT,kind TEXT,label TEXT,enabled INTEGER DEFAULT 1,last_scanned_at TEXT);
                CREATE TABLE titles(id INTEGER PRIMARY KEY,root_id INTEGER,kind TEXT,title TEXT,year INTEGER,folder_path TEXT,metadata_title TEXT,updated_at TEXT);
                CREATE TABLE files(id INTEGER PRIMARY KEY,title_id INTEGER,path TEXT,filename TEXT,extension TEXT,size_bytes INTEGER,modified_at REAL,season INTEGER,episode_start INTEGER,episode_end INTEGER,parsed_title TEXT,seen_scan TEXT);
            """)
            conn.commit(); conn.close()
            Database(path).initialize()
            with Database(path).connect() as upgraded:
                columns = {row["name"] for row in upgraded.execute("PRAGMA table_info(files)")}
                self.assertIn("edition_name", columns)
                self.assertIn("version_preferred", columns)
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=10").fetchone())


if __name__ == "__main__":
    unittest.main()
''')

write("tests/test_runtime.py", '''from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import Database
from app.runtime import RuntimeLease, RuntimeLeaseError


class RuntimeLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_second_live_process_is_rejected(self):
        first = RuntimeLease(self.database, owner="first", ttl_seconds=60)
        second = RuntimeLease(self.database, owner="second", ttl_seconds=60)
        first.acquire()
        with self.assertRaises(RuntimeLeaseError):
            second.acquire()
        first.release()
        second.acquire()
        second.release()

    def test_expired_lease_can_be_reclaimed(self):
        first = RuntimeLease(self.database, owner="first", ttl_seconds=30)
        first.acquire()
        expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with self.database.connect() as conn:
            conn.execute("UPDATE runtime_leases SET heartbeat_at=?", (expired,))
        second = RuntimeLease(self.database, owner="second", ttl_seconds=30)
        second.acquire()
        second.release()


if __name__ == "__main__":
    unittest.main()
''')

write("tests/test_route_authorization.py", '''from __future__ import annotations

import unittest

from app import main
from app.access import require_librarian


class RouteAuthorizationTests(unittest.TestCase):
    def dependencies_for(self, path: str, method: str):
        for route in main.app.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return [item.call for item in route.dependant.dependencies]
        self.fail(f"Route not found: {method} {path}")

    def test_sensitive_routes_attach_librarian_dependency(self):
        for path, method in (
            ("/settings", "GET"), ("/sources", "GET"), ("/duplicates", "GET"),
            ("/admin/users", "GET"), ("/scan-all", "POST"),
            ("/titles/{title_id}/metadata/enrich", "POST"),
        ):
            with self.subTest(path=path, method=method):
                self.assertIn(require_librarian, self.dependencies_for(path, method))

    def test_member_self_service_routes_do_not_require_librarian(self):
        for path, method in (("/account/profile", "GET"), ("/account/profile", "POST"), ("/titles/{title_id}/favorite", "POST")):
            with self.subTest(path=path, method=method):
                self.assertNotIn(require_librarian, self.dependencies_for(path, method))


if __name__ == "__main__":
    unittest.main()
''')

write("tests/test_host_updater.py", '''from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.host_updater import UpdateError, verify_release_tag


class HostUpdaterTests(unittest.TestCase):
    def test_unsigned_tag_is_rejected(self):
        completed = subprocess.CompletedProcess(["git"], 1, "", "bad signature")
        with tempfile.TemporaryDirectory() as temporary, patch("scripts.host_updater.subprocess.run", return_value=completed):
            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary))

    def test_trusted_signature_fingerprint_is_enforced(self):
        fingerprint = "A" * 40
        completed = subprocess.CompletedProcess(
            ["git"], 0, "", f"[GNUPG:] VALIDSIG {fingerprint} 2026-01-01 0 4 0 1 10 00 {fingerprint}"
        )
        with tempfile.TemporaryDirectory() as temporary, patch("scripts.host_updater.subprocess.run", return_value=completed):
            verify_release_tag("v1.2.3", Path(temporary), {fingerprint})
            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary), {"B" * 40})


if __name__ == "__main__":
    unittest.main()
''')

print("Final architecture/security batch applied")

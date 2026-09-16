from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Form, Request
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Database
from app.maintenance_gate import APPLICATION_MAINTENANCE_GATE
from app.operation_history import OperationHistoryService
from app.routes.context import RouteContext
from app.routes.duplicate_verification_maintenance import build_router as build_duplicate_router
from app.routes.resilience import build_router as build_resilience_router
from app.routes.security_hardening import _install_maintenance_admission_middleware
from app.routes import ROUTER_BUILDERS, build_security_hardening_router
from app.runtime import RuntimeLease, RuntimeLeaseError
from app.runtime_lock import RuntimeProcessLock
from app.safe_file_rename import SafeFileRenameService


class R401DuplicateWorkerAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(APPLICATION_MAINTENANCE_GATE.status()["active_operations"], 0)
        APPLICATION_MAINTENANCE_GATE.end_exclusive()
        self.addCleanup(APPLICATION_MAINTENANCE_GATE.end_exclusive)

    def _router(self, verifier, observed_events):
        job: dict = {"status": "idle"}
        lock = threading.Lock()

        class Duplicates:
            def verify(self, left, right, user_id):
                return verifier(left, right, user_id)

            def decide(self, *_args, **_kwargs):
                return True

        def record_event(*_args, **_kwargs):
            observed_events.append(
                int(APPLICATION_MAINTENANCE_GATE.status()["active_operations"])
            )

        namespace = {
            "Form": Form,
            "Request": Request,
            "duplicate_verify_job": job,
            "duplicate_verify_lock": lock,
            "duplicates": Duplicates(),
            "re": re,
            "record_event": record_event,
            "redirect": lambda path, message: (path, message),
            "threading": threading,
        }
        _router, handlers = build_duplicate_router(RouteContext(namespace))
        return handlers, job, lock

    def test_paused_duplicate_verification_prevents_raw_restore_exclusive_entry(self):
        entered = threading.Event()
        release = threading.Event()
        observed_verify: list[int] = []
        observed_events: list[int] = []

        def verifier(_left, _right, _user_id):
            observed_verify.append(
                int(APPLICATION_MAINTENANCE_GATE.status()["active_operations"])
            )
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return "exact"

        handlers, job, lock = self._router(verifier, observed_events)
        request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)))
        handlers["verify_duplicate"](request, 1, 2)
        self.assertTrue(entered.wait(timeout=2))
        self.assertGreater(APPLICATION_MAINTENANCE_GATE.status()["active_operations"], 0)
        self.assertFalse(
            APPLICATION_MAINTENANCE_GATE.try_begin_exclusive("raw database restore"),
            "raw restore entered exclusive mode while duplicate verification was paused",
        )
        release.set()
        deadline = time.time() + 3
        while time.time() < deadline:
            with lock:
                status = job.get("status")
            if status == "complete":
                break
            time.sleep(0.02)
        self.assertEqual(status, "complete")
        self.assertTrue(observed_verify and all(value > 0 for value in observed_verify))
        self.assertTrue(observed_events and all(value > 0 for value in observed_events))
        self.assertEqual(APPLICATION_MAINTENANCE_GATE.status()["active_operations"], 0)

    def test_exclusive_recovery_refuses_duplicate_worker_before_verification(self):
        called = threading.Event()
        events: list[int] = []

        def verifier(*_args):
            called.set()
            raise AssertionError("duplicate verifier ran during exclusive recovery")

        handlers, job, lock = self._router(verifier, events)
        self.assertTrue(APPLICATION_MAINTENANCE_GATE.try_begin_exclusive("raw restore"))
        request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)))
        handlers["verify_duplicate"](request, 1, 2)
        deadline = time.time() + 2
        while time.time() < deadline:
            with lock:
                status = job.get("status")
            if status == "paused":
                break
            time.sleep(0.02)
        self.assertEqual(status, "paused")
        self.assertFalse(called.is_set())

    def test_structured_error_logging_remains_inside_maintenance_admission(self):
        self.assertIs(ROUTER_BUILDERS[0], build_security_hardening_router)
        observed_events: list[int] = []
        app = FastAPI()

        def record_event(*_args, **_kwargs):
            observed_events.append(
                int(APPLICATION_MAINTENANCE_GATE.status()["active_operations"])
            )

        ctx = RouteContext({"app": app, "record_event": record_event})
        _install_maintenance_admission_middleware(ctx, record_event)
        build_resilience_router(ctx)

        @app.get("/api/r4-boom")
        async def boom():
            raise RuntimeError("r4 injected api failure")

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/r4-boom")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["detail"],
            "InfoMancer hit an unexpected error while handling that request. Do not assume "
            "the action completed. Open Logs for details, then try again.",
        )
        self.assertTrue(observed_events)
        self.assertTrue(all(value > 0 for value in observed_events))
        self.assertEqual(APPLICATION_MAINTENANCE_GATE.status()["active_operations"], 0)


class R402StartupOwnershipTests(unittest.TestCase):
    CHILD_IMPORT = r"""
import sys
try:
    import app.main
except Exception as exc:
    if exc.__class__.__name__ == 'RuntimeLeaseError':
        sys.exit(23)
    raise
sys.exit(0)
"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.path = self.base / "catalog.db"
        self.database = Database(self.path)
        self.database.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def _child_env(self):
        env = os.environ.copy()
        env["INFOMANCER_DATABASE"] = str(self.path)
        env["INFOMANCER_AUTH_MODE"] = "disabled"
        root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def test_runtime_process_lock_creates_missing_database_parent(self):
        fresh = self.base / "new-install" / "nested" / "catalog.db"
        self.assertFalse(fresh.parent.exists())
        probe = RuntimeProcessLock(fresh)
        probe.acquire()
        try:
            self.assertTrue(fresh.parent.is_dir())
            self.assertTrue(probe.path.is_file())
            self.assertFalse(fresh.exists())
        finally:
            probe.release()

    def test_competing_app_import_is_refused_before_announcement_write(self):
        with self.database.connect() as conn:
            conn.execute("CREATE TABLE r4_startup_writes(count INTEGER NOT NULL)")
            conn.execute("INSERT INTO r4_startup_writes(count) VALUES (0)")
            conn.execute(
                """CREATE TRIGGER r4_count_announcement_insert
                   AFTER INSERT ON announcements BEGIN
                     UPDATE r4_startup_writes SET count=count+1;
                   END"""
            )
            conn.execute(
                """CREATE TRIGGER r4_count_announcement_update
                   AFTER UPDATE ON announcements BEGIN
                     UPDATE r4_startup_writes SET count=count+1;
                   END"""
            )

        incumbent = RuntimeLease(self.database, owner="incumbent", ttl_seconds=90)
        incumbent.acquire()
        try:
            result = subprocess.run(
                [sys.executable, "-c", self.CHILD_IMPORT],
                env=self._child_env(), capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(
                result.returncode, 23,
                f"competing startup was not refused before composition: {result.stdout}\n{result.stderr}",
            )
            with self.database.connect() as conn:
                writes = int(conn.execute("SELECT count FROM r4_startup_writes").fetchone()[0])
            self.assertEqual(writes, 0, "losing startup wrote announcements before ownership refusal")
        finally:
            incumbent.release()

    def test_failed_persisted_binding_releases_adopted_startup_lock(self):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO runtime_leases(name,owner,heartbeat_at) VALUES (?,?,?)",
                ("web-runtime", "foreign-owner", datetime.now(timezone.utc).isoformat()),
            )
        with patch.dict(os.environ, {"INFOMANCER_DATABASE": str(self.path)}, clear=False):
            settings = get_settings()
            self.assertEqual(settings.database, self.path)
            contender = RuntimeLease(self.database, owner="challenger", ttl_seconds=90)
            with self.assertRaises(RuntimeLeaseError):
                contender.acquire()

        probe = RuntimeProcessLock(self.path)
        probe.acquire()
        probe.release()


class R403UndoRenameCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "media"
        self.root.mkdir()
        self.show = self.root / "Example Show"
        self.show.mkdir()
        self.old_file = self.show / "old.mkv"
        self.old_file.write_bytes(b"media")
        self.database = Database(self.base / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            self.user_id = int(conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('librarian','Librarian','librarian','test')"""
            ).lastrowid)
            self.root_id = int(conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(self.root), "tv", "TV"),
            ).lastrowid)
            self.title_id = int(conn.execute(
                "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                (self.root_id, "tv", "Example Show", str(self.show)),
            ).lastrowid)
            self.file_id = int(conn.execute(
                """INSERT INTO files(title_id,path,filename,extension,seen_scan)
                   VALUES (?,?,?,?,?)""",
                (self.title_id, str(self.old_file), self.old_file.name, ".mkv", "scan"),
            ).lastrowid)
        self.renames = SafeFileRenameService(self.database)
        self.history = OperationHistoryService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_undo_and_folder_rename_serialize_across_filesystem_and_catalog_commit(self):
        new_file = self.show / "new.mkv"
        self.renames.rename_file(self.file_id, self.old_file, new_file)
        operation_id = self.history.record_file_rename(
            self.file_id, self.old_file, new_file, self.user_id,
        )
        renamed_show = self.root / "Renamed Show"
        moved = threading.Event()
        release_undo = threading.Event()
        folder_done = threading.Event()
        failures: list[BaseException] = []
        original_rename = Path.rename

        def controlled_rename(path_self, target):
            result = original_rename(path_self, target)
            if Path(path_self) == new_file and Path(target) == self.old_file:
                moved.set()
                if not release_undo.wait(timeout=5):
                    raise TimeoutError("test did not release paused undo")
            return result

        def run_undo():
            try:
                self.history.undo(operation_id, self.user_id)
            except BaseException as exc:
                failures.append(exc)

        def run_folder_rename():
            try:
                self.renames.rename_folder(self.title_id, self.show, renamed_show)
            except BaseException as exc:
                failures.append(exc)
            finally:
                folder_done.set()

        with patch.object(Path, "rename", new=controlled_rename):
            undo_thread = threading.Thread(target=run_undo, daemon=True)
            undo_thread.start()
            self.assertTrue(moved.wait(timeout=2), "undo never reached its post-move pause")

            folder_thread = threading.Thread(target=run_folder_rename, daemon=True)
            folder_thread.start()
            self.assertFalse(
                folder_done.wait(timeout=0.25),
                "folder rename crossed the coordinated undo mutation boundary",
            )
            release_undo.set()
            undo_thread.join(timeout=3)
            folder_thread.join(timeout=3)

        self.assertFalse(undo_thread.is_alive())
        self.assertFalse(folder_thread.is_alive())
        self.assertEqual(failures, [])
        final_file = renamed_show / "old.mkv"
        self.assertTrue(final_file.is_file())
        with self.database.connect() as conn:
            title = conn.execute(
                "SELECT folder_path FROM titles WHERE id=?", (self.title_id,)
            ).fetchone()
            file_row = conn.execute(
                "SELECT path FROM files WHERE id=?", (self.file_id,)
            ).fetchone()
            operation = conn.execute(
                "SELECT status FROM operation_history WHERE id=?", (operation_id,)
            ).fetchone()
        self.assertEqual(title["folder_path"], str(renamed_show))
        self.assertEqual(file_row["path"], str(final_file))
        self.assertEqual(operation["status"], "undone")


if __name__ == "__main__":
    unittest.main()

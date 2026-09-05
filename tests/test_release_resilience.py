from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import maintenance
from app.duplicate_trash import DuplicateTrashError, DuplicateTrashService
from app.media_info import MediaInspectionError, inspect_media
from app.naming import contained_destination
from app.provider_secrets import ProviderSecretError, ProviderSecretStore
from app.routes.context import RouteContext
from app.routes.resilience import build_router as build_resilience_router


class FilesystemResilienceTests(unittest.TestCase):
    def test_duplicate_trash_translates_windows_resolution_error(self):
        with mock.patch.object(
            Path,
            "resolve",
            side_effect=OSError(1272, "Guest access is blocked"),
        ):
            with self.assertRaises(DuplicateTrashError) as caught:
                DuplicateTrashService._require_inside(Path("B:/movie.mkv"), Path("B:/"))
        self.assertIn("unavailable or unreadable", str(caught.exception))

    def test_maintenance_translates_windows_resolution_error(self):
        with mock.patch.object(
            Path,
            "resolve",
            side_effect=OSError(1272, "Guest access is blocked"),
        ):
            with self.assertRaises(maintenance.MaintenanceError) as caught:
                maintenance._resolved(Path("B:/"))
        self.assertIn("unavailable or unreadable", str(caught.exception))

    def test_rename_preview_does_not_resolve_storage(self):
        source = Path("B:/Movies/Example.mkv")
        with mock.patch.object(
            Path,
            "resolve",
            side_effect=AssertionError("rename preview must stay lexical"),
        ):
            destination = contained_destination(source, "Example (2026).mkv")
        self.assertEqual(destination.parent, source.parent)
        self.assertEqual(destination.name, "Example (2026).mkv")

    def test_provider_secret_directory_error_is_translated(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ProviderSecretStore(
                Path(temporary) / "missing" / "provider-secrets.enc",
                "test-secret",
            )
            with mock.patch.object(Path, "mkdir", side_effect=OSError(13, "denied")):
                with self.assertRaises(ProviderSecretError) as caught:
                    store.update({"tvdb_api_key": "example"})
        self.assertIn("could not save", str(caught.exception))

    def test_ffprobe_spawn_oserror_is_translated(self):
        with tempfile.TemporaryDirectory() as temporary:
            media = Path(temporary) / "example.mkv"
            media.write_bytes(b"test")
            with mock.patch(
                "app.media_info.subprocess.run",
                side_effect=OSError(1272, "Guest access is blocked"),
            ):
                with self.assertRaises(MediaInspectionError) as caught:
                    inspect_media(media)
        self.assertEqual(caught.exception.headline, "Media inspection could not start")
        self.assertIn("1272", caught.exception.technical_detail)


class ApiResilienceTests(unittest.TestCase):
    def test_unexpected_api_failure_returns_json_and_logs(self):
        app = FastAPI()
        events: list[tuple[tuple, dict]] = []
        namespace = {
            "app": app,
            "record_event": lambda *args, **kwargs: events.append((args, kwargs)),
        }
        router, _ = build_resilience_router(RouteContext(namespace))
        app.include_router(router)

        @app.get("/api/failure")
        def fail():
            raise OSError(1272, "Guest access is blocked")

        with TestClient(app) as client:
            response = client.get("/api/failure")

        self.assertEqual(response.status_code, 500)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.assertIn("detail", response.json())
        self.assertTrue(events)
        self.assertIn("OSError", events[0][1]["detail"])


if __name__ == "__main__":
    unittest.main()

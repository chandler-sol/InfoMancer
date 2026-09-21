from __future__ import annotations

from copy import deepcopy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.media_identity.provider_cache import (
    ProviderEpisodeCache,
    ProviderEpisodeLimits,
    ProviderEpisodeRefreshError,
)
from app.media_identity.tvdb_orders import episode_orders, episodes_for_order
from app.tvdb import TVDBClient, TVDBError


class FakeTVDBTransport:
    def __init__(
        self, *, season_types=None, default_season_type=1,
        pages=None, last_updated="2026-09-17T12:00:00Z", fail_paths=None,
    ):
        self.season_types = season_types or []
        self.default_season_type = default_season_type
        self.pages = pages or {}
        self.last_updated = last_updated
        self.fail_paths = set(fail_paths or ())
        self.calls: list[tuple[str, int | None, bool]] = []

    def _get(
        self, path: str, params: dict | None = None, *,
        allow_not_found: bool = False, _retry_auth: bool = True,
    ) -> dict:
        page = None if params is None else params.get("page")
        self.calls.append((path, page, allow_not_found))
        if path in self.fail_paths:
            raise TVDBError("simulated provider failure")
        if path.endswith("/extended"):
            return {
                "data": {
                    "seasonTypes": deepcopy(self.season_types),
                    "defaultSeasonType": self.default_season_type,
                    "lastUpdated": self.last_updated,
                }
            }
        key = (path, int(page or 0))
        if key not in self.pages:
            if allow_not_found:
                return {}
            raise AssertionError(f"Unexpected TVDB request: {key}")
        return deepcopy(self.pages[key])


def episode(
    episode_id: int, season: int, number: int, name: str, *,
    overview: str = "", aired: str = "2026-01-01", absolute: int | None = None,
) -> dict:
    value = {
        "id": episode_id,
        "seasonNumber": season,
        "number": number,
        "name": name,
        "overview": overview,
        "aired": aired,
    }
    if absolute is not None:
        value["absoluteNumber"] = absolute
    return value


def payload(rows: list[dict], next_link=None) -> dict:
    return {"data": {"episodes": rows}, "links": {"next": next_link}}


class ProviderEpisodeCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        self.cache = ProviderEpisodeCache(self.database)
        with self.database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/shows','tv','Shows')"
            ).lastrowid
            self.title_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path,tvdb_id)
                   VALUES (?,'tv','Order Show','/shows/Order Show',9001)""",
                (root_id,),
            ).lastrowid
            conn.execute(
                """INSERT INTO expected_episodes(
                     title_id,tvdb_episode_id,season,episode,name,aired
                   ) VALUES (?,101,1,1,'Existing Default Episode','2026-01-01')""",
                (self.title_id,),
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def rich_transport(*, last_updated="2026-09-17T12:00:00Z") -> FakeTVDBTransport:
        season_types = [
            {"id": 1, "type": "official", "name": "Aired Order"},
            {"id": 2, "type": "dvd", "name": "DVD Order"},
            {"id": 3, "type": "alternate", "alternateName": "Story Order"},
        ]
        pages = {
            ("/series/9001/episodes/default/eng", 0): payload([
                episode(101, 1, 1, "Pilot", overview="Opening synopsis", absolute=1),
                episode(199, 0, 1, "Special", overview="Special synopsis"),
            ], next_link="page-1"),
            ("/series/9001/episodes/default/eng", 1): payload([
                episode(102, 1, 2, "Second", overview="Second synopsis", absolute=2),
            ]),
            ("/series/9001/episodes/official/eng", 0): payload([
                episode(101, 1, 1, "Pilot", overview="Opening synopsis", absolute=1),
                episode(102, 1, 2, "Second", overview="Second synopsis", absolute=2),
                episode(199, 0, 1, "Special", overview="Special synopsis"),
            ]),
            ("/series/9001/episodes/dvd/eng", 0): payload([
                episode(101, 1, 2, "Pilot", overview="Opening synopsis", absolute=1),
                episode(101, 1, 2, "Pilot", overview="Opening synopsis", absolute=1),
                episode(102, 1, 1, "Second", overview="Second synopsis", absolute=2),
            ]),
            ("/series/9001/episodes/alternate/eng", 0): payload([
                episode(101, 1, 5, "Pilot", overview="Opening synopsis", absolute=1),
                episode(102, 1, 5, "Second", overview="Second synopsis", absolute=2),
            ]),
        }
        return FakeTVDBTransport(
            season_types=season_types,
            default_season_type=1,
            pages=pages,
            last_updated=last_updated,
        )

    def test_order_discovery_is_dynamic_and_keeps_default_alias(self) -> None:
        transport = self.rich_transport()
        result = episode_orders(transport, 9001)
        self.assertEqual(
            [item["namespace"] for item in result["orders"]],
            ["default", "official", "alternate", "dvd"],
        )
        official = next(item for item in result["orders"] if item["namespace"] == "official")
        alternate = next(item for item in result["orders"] if item["namespace"] == "alternate")
        self.assertTrue(official["default"])
        self.assertEqual(alternate["name"], "Story Order")

    def test_order_fetch_paginates_and_unsupported_order_is_empty_evidence(self) -> None:
        transport = self.rich_transport()
        default = episodes_for_order(transport, 9001, "default")
        missing = episodes_for_order(transport, 9001, "regional")
        self.assertEqual([item["id"] for item in default], [101, 199, 102])
        self.assertEqual(missing, [])
        self.assertIn(("/series/9001/episodes/default/eng", 1, True), transport.calls)
        self.assertIn(("/series/9001/episodes/regional/eng", 0, True), transport.calls)

    def test_refresh_preserves_one_identity_across_multiple_orders(self) -> None:
        result = self.cache.refresh_tvdb_title(self.title_id, self.rich_transport())
        self.assertEqual(result.episode_count, 3)
        self.assertEqual(set(result.order_namespaces), {"default", "official", "dvd", "alternate"})

        mappings = self.cache.mappings_for_episode("tvdb", "9001", "101")
        coordinates = {
            (row["order_namespace"], row["season"], row["episode"])
            for row in mappings
        }
        self.assertIn(("default", 1, 1), coordinates)
        self.assertIn(("official", 1, 1), coordinates)
        self.assertIn(("dvd", 1, 2), coordinates)
        self.assertIn(("alternate", 1, 5), coordinates)
        self.assertEqual(
            sum(1 for row in mappings if row["order_namespace"] == "dvd"), 1,
            "duplicate provider rows must not duplicate an exact mapping",
        )

        with self.database.connect() as conn:
            identity = conn.execute(
                """SELECT name,overview FROM provider_episode_identities
                   WHERE provider='tvdb' AND provider_series_id='9001'
                     AND provider_episode_id='101' AND language='eng'"""
            ).fetchone()
            special = conn.execute(
                """SELECT 1 FROM provider_episode_mappings
                   WHERE provider='tvdb' AND provider_series_id='9001'
                     AND provider_episode_id='199' AND order_namespace='default'
                     AND season=0 AND episode=1"""
            ).fetchone()
        self.assertEqual(identity["name"], "Pilot")
        self.assertEqual(identity["overview"], "Opening synopsis")
        self.assertIsNotNone(special)

    def test_ambiguous_provider_coordinate_is_retained_not_guessed(self) -> None:
        self.cache.refresh_tvdb_series(9001, self.rich_transport())
        resolved = self.cache.resolve_coordinate(
            "tvdb", "9001", "alternate", 1, 5,
        )
        self.assertTrue(resolved["ambiguous"])
        self.assertEqual(
            [row["provider_episode_id"] for row in resolved["candidates"]],
            ["101", "102"],
        )

    def test_multiple_mapping_variants_for_one_identity_are_not_false_ambiguity(self) -> None:
        transport = FakeTVDBTransport(
            pages={
                ("/series/9001/episodes/default/eng", 0): payload([
                    episode(101, 1, 1, "Pilot", absolute=1),
                    episode(101, 1, 1, "Pilot", absolute=99),
                ])
            }
        )
        self.cache.refresh_tvdb_series(9001, transport)
        resolved = self.cache.resolve_coordinate("tvdb", "9001", "default", 1, 1)
        self.assertFalse(resolved["ambiguous"])
        self.assertEqual(len(resolved["candidates"]), 1)
        self.assertEqual(
            [variant["absolute_number"] for variant in resolved["candidates"][0]["mapping_variants"]],
            [1, 99],
        )

    def test_provider_refresh_never_changes_expected_episodes(self) -> None:
        with self.database.connect() as conn:
            before = [tuple(row) for row in conn.execute(
                """SELECT tvdb_episode_id,season,episode,name,aired
                   FROM expected_episodes WHERE title_id=? ORDER BY id""",
                (self.title_id,),
            )]
        self.cache.refresh_tvdb_title(self.title_id, self.rich_transport())
        with self.database.connect() as conn:
            after = [tuple(row) for row in conn.execute(
                """SELECT tvdb_episode_id,season,episode,name,aired
                   FROM expected_episodes WHERE title_id=? ORDER BY id""",
                (self.title_id,),
            )]
        self.assertEqual(after, before)

    def test_signature_is_deterministic_and_changes_with_provider_content(self) -> None:
        first = self.cache.refresh_tvdb_series(9001, self.rich_transport())
        second = self.cache.refresh_tvdb_series(9001, self.rich_transport())
        self.assertEqual(first.source_signature, second.source_signature)

        changed = self.rich_transport(last_updated="2026-09-18T00:00:00Z")
        third = self.cache.refresh_tvdb_series(9001, changed)
        self.assertNotEqual(first.source_signature, third.source_signature)
        status = self.cache.cache_status("tvdb", "9001")
        self.assertEqual(status["source_signature"], third.source_signature)
        self.assertEqual(status["provider_updated_at"], "2026-09-18T00:00:00Z")

    def test_provider_failure_preserves_previous_complete_cache(self) -> None:
        baseline = self.cache.refresh_tvdb_series(9001, self.rich_transport())
        failing = self.rich_transport(last_updated="2026-09-18T00:00:00Z")
        failing.fail_paths.add("/series/9001/episodes/dvd/eng")
        with self.assertRaises(TVDBError):
            self.cache.refresh_tvdb_series(9001, failing)
        status = self.cache.cache_status("tvdb", "9001")
        self.assertEqual(status["source_signature"], baseline.source_signature)
        self.assertEqual(status["provider_updated_at"], "2026-09-17T12:00:00Z")

    def test_empty_provider_snapshot_cannot_replace_previous_non_empty_cache(self) -> None:
        baseline = self.cache.refresh_tvdb_series(9001, self.rich_transport())
        empty = FakeTVDBTransport(
            pages={
                ("/series/9001/episodes/default/eng", 0): payload([]),
            },
            last_updated="2026-09-19T00:00:00Z",
        )
        with self.assertRaises(ProviderEpisodeRefreshError):
            self.cache.refresh_tvdb_series(9001, empty)
        status = self.cache.cache_status("tvdb", "9001")
        self.assertEqual(status["source_signature"], baseline.source_signature)
        self.assertEqual(status["episode_count"], baseline.episode_count)
        self.assertEqual(status["provider_updated_at"], "2026-09-17T12:00:00Z")

    def test_missing_advertised_continuation_preserves_every_previous_cache_row(self) -> None:
        self.cache.refresh_tvdb_series(9001, self.rich_transport())
        with self.database.connect() as conn:
            before_series = [
                tuple(row) for row in conn.execute(
                    """SELECT provider,provider_series_id,language,provider_updated_at,
                              source_signature,episode_count,mapping_count,order_namespaces_json
                       FROM provider_episode_series_cache
                       WHERE provider='tvdb' AND provider_series_id='9001'
                       ORDER BY language"""
                )
            ]
            before_identities = [
                tuple(row) for row in conn.execute(
                    """SELECT provider_series_id,provider_episode_id,language,name,
                              overview,aired,metadata_json
                       FROM provider_episode_identities
                       WHERE provider='tvdb' AND provider_series_id='9001'
                       ORDER BY provider_episode_id,language"""
                )
            ]
            before_mappings = [
                tuple(row) for row in conn.execute(
                    """SELECT provider_series_id,provider_episode_id,language,
                              order_namespace,order_name,season,episode,absolute_number,
                              coordinate_key,details_json
                       FROM provider_episode_mappings
                       WHERE provider='tvdb' AND provider_series_id='9001'
                       ORDER BY provider_episode_id,order_namespace,coordinate_key"""
                )
            ]

        incomplete = self.rich_transport(last_updated="2026-09-20T00:00:00Z")
        incomplete.pages.pop(("/series/9001/episodes/default/eng", 1))
        with self.assertRaises(ProviderEpisodeRefreshError):
            self.cache.refresh_tvdb_series(9001, incomplete)

        with self.database.connect() as conn:
            after_series = [
                tuple(row) for row in conn.execute(
                    """SELECT provider,provider_series_id,language,provider_updated_at,
                              source_signature,episode_count,mapping_count,order_namespaces_json
                       FROM provider_episode_series_cache
                       WHERE provider='tvdb' AND provider_series_id='9001'
                       ORDER BY language"""
                )
            ]
            after_identities = [
                tuple(row) for row in conn.execute(
                    """SELECT provider_series_id,provider_episode_id,language,name,
                              overview,aired,metadata_json
                       FROM provider_episode_identities
                       WHERE provider='tvdb' AND provider_series_id='9001'
                       ORDER BY provider_episode_id,language"""
                )
            ]
            after_mappings = [
                tuple(row) for row in conn.execute(
                    """SELECT provider_series_id,provider_episode_id,language,
                              order_namespace,order_name,season,episode,absolute_number,
                              coordinate_key,details_json
                       FROM provider_episode_mappings
                       WHERE provider='tvdb' AND provider_series_id='9001'
                       ORDER BY provider_episode_id,order_namespace,coordinate_key"""
                )
            ]
        self.assertEqual(after_series, before_series)
        self.assertEqual(after_identities, before_identities)
        self.assertEqual(after_mappings, before_mappings)

    def test_missing_first_page_for_advertised_order_preserves_previous_snapshot(self) -> None:
        baseline = self.cache.refresh_tvdb_series(9001, self.rich_transport())
        incomplete = self.rich_transport(last_updated="2026-09-20T06:00:00Z")
        incomplete.pages.pop(("/series/9001/episodes/dvd/eng", 0))

        with self.assertRaisesRegex(
            ProviderEpisodeRefreshError,
            "advertised episode-order namespace",
        ):
            self.cache.refresh_tvdb_series(9001, incomplete)

        status = self.cache.cache_status("tvdb", "9001")
        self.assertEqual(status["source_signature"], baseline.source_signature)
        self.assertEqual(status["episode_count"], baseline.episode_count)
        self.assertEqual(status["mapping_count"], baseline.mapping_count)
        mappings = self.cache.mappings_for_episode("tvdb", "9001", "101")
        self.assertTrue(
            any(row["order_namespace"] == "dvd" for row in mappings),
            "a transient missing page zero must not erase an existing advertised order",
        )

    def test_configurable_aggregate_record_limit_preserves_previous_snapshot(self) -> None:
        baseline = self.cache.refresh_tvdb_series(9001, self.rich_transport())
        limited = ProviderEpisodeCache(
            self.database,
            limits=ProviderEpisodeLimits(max_records=2),
        )
        with self.assertRaises(ProviderEpisodeRefreshError):
            limited.refresh_tvdb_series(
                9001, self.rich_transport(last_updated="2026-09-20T00:00:00Z")
            )
        status = self.cache.cache_status("tvdb", "9001")
        self.assertEqual(status["source_signature"], baseline.source_signature)
        self.assertEqual(status["episode_count"], baseline.episode_count)
        self.assertEqual(status["mapping_count"], baseline.mapping_count)

    def test_configurable_per_order_limit_preserves_previous_snapshot(self) -> None:
        baseline = self.cache.refresh_tvdb_series(9001, self.rich_transport())
        limited = ProviderEpisodeCache(
            self.database,
            limits=ProviderEpisodeLimits(max_episodes_per_order=2),
        )
        with self.assertRaises(ProviderEpisodeRefreshError):
            limited.refresh_tvdb_series(
                9001, self.rich_transport(last_updated="2026-09-20T00:00:00Z")
            )
        status = self.cache.cache_status("tvdb", "9001")
        self.assertEqual(status["source_signature"], baseline.source_signature)
        self.assertEqual(status["episode_count"], baseline.episode_count)

    def test_configurable_text_budget_preserves_previous_snapshot(self) -> None:
        baseline = self.cache.refresh_tvdb_series(9001, self.rich_transport())
        limited = ProviderEpisodeCache(
            self.database,
            limits=ProviderEpisodeLimits(max_text_chars=10),
        )
        with self.assertRaises(ProviderEpisodeRefreshError):
            limited.refresh_tvdb_series(
                9001, self.rich_transport(last_updated="2026-09-20T00:00:00Z")
            )
        status = self.cache.cache_status("tvdb", "9001")
        self.assertEqual(status["source_signature"], baseline.source_signature)

    def test_tvdb_bounded_transport_rejects_response_over_byte_limit(self) -> None:
        class StreamResponse:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def raise_for_status(self):
                return None

            def iter_bytes(self, chunk_size=65536):
                yield b'{"data":"'
                yield b"x" * 64
                yield b'"}'

        client = TVDBClient("testing-key", _token="already-authenticated")
        with patch("app.tvdb.httpx.stream", return_value=StreamResponse()):
            with self.assertRaisesRegex(TVDBError, "safety limit"):
                client._get_bounded(
                    "/series/9001/extended",
                    max_bytes=32,
                )

    def test_database_failure_rolls_back_replacement_and_preserves_old_cache(self) -> None:
        baseline = self.cache.refresh_tvdb_series(9001, self.rich_transport())
        with self.database.connect() as conn:
            conn.execute(
                """CREATE TRIGGER fail_provider_dvd_mapping
                   BEFORE INSERT ON provider_episode_mappings
                   WHEN NEW.order_namespace='dvd'
                   BEGIN
                     SELECT RAISE(ABORT,'forced provider cache failure');
                   END"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.cache.refresh_tvdb_series(
                9001, self.rich_transport(last_updated="2026-09-18T00:00:00Z")
            )
        status = self.cache.cache_status("tvdb", "9001")
        self.assertEqual(status["source_signature"], baseline.source_signature)
        self.assertEqual(status["provider_updated_at"], "2026-09-17T12:00:00Z")

    def test_existing_tvdb_default_episode_method_keeps_legacy_endpoint_contract(self) -> None:
        client = TVDBClient("testing-key")
        calls = []

        def fake_get(path, params=None, **_kwargs):
            calls.append((path, dict(params or {})))
            return payload([episode(101, 1, 1, "Pilot")])

        client._get = fake_get  # type: ignore[method-assign]
        rows = client.episodes(9001)
        self.assertEqual([row["id"] for row in rows], [101])
        self.assertEqual(
            calls,
            [("/series/9001/episodes/default/eng", {"page": 0})],
        )


if __name__ == "__main__":
    unittest.main()

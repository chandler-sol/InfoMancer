from __future__ import annotations

import gzip
import io
from collections.abc import Callable
from urllib.request import Request, urlopen

from .db import Database


IMDB_BASICS_URL = "https://datasets.imdbws.com/title.basics.tsv.gz"
IMDB_RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
IMDB_CREW_URL = "https://datasets.imdbws.com/title.crew.tsv.gz"
IMDB_PRINCIPALS_URL = "https://datasets.imdbws.com/title.principals.tsv.gz"
IMDB_NAMES_URL = "https://datasets.imdbws.com/name.basics.tsv.gz"
IMDB_EPISODES_URL = "https://datasets.imdbws.com/title.episode.tsv.gz"


def imdb_ids(value: str) -> list[str]:
    return [] if not value or value == r"\N" else value.split(",")


def sync_genres(
    database: Database,
    progress: Callable[[str, int, int, int], None] | None = None,
    title_ids: list[int] | tuple[int, ...] | None = None,
    episode_ids: list[int] | tuple[int, ...] | None = None,
) -> dict[str, int]:
    """Retain free IMDb metadata, optionally scoped to titles or episodes."""
    title_scope = tuple(dict.fromkeys(int(value) for value in title_ids or ()))
    episode_scope = tuple(dict.fromkeys(int(value) for value in episode_ids or ()))
    with database.connect() as conn:
        title_filter = ""
        title_parameters: tuple[int, ...] = ()
        if title_ids is not None:
            title_filter = f" AND id IN ({','.join('?' for _ in title_scope)})" if title_scope else " AND 0"
            title_parameters = title_scope
        wanted = {
            row["imdb_id"] for row in conn.execute(
                f"SELECT imdb_id FROM titles WHERE imdb_id IS NOT NULL AND imdb_id != ''{title_filter}",
                title_parameters,
            )
        }
    if not wanted:
        return {
            "requested": 0, "matched": 0, "records": 0,
            "ratings_matched": 0, "ratings_records": 0,
            "credits_matched": 0, "people_matched": 0,
        }
    if progress:
        progress("basics", 0, 0, len(wanted))

    found: dict[str, tuple[str, str]] = {}
    records = 0
    request = Request(IMDB_BASICS_URL, headers={"User-Agent": "InfoMancer/0.1"})
    with urlopen(request, timeout=60) as response:
        with gzip.GzipFile(fileobj=response) as archive:
            with io.TextIOWrapper(archive, encoding="utf-8", newline="") as stream:
                header = stream.readline().rstrip("\n").split("\t")
                id_index = header.index("tconst")
                type_index = header.index("titleType")
                genre_index = header.index("genres")
                for line in stream:
                    records += 1
                    fields = line.rstrip("\n").split("\t")
                    imdb_id = fields[id_index]
                    if imdb_id in wanted:
                        raw_genres = fields[genre_index]
                        found[imdb_id] = (
                            "" if raw_genres == r"\N" else raw_genres,
                            fields[type_index],
                        )
                    if progress and records % 250_000 == 0:
                        progress("basics", records, len(found), len(wanted))
                    if len(found) == len(wanted):
                        break

    with database.connect() as conn:
        for imdb_id, (genres, title_type) in found.items():
            conn.execute(
                """UPDATE titles SET genres=?, imdb_title_type=?,
                   updated_at=CURRENT_TIMESTAMP WHERE imdb_id=?""",
                (genres, title_type, imdb_id),
            )
    if progress:
        progress("basics", records, len(found), len(wanted))

    ratings: dict[str, tuple[float, int]] = {}
    ratings_records = 0
    if progress:
        progress("ratings", 0, 0, len(wanted))
    ratings_request = Request(
        IMDB_RATINGS_URL, headers={"User-Agent": "InfoMancer/0.1"}
    )
    with urlopen(ratings_request, timeout=60) as response:
        with gzip.GzipFile(fileobj=response) as archive:
            with io.TextIOWrapper(archive, encoding="utf-8", newline="") as stream:
                header = stream.readline().rstrip("\n").split("\t")
                id_index = header.index("tconst")
                rating_index = header.index("averageRating")
                votes_index = header.index("numVotes")
                for line in stream:
                    ratings_records += 1
                    fields = line.rstrip("\n").split("\t")
                    imdb_id = fields[id_index]
                    if imdb_id in wanted:
                        ratings[imdb_id] = (
                            float(fields[rating_index]), int(fields[votes_index])
                        )
                    if progress and ratings_records % 250_000 == 0:
                        progress(
                            "ratings", ratings_records, len(ratings), len(wanted)
                        )

    with database.connect() as conn:
        for imdb_id, (rating, votes) in ratings.items():
            conn.execute(
                """UPDATE titles SET imdb_rating=?, imdb_votes=?,
                   updated_at=CURRENT_TIMESTAMP WHERE imdb_id=?""",
                (rating, votes, imdb_id),
            )
    if progress:
        progress("ratings", ratings_records, len(ratings), len(wanted))

    with database.connect() as conn:
        wanted_titles = {
            row["imdb_id"]: (row["id"], row["kind"]) for row in conn.execute(
                f"""SELECT id, kind, imdb_id FROM titles
                   WHERE imdb_id IS NOT NULL AND imdb_id != ''{title_filter}""",
                title_parameters,
            )
        }
        expected_filters = [
            "t.kind='tv'", "t.imdb_id IS NOT NULL", "t.imdb_id != ''"
        ]
        expected_parameters: list[int] = []
        if title_ids is not None:
            if title_scope:
                expected_filters.append(
                    f"t.id IN ({','.join('?' for _ in title_scope)})"
                )
                expected_parameters.extend(title_scope)
            else:
                expected_filters.append("0")
        if episode_ids is not None:
            if episode_scope:
                expected_filters.append(
                    f"e.id IN ({','.join('?' for _ in episode_scope)})"
                )
                expected_parameters.extend(episode_scope)
            else:
                expected_filters.append("0")
        expected_lookup = {
            (row["series_imdb_id"], row["season"], row["episode"]): row["id"]
            for row in conn.execute(
                f"""SELECT e.id, e.season, e.episode, t.imdb_id series_imdb_id
                   FROM expected_episodes e JOIN titles t ON t.id=e.title_id
                   WHERE {' AND '.join(expected_filters)}""",
                expected_parameters,
            )
        }
    if not wanted_titles:
        return {
            "requested": len(wanted), "matched": len(found), "records": records,
            "ratings_matched": len(ratings), "ratings_records": ratings_records,
            "credits_matched": 0, "people_matched": 0,
        }

    episode_targets: dict[str, int] = {}
    episode_records = 0
    if expected_lookup:
        if progress:
            progress("episodes", 0, 0, len(expected_lookup))
        episode_request = Request(
            IMDB_EPISODES_URL, headers={"User-Agent": "InfoMancer/0.1"}
        )
        with urlopen(episode_request, timeout=60) as response:
            with gzip.GzipFile(fileobj=response) as archive:
                with io.TextIOWrapper(archive, encoding="utf-8", newline="") as stream:
                    header = stream.readline().rstrip("\n").split("\t")
                    id_index = header.index("tconst")
                    parent_index = header.index("parentTconst")
                    season_index = header.index("seasonNumber")
                    episode_index = header.index("episodeNumber")
                    for line in stream:
                        episode_records += 1
                        fields = line.rstrip("\n").split("\t")
                        if fields[season_index].isdigit() and fields[episode_index].isdigit():
                            key = (
                                fields[parent_index], int(fields[season_index]),
                                int(fields[episode_index]),
                            )
                            if key in expected_lookup:
                                episode_targets[fields[id_index]] = expected_lookup[key]
                        if progress and episode_records % 500_000 == 0:
                            progress(
                                "episodes", episode_records,
                                len(episode_targets), len(expected_lookup),
                            )
        with database.connect() as conn:
            for episode_imdb_id, expected_id in episode_targets.items():
                conn.execute(
                    "UPDATE expected_episodes SET imdb_id=? WHERE id=?",
                    (episode_imdb_id, expected_id),
                )
        if progress:
            progress("episodes", episode_records, len(episode_targets), len(expected_lookup))

    crew_targets = set(wanted_titles) | set(episode_targets)
    crew: dict[str, tuple[list[str], list[str]]] = {}
    crew_records = 0
    if progress:
        progress("crew", 0, 0, len(crew_targets))
    crew_request = Request(IMDB_CREW_URL, headers={"User-Agent": "InfoMancer/0.1"})
    with urlopen(crew_request, timeout=60) as response:
        with gzip.GzipFile(fileobj=response) as archive:
            with io.TextIOWrapper(archive, encoding="utf-8", newline="") as stream:
                header = stream.readline().rstrip("\n").split("\t")
                id_index = header.index("tconst")
                director_index = header.index("directors")
                writer_index = header.index("writers")
                for line in stream:
                    crew_records += 1
                    fields = line.rstrip("\n").split("\t")
                    imdb_id = fields[id_index]
                    if imdb_id in crew_targets:
                        crew[imdb_id] = (
                            imdb_ids(fields[director_index]),
                            imdb_ids(fields[writer_index]),
                        )
                    if progress and crew_records % 250_000 == 0:
                        progress("crew", crew_records, len(crew), len(crew_targets))
                    if len(crew) == len(crew_targets):
                        break
    if progress:
        progress("crew", crew_records, len(crew), len(crew_targets))

    actors: dict[str, list[tuple[str, int]]] = {imdb_id: [] for imdb_id in wanted_titles}
    principal_titles_seen: set[str] = set()
    principal_records = 0
    if progress:
        progress("principals", 0, 0, len(wanted_titles))
    principals_request = Request(
        IMDB_PRINCIPALS_URL, headers={"User-Agent": "InfoMancer/0.1"}
    )
    with urlopen(principals_request, timeout=60) as response:
        with gzip.GzipFile(fileobj=response) as archive:
            with io.TextIOWrapper(archive, encoding="utf-8", newline="") as stream:
                header = stream.readline().rstrip("\n").split("\t")
                id_index = header.index("tconst")
                order_index = header.index("ordering")
                person_index = header.index("nconst")
                category_index = header.index("category")
                for line in stream:
                    principal_records += 1
                    fields = line.rstrip("\n").split("\t")
                    imdb_id = fields[id_index]
                    if imdb_id in wanted_titles:
                        principal_titles_seen.add(imdb_id)
                        if fields[category_index] in {"actor", "actress"}:
                            actors[imdb_id].append(
                                (fields[person_index], int(fields[order_index]))
                            )
                    if progress and principal_records % 500_000 == 0:
                        progress(
                            "principals", principal_records,
                            len(principal_titles_seen), len(wanted_titles),
                        )
                    if len(principal_titles_seen) == len(wanted_titles):
                        # Rows are grouped by title. Continue until the group changes
                        # so every billed actor for the final wanted title is retained.
                        final_title = imdb_id
                        for remaining in stream:
                            principal_records += 1
                            final_fields = remaining.rstrip("\n").split("\t")
                            if final_fields[id_index] != final_title:
                                break
                            if final_fields[category_index] in {"actor", "actress"}:
                                actors[final_title].append(
                                    (final_fields[person_index], int(final_fields[order_index]))
                                )
                        break
    if progress:
        progress(
            "principals", principal_records,
            len(principal_titles_seen), len(wanted_titles),
        )

    wanted_people = {
        person_id
        for directors, writers in crew.values()
        for person_id in directors + writers
    }
    wanted_people.update(
        person_id for title_actors in actors.values() for person_id, _order in title_actors
    )
    people: dict[str, str] = {}
    name_records = 0
    if wanted_people:
        if progress:
            progress("names", 0, 0, len(wanted_people))
        names_request = Request(IMDB_NAMES_URL, headers={"User-Agent": "InfoMancer/0.1"})
        with urlopen(names_request, timeout=60) as response:
            with gzip.GzipFile(fileobj=response) as archive:
                with io.TextIOWrapper(archive, encoding="utf-8", newline="") as stream:
                    header = stream.readline().rstrip("\n").split("\t")
                    id_index = header.index("nconst")
                    name_index = header.index("primaryName")
                    for line in stream:
                        name_records += 1
                        fields = line.rstrip("\n").split("\t")
                        person_id = fields[id_index]
                        if person_id in wanted_people:
                            people[person_id] = fields[name_index]
                        if progress and name_records % 500_000 == 0:
                            progress("names", name_records, len(people), len(wanted_people))
                        if len(people) == len(wanted_people):
                            break
        if progress:
            progress("names", name_records, len(people), len(wanted_people))

    credits: list[tuple[int, str, str, str, int]] = []
    credited_titles: set[int] = set()
    for imdb_id, (title_id, _kind) in wanted_titles.items():
        directors, writers = crew.get(imdb_id, ([], []))
        for order, person_id in enumerate(directors, start=1):
            if person_id in people:
                credits.append((title_id, person_id, people[person_id], "director", order))
                credited_titles.add(title_id)
        for order, person_id in enumerate(writers, start=1):
            if person_id in people:
                credits.append((title_id, person_id, people[person_id], "writer", order))
                credited_titles.add(title_id)
        for person_id, order in sorted(actors.get(imdb_id, []), key=lambda item: item[1]):
            if person_id in people:
                credits.append((title_id, person_id, people[person_id], "actor", order))
                credited_titles.add(title_id)

    episode_credits: list[tuple[int, str, str, str, int]] = []
    credited_episodes: set[int] = set()
    for imdb_id, expected_episode_id in episode_targets.items():
        directors, writers = crew.get(imdb_id, ([], []))
        for order, person_id in enumerate(directors, start=1):
            if person_id in people:
                episode_credits.append(
                    (expected_episode_id, person_id, people[person_id], "director", order)
                )
                credited_episodes.add(expected_episode_id)
        for order, person_id in enumerate(writers, start=1):
            if person_id in people:
                episode_credits.append(
                    (expected_episode_id, person_id, people[person_id], "writer", order)
                )
                credited_episodes.add(expected_episode_id)

    with database.connect() as conn:
        title_ids = tuple(title_id for title_id, _kind in wanted_titles.values())
        placeholders = ",".join("?" for _ in title_ids)
        conn.execute(
            f"DELETE FROM title_credits WHERE title_id IN ({placeholders})", title_ids
        )
        conn.executemany(
            """INSERT OR IGNORE INTO title_credits
               (title_id, imdb_person_id, person_name, role, billing_order)
               VALUES (?, ?, ?, ?, ?)""",
            credits,
        )
        if expected_lookup:
            expected_ids = tuple(expected_lookup.values())
            expected_placeholders = ",".join("?" for _ in expected_ids)
            conn.execute(
                f"DELETE FROM episode_credits WHERE expected_episode_id IN ({expected_placeholders})",
                expected_ids,
            )
            conn.executemany(
                """INSERT OR IGNORE INTO episode_credits
                   (expected_episode_id, imdb_person_id, person_name, role, billing_order)
                   VALUES (?, ?, ?, ?, ?)""",
                episode_credits,
            )
    return {
        "requested": len(wanted), "matched": len(found), "records": records,
        "ratings_matched": len(ratings), "ratings_records": ratings_records,
        "credits_matched": len(credited_titles), "people_matched": len(people),
        "crew_records": crew_records, "principal_records": principal_records,
        "name_records": name_records, "episode_records": episode_records,
        "episode_credits_matched": len(credited_episodes),
    }

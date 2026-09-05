from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def seed_activity(conn: sqlite3.Connection, count: int) -> None:
    conn.executemany(
        """INSERT INTO event_logs(level, category, message, detail, context_json, user_id)
           VALUES('warning', 'mie', ?, '', ?, NULL)""",
        [
            (
                f"E2E Library Health finding {index + 1}",
                json.dumps({"category": "identity", "fixture": True, "index": index + 1}),
            )
            for index in range(count)
        ],
    )


def seed_movie_suggestions(conn: sqlite3.Connection, count: int) -> None:
    rows = conn.execute(
        """SELECT id, title, year FROM titles
           WHERE kind='movie' AND tvdb_movie_id IS NULL
           ORDER BY title COLLATE NOCASE LIMIT ?""",
        (count,),
    ).fetchall()
    conn.execute("DELETE FROM movie_match_suggestions")
    for index, row in enumerate(rows):
        exact = index < max(0, len(rows) - 1)
        score = 100 if exact else 55
        candidate = {
            "id": 900000 + index,
            "tvdb_id": 900000 + index,
            "name": row["title"] if exact else "Deliberately Wrong Candidate",
            "year": row["year"] if exact else 1999,
            "image_url": "",
            "_possible_match": not exact,
            "_search_query": row["title"],
        }
        conn.execute(
            """INSERT INTO movie_match_suggestions(
                   title_id, candidate_json, confidence_score, confidence_label,
                   result_count, exact, error
               ) VALUES(?, ?, ?, ?, ?, ?, NULL)""",
            (
                row["id"],
                json.dumps(candidate),
                score,
                "Very high" if exact else "Low",
                1,
                1 if exact else 0,
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--activity", type=int, default=0)
    parser.add_argument("--movie-suggestions", type=int, default=0)
    args = parser.parse_args()

    database = Path(args.database)
    if not database.exists():
        raise SystemExit(f"InfoMancer E2E database does not exist: {database}")

    conn = sqlite3.connect(database, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        if args.activity:
            seed_activity(conn, args.activity)
        if args.movie_suggestions:
            seed_movie_suggestions(conn, args.movie_suggestions)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
from pathlib import Path

from .auth import AuthService
from .config import get_settings
from .db import Database
from .engagement import EngagementService
from .scanner import scan_root


def main() -> None:
    if os.getenv("INFOMANCER_SANDBOX") != "1":
        raise SystemExit("Sandbox seeding is disabled outside an InfoMancer sandbox.")
    settings = get_settings()
    database = Database(settings.database)
    database.initialize()
    auth = AuthService(database, settings)
    if not auth.user_count():
        user = auth.create_user(
            "sandbox", "sandbox@example.invalid", "Sandbox Librarian",
            "sandbox librarian password", role="librarian", profile_icon="library",
        )
    else:
        user = auth.get_user_by_username("sandbox")
    with database.connect() as conn:
        for path, kind, label in (
            ("/media/movies", "movie", "Sample Movies"),
            ("/media/tv", "tv", "Sample TV"),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO roots(path,kind,label) VALUES (?,?,?)",
                (path, kind, label),
            )
        roots = conn.execute("SELECT * FROM roots ORDER BY id").fetchall()
        for root in roots:
            if Path(root["path"]).is_dir():
                scan_root(conn, root)
    if user:
        engagement = EngagementService(database)
        engagement.set_tour_state(user.id, completed=True)
        engagement.complete_setup(user.id)
    print("Sample sandbox ready: username sandbox / password sandbox librarian password")


if __name__ == "__main__":
    main()

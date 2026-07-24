from __future__ import annotations

import argparse
import getpass
import sys

from .auth import AuthService, AuthenticationError
from .config import get_settings
from .db import Database


def reset_librarian(username: str) -> int:
    password = getpass.getpass("New temporary password: ")
    confirmation = getpass.getpass("Confirm temporary password: ")
    if password != confirmation:
        print(
            "The two passwords did not match. No account or session was changed.",
            file=sys.stderr,
        )
        return 2
    settings = get_settings()
    database = Database(settings.database)
    database.initialize()
    service = AuthService(database, settings)
    try:
        user = service.recover_librarian(username, password)
    except AuthenticationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        f'Recovery completed for "{user.username}". Existing sessions and setup links were revoked.'
    )
    print(
        "Sign in with the temporary password. InfoMancer will require a new password immediately."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.admin_cli",
        description="Offline recovery tools for an InfoMancer installation.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser(
        "reset-librarian",
        help="Reset a Librarian password interactively and revoke its sessions.",
    )
    reset.add_argument("username", help="The Librarian's InfoMancer username")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if arguments.command == "reset-librarian":
        return reset_librarian(arguments.username)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

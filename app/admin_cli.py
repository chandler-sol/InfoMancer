from __future__ import annotations

import argparse
import getpass
import sys
from urllib.parse import urljoin

from .auth import AuthService, AuthenticationError
from .config import get_settings
from .db import Database
from .event_log import EventLog


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


def create_recovery_link(
    username: str, base_url: str, hours: int = 1,
) -> int:
    settings = get_settings()
    database = Database(settings.database)
    database.initialize()
    service = AuthService(database, settings)
    user = service.get_user_by_username(username)
    if not user:
        print(
            f'No InfoMancer account uses the username "{username.strip()}". '
            "Check the spelling and try again.",
            file=sys.stderr,
        )
        return 2
    if not user.active:
        print(
            f'"{user.username}" is disabled. A Librarian must enable the account '
            "before a recovery link can be created.",
            file=sys.stderr,
        )
        return 2
    try:
        raw_token, expires = service.create_invitation(
            user.id, created_by=None, hours=hours,
        )
    except AuthenticationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    activation_path = f"/activate/{raw_token}"
    normalized_base = base_url.strip().rstrip("/") + "/"
    link = urljoin(normalized_base, activation_path.lstrip("/"))
    EventLog(database).write(
        "authentication",
        f'A password recovery link was created from the server for "{user.username}".',
        context={"user_id": user.id, "expires_at": expires},
    )
    print(f'Password recovery link for "{user.username}":')
    print(link)
    print(f"The link expires at {expires} and can be used only once.")
    print(
        "Treat this link like a password. After it is used, InfoMancer signs "
        "out every existing session for this account."
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
    recovery = commands.add_parser(
        "recovery-link",
        help="Create a short-lived, single-use password recovery link.",
    )
    recovery.add_argument("username", help="The local InfoMancer username")
    recovery.add_argument(
        "--base-url", default="http://127.0.0.1:8787",
        help="Public InfoMancer address used to build the link.",
    )
    recovery.add_argument(
        "--hours", type=int, default=1,
        help="Link lifetime from 1 to 168 hours (default: 1).",
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if arguments.command == "reset-librarian":
        return reset_librarian(arguments.username)
    if arguments.command == "recovery-link":
        return create_recovery_link(
            arguments.username, arguments.base_url, arguments.hours,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

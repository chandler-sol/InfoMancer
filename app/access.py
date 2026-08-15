from __future__ import annotations

from fastapi import Request


class LibrarianAccessRequired(RuntimeError):
    """Raised when a route explicitly requires Librarian privileges."""


def require_librarian(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if not user or not user.is_librarian:
        raise LibrarianAccessRequired

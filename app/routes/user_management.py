from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    auth_service = ctx.live("auth_service")
    db = ctx.live("db")
    redirect = ctx.live("redirect")
    record_event = ctx.live("record_event")
    settings = ctx.live("settings")

    @router.post(
        "/admin/users/{user_id}/delete",
        dependencies=[Depends(require_librarian)],
    )
    def delete_managed_user(request: Request, user_id: int):
        actor = getattr(request.state, "user", None)
        actor_id = int(getattr(actor, "id", 0) or 0)
        if actor_id <= 0:
            return redirect("/admin/users", "Account deletion requires a signed-in Librarian account.")
        if user_id == actor_id:
            return redirect("/admin/users", "You cannot delete the account you are currently using.")

        target = auth_service.get_user(user_id)
        if not target:
            return redirect("/admin/users", "That account no longer exists.")
        if (
            target.role == "librarian"
            and target.active
            and auth_service.librarian_count(excluding=user_id) == 0
        ):
            return redirect("/admin/users", "InfoMancer must retain at least one active Librarian.")

        # User-owned records use ON DELETE CASCADE while shared audit/config rows use
        # ON DELETE SET NULL. Keep deletion atomic so there is never a half-removed
        # account if SQLite rejects an unexpected relationship.
        with db.connect() as conn:
            result = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        if result.rowcount != 1:
            return redirect("/admin/users", "That account changed before it could be deleted.")

        # Custom avatars live beside, rather than inside, SQLite. Remove the orphaned
        # sanitized PNG after the account transaction succeeds.
        avatar_path = Path(settings.database).resolve().parent / "profile-avatars" / f"{user_id}.png"
        try:
            avatar_path.unlink(missing_ok=True)
        except OSError:
            # Account deletion succeeded; a stale avatar is harmless and can be
            # cleaned manually rather than turning a completed delete into an error.
            pass

        record_event(
            "authentication",
            f"Deleted user account {target.username}.",
            level="warning",
            detail=f"Removed {target.display_name} (@{target.username}) from InfoMancer.",
            context={
                "operation": "delete-user",
                "deleted_user_id": user_id,
                "deleted_username": target.username,
                "deleted_role": target.role,
            },
            user_id=actor_id,
        )
        return redirect("/admin/users", f"{target.display_name} was deleted.")

    return router, {"delete_managed_user": delete_managed_user}

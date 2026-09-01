from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Request

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    """Own collection deletion before the broader Collections router is registered.

    0.8.1 keeps a complete catalog snapshot for each collection deletion so the
    visible Undo control can restore both manual and Smart Collections exactly.
    Media files are never involved in this workflow.
    """

    router = APIRouter()
    db = ctx.live("db")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @librarian_post("/collections/{collection_id}/delete")
    def delete_collection_with_undo(request: Request, collection_id: int):
        actor = request.state.user.id if request.state.user.id > 0 else None
        with db.connect() as conn:
            collection = conn.execute(
                "SELECT * FROM collections WHERE id=?",
                (collection_id,),
            ).fetchone()
            if not collection:
                return redirect("/collections", "That collection no longer exists.")

            title_rows = conn.execute(
                """SELECT title_id,position,added_at FROM collection_titles
                   WHERE collection_id=? ORDER BY position,title_id""",
                (collection_id,),
            ).fetchall()
            episode_rows = conn.execute(
                """SELECT expected_episode_id,position,added_at FROM collection_episodes
                   WHERE collection_id=? ORDER BY position,expected_episode_id""",
                (collection_id,),
            ).fetchall()
            payload = {
                "collection": dict(collection),
                "titles": [dict(row) for row in title_rows],
                "episodes": [dict(row) for row in episode_rows],
            }
            operation_id = int(conn.execute(
                """INSERT INTO operation_history(
                     operation_type,status,summary,detail,actor_user_id,undo_payload
                   ) VALUES ('collection_delete','completed',?,?,?,?)""",
                (
                    f'Collection deleted: {collection["name"]}'[:500],
                    (
                        f"Saved {len(title_rows):,} title membership(s) and "
                        f"{len(episode_rows):,} episode membership(s) for safe Undo."
                    )[:2000],
                    actor,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            ).lastrowid)
            conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))

        # Deliberately keep collection artwork on disk while the database Undo is
        # available. Deleting the file here would make an otherwise successful Undo
        # restore a visually incomplete collection.
        record_event(
            "library",
            f'Collection "{collection["name"]}" deleted with an undo snapshot.',
            user_id=request.state.user.id,
            context={
                "operation": "collection_delete",
                "collection_id": collection_id,
                "operation_id": operation_id,
            },
        )
        return redirect(
            f"/collections?undo_collection={operation_id}",
            f'Collection "{collection["name"]}" deleted. No movies, TV series, episodes, or media files were removed.',
        )

    @librarian_post("/collections/deletions/{operation_id}/undo")
    def undo_collection_delete(request: Request, operation_id: int):
        actor = request.state.user.id if request.state.user.id > 0 else None
        try:
            with db.connect() as conn:
                operation = conn.execute(
                    """SELECT id,status,operation_type,undo_payload
                       FROM operation_history WHERE id=?""",
                    (operation_id,),
                ).fetchone()
                if not operation or operation["operation_type"] != "collection_delete":
                    return redirect("/collections", "That collection Undo is no longer available.")
                if operation["status"] == "undone":
                    return redirect("/collections", "That collection deletion has already been undone.")
                if operation["status"] != "completed":
                    return redirect("/collections", "That collection Undo is already being changed. Refresh and try again.")

                try:
                    payload = json.loads(operation["undo_payload"] or "{}")
                    snapshot = payload["collection"]
                    title_rows = payload.get("titles") or []
                    episode_rows = payload.get("episodes") or []
                    collection_id = int(snapshot["id"])
                    name = str(snapshot["name"])
                except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                    return redirect(
                        "/collections",
                        "Undo stopped because the saved collection snapshot is invalid. Nothing was changed.",
                    )

                collision = conn.execute(
                    "SELECT id FROM collections WHERE id=? OR name=? COLLATE NOCASE LIMIT 1",
                    (collection_id, name),
                ).fetchone()
                if collision:
                    return redirect(
                        "/collections",
                        "Undo stopped because a collection now occupies the original id or name. Nothing was changed.",
                    )

                title_ids = [int(row["title_id"]) for row in title_rows]
                episode_ids = [int(row["expected_episode_id"]) for row in episode_rows]
                if title_ids:
                    placeholders = ",".join("?" for _ in title_ids)
                    existing = int(conn.execute(
                        f"SELECT COUNT(*) count FROM titles WHERE id IN ({placeholders})",
                        tuple(title_ids),
                    ).fetchone()["count"])
                    if existing != len(set(title_ids)):
                        return redirect(
                            "/collections",
                            "Undo stopped because a title from the deleted collection no longer exists. Nothing was changed.",
                        )
                if episode_ids:
                    placeholders = ",".join("?" for _ in episode_ids)
                    existing = int(conn.execute(
                        f"SELECT COUNT(*) count FROM expected_episodes WHERE id IN ({placeholders})",
                        tuple(episode_ids),
                    ).fetchone()["count"])
                    if existing != len(set(episode_ids)):
                        return redirect(
                            "/collections",
                            "Undo stopped because an episode from the deleted collection no longer exists. Nothing was changed.",
                        )

                claimed = conn.execute(
                    """UPDATE operation_history SET status='undoing',undo_error=''
                       WHERE id=? AND status='completed'""",
                    (operation_id,),
                )
                if not claimed.rowcount:
                    return redirect(
                        "/collections",
                        "That collection Undo is already being changed. Refresh and try again.",
                    )

                created_by = snapshot.get("created_by")
                if created_by:
                    creator = conn.execute("SELECT id FROM users WHERE id=?", (created_by,)).fetchone()
                    if not creator:
                        created_by = None

                conn.execute(
                    """INSERT INTO collections(
                         id,name,description,artwork_filename,created_by,created_at,updated_at,
                         collection_type,filter_json
                       ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        collection_id,
                        name,
                        str(snapshot.get("description") or ""),
                        snapshot.get("artwork_filename"),
                        created_by,
                        snapshot.get("created_at") or "",
                        snapshot.get("updated_at") or snapshot.get("created_at") or "",
                        str(snapshot.get("collection_type") or "manual"),
                        str(snapshot.get("filter_json") or "{}"),
                    ),
                )
                conn.executemany(
                    """INSERT INTO collection_titles(collection_id,title_id,position,added_at)
                       VALUES (?,?,?,?)""",
                    [
                        (
                            collection_id,
                            int(row["title_id"]),
                            int(row.get("position") or 0),
                            str(row.get("added_at") or ""),
                        )
                        for row in title_rows
                    ],
                )
                conn.executemany(
                    """INSERT INTO collection_episodes(
                         collection_id,expected_episode_id,position,added_at
                       ) VALUES (?,?,?,?)""",
                    [
                        (
                            collection_id,
                            int(row["expected_episode_id"]),
                            int(row.get("position") or 0),
                            str(row.get("added_at") or ""),
                        )
                        for row in episode_rows
                    ],
                )
                conn.execute(
                    """UPDATE operation_history
                       SET status='undone',undone_at=CURRENT_TIMESTAMP,undone_by=?,undo_error=''
                       WHERE id=? AND status='undoing'""",
                    (actor, operation_id),
                )
        except sqlite3.IntegrityError:
            return redirect(
                "/collections",
                "Undo stopped because the collection can no longer be restored without a catalog conflict. Nothing was changed.",
            )

        record_event(
            "library",
            f'Collection "{name}" restored by Undo.',
            user_id=request.state.user.id,
            context={
                "operation": "collection_delete_undo",
                "collection_id": collection_id,
                "operation_id": operation_id,
            },
        )
        return redirect(
            f"/collections/{collection_id}",
            f'Collection "{name}" restored. Its saved order and Smart Collection rules were preserved.',
        )

    return router, {
        "delete_collection_with_undo": delete_collection_with_undo,
        "undo_collection_delete": undo_collection_delete,
    }

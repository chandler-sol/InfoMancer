from __future__ import annotations

import ntpath
import posixpath
import re
from pathlib import Path

from fastapi import APIRouter, Depends

from ..access import require_librarian
from ..operation_history import OperationHistoryError, OperationHistoryService
from .context import RouteContext


def _path_module(value: str):
    """Choose lexical path rules without touching the filesystem."""
    return ntpath if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", value) else posixpath


def network_safe_require_inside(path: Path, parent: Path) -> None:
    """Reject escaped undo paths without requiring network realpath access.

    Windows ``Path.resolve(strict=False)`` can still call into the mapped-drive
    provider. On shares that Windows exposes to the logged-in desktop but refuses
    to resolve as an unauthenticated network path, that can raise WinError 1272
    before Undo reaches its normal existence and rename checks. First prove lexical
    containment, then retain the stronger resolved-path check whenever the provider
    permits it. A provider error only skips the optional realpath hardening; it does
    not skip the catalog, collision, parent-directory, or rename checks performed by
    OperationHistoryService itself.
    """
    path_text = str(path)
    parent_text = str(parent)
    path_ops = _path_module(path_text)
    parent_ops = _path_module(parent_text)
    if path_ops is not parent_ops:
        raise OperationHistoryError(
            "Undo stopped because the recorded path is outside its configured source. Nothing was changed."
        )

    normalized_path = path_ops.normcase(path_ops.abspath(path_ops.normpath(path_text)))
    normalized_parent = parent_ops.normcase(parent_ops.abspath(parent_ops.normpath(parent_text)))
    try:
        common = path_ops.commonpath((normalized_path, normalized_parent))
    except ValueError as exc:
        raise OperationHistoryError(
            "Undo stopped because the recorded path is outside its configured source. Nothing was changed."
        ) from exc
    if common != normalized_parent:
        raise OperationHistoryError(
            "Undo stopped because the recorded path is outside its configured source. Nothing was changed."
        )

    try:
        resolved_path = path.resolve(strict=False)
        resolved_parent = parent.resolve(strict=False)
    except OSError:
        # The mapped/network source refused realpath resolution. Lexical containment
        # has already been proven; the actual operation still has all normal guards.
        return
    try:
        resolved_path.relative_to(resolved_parent)
    except ValueError as exc:
        raise OperationHistoryError(
            "Undo stopped because the recorded path is outside its configured source. Nothing was changed."
        ) from exc


def build_router(ctx: RouteContext):
    router = APIRouter()
    Form = ctx.get("Form")
    HTMLResponse = ctx.get("HTMLResponse")
    Request = ctx.get("Request")
    db = ctx.live("db")
    next_collection_position = ctx.live("next_collection_position")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    safe_next = ctx.live("safe_next")
    smart_filter_form = ctx.live("smart_filter_form")
    encode_filters = ctx.live("encode_filters")
    sqlite3 = ctx.live("sqlite3")
    templates = ctx.live("templates")

    # Operations is registered after this release-stabilization router. Patch the
    # class before that router constructs its service so every undo uses the same
    # mapped-drive-safe containment policy.
    OperationHistoryService._require_inside = staticmethod(network_safe_require_inside)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @librarian_post("/titles/collections-bulk", response_class=HTMLResponse)
    def bulk_collection_picker(
        request: Request,
        selected: list[int] = Form(default=[]),
        return_to: str = Form("/library"),
    ):
        title_ids = list(dict.fromkeys(int(value) for value in selected if int(value) > 0))[:1000]
        if len(title_ids) < 2:
            return redirect(safe_next(return_to or "/library"), "Select at least two titles first.")
        placeholders = ",".join("?" for _ in title_ids)
        with db.connect() as conn:
            titles = conn.execute(
                f"""SELECT id,COALESCE(NULLIF(metadata_title,''),title) display_title
                    FROM titles WHERE id IN ({placeholders})
                    ORDER BY display_title COLLATE NOCASE""",
                tuple(title_ids),
            ).fetchall()
            collections = conn.execute(
                """SELECT id,name FROM collections
                   WHERE collection_type='manual' ORDER BY name COLLATE NOCASE"""
            ).fetchall()
        valid_ids = {int(row["id"]) for row in titles}
        title_ids = [title_id for title_id in title_ids if title_id in valid_ids]
        if len(title_ids) < 2:
            return redirect(safe_next(return_to or "/library"), "Some selected titles are no longer in the Library.")
        return templates.TemplateResponse(request, "bulk_title_collections.html", {
            "titles": titles,
            "selected": title_ids,
            "collections": collections,
            "return_to": safe_next(return_to or "/library"),
            "message": "",
        })

    @librarian_post("/titles/collections-bulk/apply")
    def bulk_collection_apply(
        request: Request,
        selected: list[int] = Form(default=[]),
        collection_ids: list[int] = Form(default=[]),
        new_collection_name: str = Form(""),
        return_to: str = Form("/library"),
    ):
        title_ids = list(dict.fromkeys(int(value) for value in selected if int(value) > 0))[:1000]
        chosen_ids = set(int(value) for value in collection_ids if int(value) > 0)
        cleaned_new_name = " ".join(new_collection_name.split())[:80]
        destination = safe_next(return_to or "/library")
        if not title_ids:
            return redirect(destination, "No selected titles were available to add.")
        if not chosen_ids and not cleaned_new_name:
            return redirect(destination, "Choose a collection or enter a name for a new one.")

        added = 0
        collection_names: list[str] = []
        try:
            with db.connect() as conn:
                placeholders = ",".join("?" for _ in title_ids)
                valid_titles = {
                    int(row["id"])
                    for row in conn.execute(
                        f"SELECT id FROM titles WHERE id IN ({placeholders})",
                        tuple(title_ids),
                    ).fetchall()
                }
                title_ids = [title_id for title_id in title_ids if title_id in valid_titles]

                allowed = {
                    int(row["id"]): str(row["name"])
                    for row in conn.execute(
                        "SELECT id,name FROM collections WHERE collection_type='manual'"
                    ).fetchall()
                }
                chosen_ids &= set(allowed)

                if cleaned_new_name:
                    cursor = conn.execute(
                        """INSERT INTO collections(name,description,created_by,collection_type)
                           VALUES (?,?,?,'manual')""",
                        (
                            cleaned_new_name,
                            "",
                            request.state.user.id if request.state.user.id > 0 else None,
                        ),
                    )
                    new_id = int(cursor.lastrowid)
                    allowed[new_id] = cleaned_new_name
                    chosen_ids.add(new_id)

                for collection_id in sorted(chosen_ids):
                    collection_names.append(allowed[collection_id])
                    for title_id in title_ids:
                        cursor = conn.execute(
                            """INSERT OR IGNORE INTO collection_titles(collection_id,title_id,position)
                               VALUES (?,?,?)""",
                            (collection_id, title_id, next_collection_position(conn, collection_id)),
                        )
                        added += int(bool(cursor.rowcount))
        except sqlite3.IntegrityError:
            return redirect(
                destination,
                f'A collection named "{cleaned_new_name}" already exists. Choose it from the collection list instead.',
            )

        if not chosen_ids:
            return redirect(destination, "No valid manual collection was selected.")
        message = (
            f"Added {added:,} collection membership{'s' if added != 1 else ''} across "
            f"{len(chosen_ids):,} collection{'s' if len(chosen_ids) != 1 else ''}."
            if added else
            "Those titles were already in the selected collection or collections."
        )
        record_event(
            "library",
            message,
            user_id=request.state.user.id,
            context={
                "operation": "bulk_add_to_collection",
                "title_count": len(title_ids),
                "collection_ids": sorted(chosen_ids),
                "collection_names": collection_names,
                "memberships_added": added,
            },
        )
        return redirect(destination, message)

    @librarian_post("/collections/{collection_id}/smart/edit")
    async def edit_smart_collection(request: Request, collection_id: int):
        form = await request.form()
        cleaned = " ".join(str(form.get("name") or "").split())[:80]
        description = str(form.get("description") or "").strip()[:1000]
        if not cleaned:
            return redirect(f"/collections/{collection_id}", "Enter a Smart Collection name.")
        try:
            filters = smart_filter_form(form)
        except ValueError as exc:
            return redirect(
                f"/collections/{collection_id}",
                f"Smart Collection was not changed. {exc}",
            )
        try:
            with db.connect() as conn:
                current = conn.execute(
                    "SELECT collection_type FROM collections WHERE id=?",
                    (collection_id,),
                ).fetchone()
                if not current or current["collection_type"] != "smart":
                    return redirect("/collections", "That Smart Collection no longer exists.")
                conn.execute(
                    """UPDATE collections
                       SET name=?,description=?,filter_json=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND collection_type='smart'""",
                    (cleaned, description, encode_filters(filters), collection_id),
                )
        except sqlite3.IntegrityError:
            return redirect(
                f"/collections/{collection_id}",
                f'The name "{cleaned}" is already used by another collection.',
            )
        record_event(
            "library",
            f'Smart Collection "{cleaned}" rules updated.',
            user_id=request.state.user.id,
            context={"collection_id": collection_id, "operation": "smart_collection_edit"},
        )
        return redirect(
            f"/collections/{collection_id}",
            f'Smart Collection "{cleaned}" updated. Matching titles were recalculated.',
        )

    return router, {
        "bulk_collection_picker": bulk_collection_picker,
        "bulk_collection_apply": bulk_collection_apply,
        "edit_smart_collection": edit_smart_collection,
        "network_safe_require_inside": network_safe_require_inside,
    }

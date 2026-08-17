from fastapi import APIRouter

from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    EngagementError = ctx.get("EngagementError")
    Form = ctx.get("Form")
    HTMLResponse = ctx.get("HTMLResponse")
    JSONResponse = ctx.get("JSONResponse")
    Request = ctx.get("Request")
    announcement_page_context = ctx.live("announcement_page_context")
    engagement = ctx.live("engagement")
    event_log = ctx.live("event_log")
    redirect = ctx.live("redirect")
    templates = ctx.live("templates")

    @router.post("/engagement/announcements/{announcement_id}/seen")
    def mark_announcement_seen(request: Request, announcement_id: int):
        try:
            engagement.mark_seen(announcement_id, request.state.user.id)
        except EngagementError:
            return JSONResponse(
                {"detail": "That announcement is no longer available. Refresh and try again."},
                status_code=404,
            )
        return JSONResponse({"saved": True})

    @router.get("/announcements", response_class=HTMLResponse)
    def announcements_page(request: Request):
        user = request.state.user
        rows = engagement.list_for_user(user.id, user.role)
        for row in rows:
            if row["due_now"]:
                engagement.mark_seen(row["id"], user.id)
        return templates.TemplateResponse(
            request, "announcements.html", announcement_page_context(request)
        )

    @router.get("/help", response_class=HTMLResponse)
    def help_page(request: Request):
        return templates.TemplateResponse(request, "help.html", {
            "message": request.query_params.get("message", ""),
        })

    @router.get("/about", response_class=HTMLResponse)
    def about_page(request: Request):
        return templates.TemplateResponse(request, "about.html", {
            "message": request.query_params.get("message", ""),
        })

    @router.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request, unread: str = ""):
        items = event_log.activity(
            request.state.user.id, unread_only=unread == "1", limit=150,
        )
        return templates.TemplateResponse(request, "activity.html", {
            "items": items, "unread_only": unread == "1",
            "message": request.query_params.get("message", ""),
        })

    @router.post("/activity/read")
    def mark_activity_read(
        request: Request, event_ids: list[int] = Form(default=[]), all_events: str = Form(""),
    ):
        # Read state is account-local and EventLog intersects requested IDs with
        # notifications visible to this user, so members can safely manage their own inbox.
        changed = event_log.mark_read(
            request.state.user.id, None if all_events == "1" else event_ids,
        )
        return redirect("/activity", f"Marked {changed:,} notification{'s' if changed != 1 else ''} as read.")

    @router.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return router, {
        "mark_announcement_seen": mark_announcement_seen,
        "announcements_page": announcements_page,
        "help_page": help_page,
        "about_page": about_page,
        "activity_page": activity_page,
        "mark_activity_read": mark_activity_read,
        "health": health,
    }

from fastapi import APIRouter, Depends

from ..access import require_librarian
from ..review_queue import ReviewQueue
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    DuplicateTrashError = ctx.get("DuplicateTrashError")
    Form = ctx.get("Form")
    HTMLResponse = ctx.get("HTMLResponse")
    HTTPException = ctx.get("HTTPException")
    MIE_CATEGORIES = ctx.get("MIE_CATEGORIES")
    MIE_SEVERITIES = ctx.get("MIE_SEVERITIES")
    Request = ctx.get("Request")
    TVDBError = ctx.get("TVDBError")
    analyze_library_health_with_activity = ctx.live("analyze_library_health_with_activity")
    app_settings = ctx.live("app_settings")
    check_source_health = ctx.live("check_source_health")
    db = ctx.live("db")
    duplicate_trash = ctx.live("duplicate_trash")
    duplicate_verify_job = ctx.live("duplicate_verify_job")
    duplicate_verify_lock = ctx.live("duplicate_verify_lock")
    duplicates = ctx.live("duplicates")
    json = ctx.live("json")
    maybe_start_trash_cleanup = ctx.live("maybe_start_trash_cleanup")
    media_info_job = ctx.live("media_info_job")
    media_info_lock = ctx.live("media_info_lock")
    mie = ctx.live("mie")
    movie_match_job = ctx.live("movie_match_job")
    movie_match_lock = ctx.live("movie_match_lock")
    re = ctx.live("re")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    remediation_context = ctx.live("remediation_context")
    run_media_inspection = ctx.live("run_media_inspection")
    run_movie_match_analysis = ctx.live("run_movie_match_analysis")
    run_scan = ctx.live("run_scan")
    run_title_scan = ctx.live("run_title_scan")
    run_tv_match_analysis = ctx.live("run_tv_match_analysis")
    scan_jobs = ctx.live("scan_jobs")
    scan_lock = ctx.live("scan_lock")
    sqlite3 = ctx.live("sqlite3")
    store_movie_match = ctx.live("store_movie_match")
    store_tv_match = ctx.live("store_tv_match")
    templates = ctx.live("templates")
    threading = ctx.live("threading")
    title_scan_jobs = ctx.live("title_scan_jobs")
    title_scan_lock = ctx.live("title_scan_lock")
    trash_retention_days = ctx.live("trash_retention_days")
    tv_match_job = ctx.live("tv_match_job")
    tv_match_lock = ctx.live("tv_match_lock")
    review_queue = ReviewQueue(db, mie, duplicates)

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @router.get("/review", response_class=HTMLResponse)
    def review_workspace(
        request: Request, status: str = "active", severity: str = "",
        bucket: str = "", q: str = "", sort: str = "priority",
    ):
        queue = review_queue.view(
            status=status, severity=severity, bucket=bucket, q=q, sort=sort,
            include_librarian=request.state.user.is_librarian,
        )
        response = templates.TemplateResponse(request, "review.html", {
            "queue": queue,
            "filters": queue["filters"],
            "message": request.query_params.get("message", ""),
        })
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/review/items/{source}/{item_id}", response_class=HTMLResponse)
    def review_workspace_item(request: Request, source: str, item_id: str):
        item = review_queue.get_item(
            source, item_id, include_librarian=request.state.user.is_librarian,
        )
        if item is None:
            raise HTTPException(404, "That review item is no longer available.")
        response = templates.TemplateResponse(
            request, "_review_drawer.html", {"item": item},
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @librarian_post("/review/analyze")
    def analyze_review_workspace(request: Request):
        try:
            finding_count = analyze_library_health_with_activity()
        except sqlite3.Error as exc:
            record_event(
                "mie", "Review analysis could not be completed.", level="error",
                detail=str(exc), context={"operation": "review-analysis"},
                user_id=request.state.user.id,
            )
            return redirect(
                "/review",
                "Review could not refresh because the findings could not be saved. No media files were changed.",
            )
        message = (
            f"Review refreshed with {finding_count} current finding"
            f"{'s' if finding_count != 1 else ''}. No media files were changed."
        )
        record_event(
            "mie", message, context={"finding_count": finding_count, "source": "review-workspace"},
            user_id=request.state.user.id,
        )
        return redirect("/review", message)

    @librarian_post("/api/review/findings/{finding_id}/dismiss")
    def workspace_dismiss_review_finding(
        request: Request, finding_id: int, reason: str = Form("other"),
        scope: str = Form("finding"), note: str = Form(""),
    ) -> dict:
        try:
            dismissed = mie.dismiss(
                finding_id, request.state.user.id, reason=reason, scope=scope, note=note,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not dismissed:
            raise HTTPException(409, "That finding is no longer active.")
        message = "Feedback saved and the finding was removed from active Review."
        record_event(
            "mie", f"Review finding {finding_id} was dismissed.",
            context={"finding_id": finding_id, "reason": reason, "scope": scope, "source": "review-workspace"},
            user_id=request.state.user.id,
        )
        active = review_queue.view(include_librarian=True)
        counts = dict(active["counts"])
        counts["buckets"] = active["bucket_counts"]
        return {"ok": True, "message": message, "remove_key": f"finding:{finding_id}", "counts": counts}

    @librarian_post("/api/review/findings/{finding_id}/restore")
    def workspace_restore_review_finding(request: Request, finding_id: int) -> dict:
        if not mie.restore(finding_id):
            raise HTTPException(409, "That finding is no longer dismissed.")
        message = "Finding restored to active Review."
        record_event(
            "mie", f"Review finding {finding_id} was restored.",
            context={"finding_id": finding_id, "source": "review-workspace"},
            user_id=request.state.user.id,
        )
        dismissed = review_queue.view(status="dismissed", include_librarian=True)
        counts = dict(dismissed["counts"])
        counts["buckets"] = dismissed["bucket_counts"]
        return {"ok": True, "message": message, "remove_key": f"finding:{finding_id}", "counts": counts}

    @librarian_post("/api/review/duplicates/{file_a_id}/{file_b_id}/decision")
    def workspace_duplicate_decision(
        request: Request, file_a_id: int, file_b_id: int,
        decision: str = Form(...),
    ) -> dict:
        if decision not in {"ignored", "not_duplicate"}:
            raise HTTPException(400, "Choose Ignore for now or Intentional alternative.")
        if not duplicates.decide(file_a_id, file_b_id, decision, request.state.user.id):
            raise HTTPException(409, "That duplicate pair is no longer available.")
        message = (
            "Duplicate pair ignored for now. It can return if either file changes."
            if decision == "ignored" else
            "Files marked as intentional alternatives. Neither file was changed."
        )
        record_event(
            "duplicates", message,
            context={"file_a_id": file_a_id, "file_b_id": file_b_id, "decision": decision, "source": "review-workspace"},
            user_id=request.state.user.id,
        )
        active = review_queue.view(include_librarian=True)
        counts = dict(active["counts"])
        counts["buckets"] = active["bucket_counts"]
        left, right = sorted((file_a_id, file_b_id))
        return {"ok": True, "message": message, "remove_key": f"duplicate:{left}:{right}", "counts": counts}

    @router.get("/library-health", response_class=HTMLResponse)
    def library_health(
        request: Request, status: str = "active", severity: str = "",
        category: str = "",
    ):
        status = status if status in {"active", "dismissed", "resolved"} else "active"
        severity = severity if severity in MIE_SEVERITIES else ""
        category = category if category in MIE_CATEGORIES else ""
        summary = mie.summary()
        if not summary["last_analyzed_at"]:
            try:
                mie.analyze()
                summary = mie.summary()
            except sqlite3.Error as exc:
                record_event(
                    "mie", "Library Health analysis could not start.",
                    level="error", detail=str(exc),
                    context={"operation": "initial-analysis"},
                    user_id=request.state.user.id,
                )
                return templates.TemplateResponse(
                    request, "library_health.html", {
                        "summary": summary, "findings": [], "status": status,
                        "severity": severity, "category": category,
                        "categories": sorted(MIE_CATEGORIES),
                        "severities": ["critical", "warning", "information"],
                        "quality_profiles": mie.quality_profiles(),
                        "calibration": mie.calibration(),
                        "category_scores": mie.category_scores(),
                        "analysis_history": mie.analysis_history(),
                        "feedback_rules": mie.feedback(),
                        "duplicate_impact": duplicate_trash.impact(),
                        "message": "",
                        "error": (
                            "InfoMancer could not analyze the catalog because its "
                            "findings could not be saved. No media files were changed. "
                            "Try again; if it continues, open Logs for the technical details."
                        ),
                    }, status_code=500,
                )
        return templates.TemplateResponse(request, "library_health.html", {
            "summary": summary,
            "findings": mie.findings(
                status=status, severity=severity, category=category,
            ),
            "status": status, "severity": severity, "category": category,
            "categories": sorted(MIE_CATEGORIES),
            "severities": ["critical", "warning", "information"],
            "quality_profiles": mie.quality_profiles(),
            "calibration": mie.calibration(),
            "category_scores": mie.category_scores(),
            "analysis_history": mie.analysis_history(),
            "feedback_rules": mie.feedback(),
            "duplicate_impact": duplicate_trash.impact(),
            "message": request.query_params.get("message", ""),
            "error": "",
        })

    @router.get("/storage-intelligence", response_class=HTMLResponse)
    def storage_intelligence(request: Request):
        return templates.TemplateResponse(request, "storage_intelligence.html", {
            "report": mie.storage_report(),
            "duplicate_impact": duplicate_trash.impact(),
        })

    @router.get("/titles/{title_id}/identity", response_class=HTMLResponse)
    def title_identity(request: Request, title_id: int):
        report = mie.identity_report(title_id)
        if report is None:
            raise HTTPException(404, "That library title no longer exists.")
        return templates.TemplateResponse(request, "identity_report.html", {"report": report})

    @librarian_post("/library-health/analyze")
    def analyze_library_health(request: Request):
        try:
            finding_count = analyze_library_health_with_activity()
        except sqlite3.Error as exc:
            record_event(
                "mie", "Library Health analysis could not be completed.",
                level="error", detail=str(exc),
                context={"operation": "analysis"},
                user_id=request.state.user.id,
            )
            return redirect(
                "/library-health",
                "Library Health could not refresh because the findings could not be "
                "saved. No media files were changed. Try again, then check Logs if "
                "the problem continues.",
            )
        record_event(
            "mie",
            f"Library Health analysis completed with {finding_count} current findings.",
            context={"finding_count": finding_count},
            user_id=request.state.user.id,
        )
        return redirect(
            "/library-health",
            f"Library Health refreshed. InfoMancer found {finding_count} current "
            f"issue{'s' if finding_count != 1 else ''}. No media files were changed.",
        )

    @router.get("/library-health/findings/{finding_id}/remediate", response_class=HTMLResponse)
    def preview_library_health_remediation(request: Request, finding_id: int):
        context = remediation_context(finding_id)
        if not context or not context["actions"]:
            return redirect(
                "/library-health",
                "That finding has no automatic fix. Review the affected media and its recommended next step.",
            )
        return templates.TemplateResponse(request, "library_health_remediation.html", {
            **context, "message": request.query_params.get("message", ""),
        })

    @librarian_post("/library-health/findings/{finding_id}/remediate")
    def apply_library_health_remediation(
        request: Request, finding_id: int, action: str = Form(...),
        confirm: str = Form(""),
    ):
        context = remediation_context(finding_id)
        allowed = {item["key"]: item for item in (context or {}).get("actions", [])}
        selected = allowed.get(action)
        if not context or not selected:
            return redirect("/library-health", "That remediation is no longer available. Nothing changed.")
        if confirm.strip().upper() != selected["confirm"]:
            return redirect(
                f"/library-health/findings/{finding_id}/remediate",
                f"Confirmation did not match {selected['confirm']}. Nothing changed.",
            )
        root_id = context["finding"].get("root_id")
        if not root_id:
            return redirect("/library-health", "That finding is no longer tied to a source. Nothing changed.")
        if action == "check":
            result = check_source_health(int(root_id))
            mie.analyze()
            record_event(
                "source-guard", f"Source connection check completed: {result['status']}.",
                level="warning" if result["status"] != "healthy" else "info",
                context={"root_id": root_id, **result}, user_id=request.state.user.id,
            )
            messages = {
                "healthy": "Connection confirmed. The source is available; no catalog or media files were changed.",
                "degraded": "The source opened but still appears incomplete. Source Guard continues protecting the existing catalog.",
                "offline": "The source is still unavailable. Source Guard preserved the existing catalog; check the NAS, mount, and permissions.",
            }
            return redirect("/library-health", messages[result["status"]])
        if action in {"rescan_title", "inspect_source", "inspect_file"}:
            source = check_source_health(int(root_id))
            if source["status"] != "healthy":
                mie.analyze()
                return redirect(
                    "/library-health",
                    "The action was not started because Source Guard cannot confirm a complete source connection. Restore the source and run a guarded source scan first.",
                )
        if action == "rescan_title":
            title_id = int(context["finding"]["title_id"])
            with title_scan_lock:
                if title_scan_jobs.get(title_id, {}).get("status") in {"starting", "running"}:
                    return redirect("/library-health", "That series is already scanning. Nothing else was started.")
                title_scan_jobs[title_id] = {"status": "starting", "files": 0}
            threading.Thread(target=run_title_scan, args=(title_id,), daemon=True).start()
            return redirect(
                "/library-health",
                "Guarded series rescan started. Progress is shown in the task panel. Media files will not be changed.",
            )
        if action in {"inspect_source", "inspect_file"}:
            with db.connect() as conn:
                if action == "inspect_file":
                    file_ids = [int(context["finding"]["file_id"])]
                else:
                    file_ids = [int(row["id"]) for row in conn.execute(
                        """SELECT f.id FROM files f JOIN titles t ON t.id=f.title_id
                           WHERE t.root_id=? AND (f.media_info_at IS NULL OR
                             COALESCE(f.media_info_error,'')!='') ORDER BY f.id""",
                        (root_id,),
                    )]
            if not file_ids:
                mie.analyze()
                return redirect("/library-health", "Every affected file already has current media details. Nothing was started.")
            with media_info_lock:
                if media_info_job.get("status") in {"starting", "running"}:
                    return redirect("/library-health", "Media inspection is already running. Nothing else was started.")
                media_info_job.clear()
                media_info_job.update({"status": "starting", "processed": 0, "total": len(file_ids)})
            threading.Thread(target=run_media_inspection, args=(file_ids,), daemon=True).start()
            return redirect(
                "/library-health",
                f"Media inspection started for {len(file_ids):,} file{'s' if len(file_ids) != 1 else ''}. Progress is shown in the task panel; media files will not be changed.",
            )
        with scan_lock:
            if scan_jobs.get(int(root_id), {}).get("status") in {"starting", "running"}:
                return redirect("/library-health", "That source is already scanning. Nothing else was started.")
            scan_jobs[int(root_id)] = {"status": "starting", "files": 0, "titles": 0}
        threading.Thread(
            target=run_scan, args=(int(root_id),),
            kwargs={"force_cleanup": action == "reconcile"}, daemon=True,
        ).start()
        label = "reconciliation" if action == "reconcile" else "guarded scan"
        record_event(
            "source-guard", f"Source {label} was confirmed and started.",
            context={"root_id": root_id, "action": action}, user_id=request.state.user.id,
        )
        return redirect(
            "/library-health",
            f"Source {label} started. Progress is shown in the task panel. Media files will not be changed.",
        )

    @librarian_post("/library-health/remediate-batch")
    def batch_library_health_remediation(
        request: Request, findings: list[str] = Form(default=[]),
        action: str = Form("check_sources"), confirm: str = Form(""),
    ):
        if action != "check_sources" or confirm.strip().upper() != "CHECK":
            return redirect("/library-health", "Batch source check was not confirmed. Nothing changed.")
        ids = [int(value) for value in dict.fromkeys(findings) if value.isdigit()][:100]
        if not ids:
            return redirect("/library-health", "Select at least one source finding first.")
        placeholders = ",".join("?" for _ in ids)
        with db.connect() as conn:
            roots = conn.execute(
                f"""SELECT DISTINCT root_id FROM mie_findings
                     WHERE id IN ({placeholders}) AND status='active'
                       AND rule_key IN ('source-offline','source-degraded')
                       AND root_id IS NOT NULL""", ids,
            ).fetchall()
        results = [check_source_health(int(row["root_id"])) for row in roots]
        mie.analyze()
        healthy = sum(result["status"] == "healthy" for result in results)
        record_event(
            "source-guard", f"Batch connection check completed for {len(results):,} sources.",
            context={"sources": len(results), "healthy": healthy}, user_id=request.state.user.id,
        )
        return redirect(
            "/library-health",
            f"Checked {len(results):,} sources: {healthy:,} available and {len(results)-healthy:,} still protected. No catalog or media files were changed.",
        )

    @librarian_post("/library-health/quality-profiles/{root_id}")
    def save_library_quality_profile(
        request: Request, root_id: int,
        minimum_width: str = Form(""), minimum_height: str = Form(""),
        minimum_bitrate_mbps: str = Form(""),
        preferred_video_codecs: str = Form(""),
        preferred_containers: str = Form(""),
        minimum_audio_channels: str = Form(""),
        dynamic_range: str = Form("any"), detect_outliers: str = Form(""),
    ):
        try:
            mie.save_quality_profile(
                root_id,
                minimum_width=minimum_width,
                minimum_height=minimum_height,
                minimum_bitrate_mbps=minimum_bitrate_mbps,
                preferred_video_codecs=preferred_video_codecs,
                preferred_containers=preferred_containers,
                minimum_audio_channels=minimum_audio_channels,
                dynamic_range=dynamic_range,
                detect_outliers=detect_outliers == "on",
                user_id=request.state.user.id,
            )
            finding_count = mie.analyze()
        except (ValueError, sqlite3.Error) as exc:
            record_event(
                "mie", "A Library Health quality profile could not be saved.",
                level="error", detail=str(exc), context={"root_id": root_id},
                user_id=request.state.user.id,
            )
            return redirect(
                "/library-health",
                f"The quality profile was not saved. {exc}",
            )
        record_event(
            "mie", "A Library Health quality profile was saved.",
            context={"root_id": root_id, "finding_count": finding_count},
            user_id=request.state.user.id,
        )
        return redirect(
            "/library-health",
            "Quality profile saved and Library Health refreshed. No media files were changed.",
        )

    @librarian_post("/library-health/calibration")
    def save_library_health_calibration(
        request: Request,
        identity_warning_threshold: str = Form("70"),
        source_stale_hours: str = Form("24"),
        critical_weight: str = Form("20"),
        warning_weight: str = Form("8"),
        information_weight: str = Form("2"),
    ):
        try:
            mie.save_calibration(
                identity_warning_threshold=identity_warning_threshold,
                source_stale_hours=source_stale_hours,
                critical_weight=critical_weight,
                warning_weight=warning_weight,
                information_weight=information_weight,
                user_id=request.state.user.id,
            )
            finding_count = mie.analyze()
        except (ValueError, sqlite3.Error) as exc:
            record_event(
                "mie", "Library Health calibration could not be saved.",
                level="error", detail=str(exc), context={"operation": "calibration"},
                user_id=request.state.user.id,
            )
            return redirect(
                "/library-health", f"Calibration was not saved. {exc} Correct the settings and try again."
            )
        record_event(
            "mie", "Library Health calibration was saved.",
            context={"finding_count": finding_count}, user_id=request.state.user.id,
        )
        return redirect(
            "/library-health",
            "Calibration saved and Library Health refreshed. No media files were changed.",
        )

    @librarian_post("/library-health/quality-profiles/{root_id}/delete")
    def delete_library_quality_profile(request: Request, root_id: int):
        if not mie.delete_quality_profile(root_id):
            return redirect(
                "/library-health",
                "That quality profile no longer exists. Refresh Library Health to see current settings.",
            )
        finding_count = mie.analyze()
        record_event(
            "mie", "A Library Health quality profile was removed.",
            context={"root_id": root_id, "finding_count": finding_count},
            user_id=request.state.user.id,
        )
        return redirect(
            "/library-health",
            "Quality profile removed and related findings refreshed. No media files were changed.",
        )

    @librarian_post("/library-health/findings/{finding_id}/dismiss")
    def dismiss_library_health_finding(
        request: Request, finding_id: int, reason: str = Form("other"),
        scope: str = Form("finding"), note: str = Form(""),
    ):
        try:
            dismissed = mie.dismiss(
                finding_id, request.state.user.id, reason=reason, scope=scope, note=note,
            )
        except ValueError as exc:
            return redirect(
                "/library-health", f"The finding was not dismissed. {exc} Review the feedback and try again."
            )
        if not dismissed:
            return redirect(
                "/library-health",
                "That finding was not dismissed because it is no longer active. "
                "Refresh Library Health to see its current status.",
            )
        record_event(
            "mie", f"Library Health finding {finding_id} was dismissed.",
            context={"finding_id": finding_id, "reason": reason, "scope": scope},
            user_id=request.state.user.id,
        )
        return redirect(
            "/library-health",
            "Feedback saved and finding dismissed. MIE will apply that correction to the selected scope.",
        )

    @librarian_post("/library-health/findings/{finding_id}/restore")
    def restore_library_health_finding(request: Request, finding_id: int):
        if not mie.restore(finding_id):
            return redirect(
                "/library-health?status=dismissed",
                "That finding could not be restored because it is no longer dismissed. "
                "Refresh Library Health to see its current status.",
            )
        record_event(
            "mie", f"Library Health finding {finding_id} was restored.",
            context={"finding_id": finding_id}, user_id=request.state.user.id,
        )
        return redirect(
            "/library-health?status=dismissed",
            "Finding restored to the active Library Health list.",
        )

    @librarian_post("/library-health/feedback/{feedback_id}/delete")
    def delete_library_health_feedback(request: Request, feedback_id: int):
        if not mie.delete_feedback(feedback_id):
            return redirect(
                "/library-health",
                "That learned exception was not removed because it is no longer active. Refresh and try again.",
            )
        finding_count = mie.analyze()
        record_event(
            "mie", f"Library Health feedback {feedback_id} was removed.",
            context={"feedback_id": feedback_id, "finding_count": finding_count},
            user_id=request.state.user.id,
        )
        return redirect(
            "/library-health",
            "Learned exception removed and Library Health refreshed. Matching findings may appear again.",
        )

    @librarian_get("/duplicates", response_class=HTMLResponse)
    def duplicate_review(
        request: Request, status: str = "active", evidence: str | None = None,
        refresh: bool = False, cleanup_status: str = "all", q: str = "",
        source: str = "", sort: str = "confidence",
    ):
        status = status if status in {"active", "ignored", "not_duplicate"} else "active"
        evidence = evidence if evidence in {"strong", "alternate", "all"} else (
            "strong" if status == "active" else "all"
        )
        all_candidates = duplicates.candidates(status=status)
        cleanup_status = cleanup_status if cleanup_status in {
            "all", "pending", "purged", "restored", "manual",
        } else "all"
        duplicate_opportunity = duplicates.recovery_opportunity(all_candidates)
        duplicate_impact = duplicate_trash.impact()
        cleanup_history = duplicate_trash.history(cleanup_status, limit=50)
        counts = {
            "verified_exact": sum(
                candidate["classification"] == "verified_exact"
                for candidate in all_candidates
            ),
            "likely": sum(
                candidate["classification"] == "likely"
                for candidate in all_candidates
            ),
            "alternate": sum(
                candidate["classification"] == "alternate"
                for candidate in all_candidates
            ),
        }
        if evidence == "strong":
            candidates = [
                candidate for candidate in all_candidates
                if candidate["classification"] in {"verified_exact", "likely"}
            ]
        elif evidence == "alternate":
            candidates = [
                candidate for candidate in all_candidates
                if candidate["classification"] == "alternate"
            ]
        else:
            candidates = all_candidates
        source_options = sorted({
            (str(item["file_a"].get("root_id") or ""), item["file_a"]["root_label"])
            for item in all_candidates
        } | {
            (str(item["file_b"].get("root_id") or ""), item["file_b"]["root_label"])
            for item in all_candidates
        }, key=lambda item: item[1].casefold())
        query = q.strip().casefold()
        if query:
            candidates = [item for item in candidates if query in " ".join([
                item["title_name"], item["file_a"]["filename"], item["file_a"]["path"],
                item["file_b"]["filename"], item["file_b"]["path"],
            ]).casefold()]
        if source:
            candidates = [item for item in candidates if source in {
                str(item["file_a"].get("root_id") or ""),
                str(item["file_b"].get("root_id") or ""),
            }]
        sort = sort if sort in {"confidence", "space", "title"} else "confidence"
        if sort == "space":
            candidates.sort(key=lambda item: (-item["recoverable_bytes"], item["title_name"].casefold()))
        elif sort == "title":
            candidates.sort(key=lambda item: item["title_name"].casefold())
        message = request.query_params.get("message", "")
        if refresh and not message:
            message = (
                f"Duplicate candidates refreshed from the current catalog. "
                f"InfoMancer found {len(all_candidates):,} pair"
                f"{'s' if len(all_candidates) != 1 else ''} in this review state."
            )
        return templates.TemplateResponse(request, "duplicates.html", {
            "candidates": candidates,
            "candidate_counts": counts,
            "candidate_total": len(all_candidates),
            "status": status,
            "evidence": evidence,
            "message": message,
            "trash_count": len(duplicate_trash.items()),
            "duplicate_opportunity": duplicate_opportunity,
            "duplicate_impact": duplicate_impact,
            "cleanup_history": cleanup_history,
            "cleanup_status": cleanup_status,
            "q": q.strip(), "source": source, "sort": sort,
            "source_options": source_options,
        })

    @librarian_post("/duplicates/bulk-action")
    def bulk_duplicate_action(
        request: Request, pairs: list[str] = Form(default=[]), action: str = Form(...),
    ):
        allowed = {"ignored", "not_duplicate", "active", "verify"}
        if action not in allowed:
            return redirect("/duplicates", "That bulk review choice was not recognized. Nothing changed.")
        parsed: list[tuple[int, int]] = []
        for value in list(dict.fromkeys(pairs))[:500]:
            if not re.fullmatch(r"\d+:\d+", value):
                continue
            left, right = (int(part) for part in value.split(":", 1))
            if left != right:
                parsed.append((left, right))
        if not parsed:
            return redirect("/duplicates", "Select at least one duplicate candidate first.")
        user_id = request.state.user.id
        if action != "verify":
            changed = sum(duplicates.decide(left, right, action, user_id) for left, right in parsed)
            labels = {"ignored": "ignored for now", "not_duplicate": "kept as intentional alternatives", "active": "returned to review"}
            message = f"{changed:,} duplicate candidate pair{'s' if changed != 1 else ''} {labels[action]}. No media files were changed."
            record_event("duplicates", message, context={"pairs": changed, "action": action}, user_id=user_id)
            return redirect("/duplicates", message)
        with duplicate_verify_lock:
            if duplicate_verify_job.get("status") in {"starting", "running"}:
                return redirect("/duplicates", "A duplicate verification is already running. Its progress is shown in the task panel.")
            duplicate_verify_job.clear()
            duplicate_verify_job.update({"status": "starting", "total": len(parsed), "processed": 0, "detail": "Preparing selected file comparisons"})

        def run_bulk_verification() -> None:
            exact = different = failed = 0
            for index, (left, right) in enumerate(parsed, 1):
                with duplicate_verify_lock:
                    duplicate_verify_job.update({"status": "running", "processed": index - 1, "detail": f"Verifying pair {index:,} of {len(parsed):,}"})
                try:
                    result = duplicates.verify(left, right, user_id)
                    exact += result == "exact"
                    different += result != "exact"
                except (OSError, ValueError):
                    failed += 1
                with duplicate_verify_lock:
                    duplicate_verify_job["processed"] = index
            message = f"Verified {len(parsed):,} pairs: {exact:,} exact, {different:,} different, {failed:,} unavailable. No files were changed."
            record_event("duplicates", message, context={"pairs": len(parsed), "exact": exact, "different": different, "failed": failed}, user_id=user_id)
            with duplicate_verify_lock:
                duplicate_verify_job.update({"status": "complete", "detail": message})

        threading.Thread(target=run_bulk_verification, daemon=True).start()
        return redirect("/duplicates", f"Verification started for {len(parsed):,} selected pairs. Progress is shown in the task panel.")

    @librarian_get("/duplicates/{file_id}/trash-preview")
    def preview_duplicate_trash(request: Request, file_id: int):
        try:
            preview = duplicate_trash.preview(file_id, trash_retention_days())
        except DuplicateTrashError as exc:
            return redirect("/duplicates", str(exc))
        return templates.TemplateResponse(request, "duplicate_trash_preview.html", {
            "preview": preview,
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/duplicates/{file_id}/trash")
    def move_duplicate_to_trash(request: Request, file_id: int):
        try:
            duplicate_trash.move(file_id, trash_retention_days(), request.state.user.id)
        except (DuplicateTrashError, OSError, sqlite3.Error) as exc:
            return redirect(
                f"/duplicates/{file_id}/trash-preview",
                str(exc) if isinstance(exc, DuplicateTrashError) else
                "InfoMancer could not move the file into managed trash. The original file was left in place. Check that the source is writable, then try again.",
            )
        message = (
            "The selected copy was moved into managed trash and removed from the active catalog. "
            "You can restore it from Duplicate Review → Trash until its retention date."
        )
        record_event(
            "duplicates", message, context={"file_id": file_id},
            user_id=request.state.user.id,
        )
        return redirect("/duplicates/trash", message)

    @librarian_post("/duplicates/{file_id}/verify-removed")
    def verify_duplicate_removed(request: Request, file_id: int):
        try:
            path = duplicate_trash.verify_manually_removed(file_id, request.state.user.id)
        except DuplicateTrashError as exc:
            return redirect("/duplicates", str(exc))
        message = (
            "Deletion verified. The file was no longer present, so InfoMancer removed its stale "
            "catalog entry. No other file was changed."
        )
        record_event(
            "duplicates", message, context={"file_id": file_id, "path": path},
            user_id=request.state.user.id,
        )
        return redirect("/duplicates", message)

    @librarian_get("/duplicates/trash")
    def duplicate_trash_page(request: Request):
        maybe_start_trash_cleanup()
        return templates.TemplateResponse(request, "duplicate_trash.html", {
            "items": duplicate_trash.items(),
            "retention": app_settings.get("trash_retention_days"),
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/duplicates/trash/retention")
    def update_duplicate_trash_retention(
        request: Request, retention: str = Form(...),
    ):
        retention = retention.strip().casefold()
        if retention not in {"never", "7", "30", "90", "365"}:
            return redirect(
                "/duplicates/trash",
                "Choose Never, 7 days, 30 days, 90 days, or 1 year. The retention setting was not changed.",
            )
        app_settings.update(
            {"trash_retention_days": retention}, request.state.user.id,
        )
        label = "Never automatically" if retention == "never" else f"After {retention} days"
        return redirect(
            "/duplicates/trash",
            f"Managed-trash retention updated: {label}. This applies to files moved to trash from now on.",
        )

    @librarian_post("/duplicates/trash/{trash_id}/restore")
    def restore_duplicate_trash(request: Request, trash_id: int):
        try:
            path = duplicate_trash.restore(trash_id)
        except (DuplicateTrashError, OSError, sqlite3.Error) as exc:
            return redirect(
                "/duplicates/trash",
                str(exc) if isinstance(exc, DuplicateTrashError) else
                "InfoMancer could not restore that file. Nothing was overwritten. Check that the source is mounted and writable, then try again.",
            )
        message = f"File restored to its original location and returned to the catalog: {path}"
        record_event(
            "duplicates", message, context={"trash_id": trash_id, "path": path},
            user_id=request.state.user.id,
        )
        return redirect("/duplicates/trash", message)

    @librarian_post("/duplicates/{file_a_id}/{file_b_id}/decision")
    def decide_duplicate(
        request: Request, file_a_id: int, file_b_id: int,
        decision: str = Form(...),
    ):
        labels = {
            "ignored": (
                "Candidate ignored. It will return if either file changes so the new "
                "version can be reviewed."
            ),
            "not_duplicate": (
                "Files marked as intentional alternatives. InfoMancer will not show "
                "this pair as an active duplicate candidate."
            ),
            "active": "Candidate restored to the active duplicate review list.",
        }
        if decision not in labels:
            return redirect(
                "/duplicates",
                "That review choice was not recognized, so nothing changed. Refresh the page and try again.",
            )
        if not duplicates.decide(file_a_id, file_b_id, decision, request.state.user.id):
            return redirect(
                "/duplicates",
                "InfoMancer could not save that choice because one or both files are no longer in the catalog. Rescan the source and review the current candidates.",
            )
        record_event(
            "duplicates", labels[decision],
            context={"file_a_id": file_a_id, "file_b_id": file_b_id, "decision": decision},
            user_id=request.state.user.id,
        )
        destination = "/duplicates"
        return redirect(destination, labels[decision])

    @librarian_post("/duplicates/{file_a_id}/{file_b_id}/verify")
    def verify_duplicate(request: Request, file_a_id: int, file_b_id: int):
        with duplicate_verify_lock:
            if duplicate_verify_job.get("status") in {"starting", "running"}:
                return redirect(
                    "/duplicates",
                    "A duplicate verification is already running. Its progress is shown in the task panel.",
                )
            duplicate_verify_job.clear()
            duplicate_verify_job.update({
                "status": "starting",
                "detail": "Preparing to read both files byte for byte",
            })

        user_id = request.state.user.id

        def run_verification() -> None:
            try:
                with duplicate_verify_lock:
                    duplicate_verify_job.update({
                        "status": "running",
                        "detail": "Reading both files byte for byte; large files may take several minutes",
                    })
                result = duplicates.verify(file_a_id, file_b_id, user_id)
                message = (
                    "Verification finished: the files are byte-for-byte identical. "
                    "InfoMancer did not delete or move either file."
                    if result == "exact" else
                    "Verification finished: the files contain different bytes. They may be "
                    "different encodes or editions, and InfoMancer did not change either file."
                )
                record_event(
                    "duplicates", message,
                    context={"file_a_id": file_a_id, "file_b_id": file_b_id, "result": result},
                    user_id=user_id,
                )
                with duplicate_verify_lock:
                    duplicate_verify_job.update({
                        "status": "complete", "detail": message, "result": result,
                    })
            except (OSError, ValueError) as exc:
                message = str(exc)
                record_event(
                    "duplicates", "Duplicate verification could not be completed.",
                    level="error", detail=message,
                    context={"file_a_id": file_a_id, "file_b_id": file_b_id},
                    user_id=user_id,
                )
                with duplicate_verify_lock:
                    duplicate_verify_job.update({
                        "status": "error", "detail": message, "error": message,
                    })

        threading.Thread(target=run_verification, daemon=True).start()
        return redirect(
            "/duplicates",
            "Verification started in the background. InfoMancer will read both files without changing them; progress is shown in the task panel.",
        )

    @librarian_get("/intake", response_class=HTMLResponse)
    def intake(request: Request):
        with db.connect() as conn:
            rows = conn.execute(
                """SELECT t.*, r.label root_label, r.path root_path,
                   (SELECT COUNT(*) FROM files f WHERE f.title_id=t.id) file_count
                   FROM titles t JOIN roots r ON r.id=t.root_id
                   WHERE t.discovered_at IS NOT NULL AND
                     ((t.kind='tv' AND t.tvdb_id IS NULL) OR
                      (t.kind='movie' AND t.tvdb_movie_id IS NULL))
                   ORDER BY t.discovered_at DESC, t.kind, t.title COLLATE NOCASE"""
            ).fetchall()
        return templates.TemplateResponse(request, "intake.html", {
            "rows": rows, "message": request.query_params.get("message", ""),
        })

    @librarian_get("/bulk-match", response_class=HTMLResponse)
    def bulk_match_home(request: Request):
        with db.connect() as conn:
            counts = conn.execute(
                """SELECT
                   (SELECT COUNT(*) FROM titles WHERE kind='movie' AND tvdb_movie_id IS NULL) movies,
                   (SELECT COUNT(*) FROM titles WHERE kind='tv' AND tvdb_id IS NULL) shows"""
            ).fetchone()
        return templates.TemplateResponse(request, "bulk_match.html", {
            "counts": counts, "message": request.query_params.get("message", ""),
        })

    @librarian_get("/shows/bulk-match", response_class=HTMLResponse)
    def bulk_tv_match_review(
        request: Request, review: bool = False, offset: int = 0, selected: bool = False,
    ):
        with tv_match_lock:
            job = dict(tv_match_job)
        selected_ids = job.get("title_ids", []) if selected and job.get("mode") == "selected" else []
        direct_selection = bool(selected_ids)
        with db.connect() as conn:
            available = conn.execute(
                """SELECT t.*, r.label root_label, r.path root_path
                   FROM titles t JOIN roots r ON r.id=t.root_id
                   WHERE t.kind='tv' AND t.tvdb_id IS NULL
                   ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
            ).fetchall()
            cached_count = conn.execute(
                """SELECT COUNT(*) FROM tv_match_suggestions s JOIN titles t ON t.id=s.title_id
                   WHERE t.kind='tv' AND t.tvdb_id IS NULL"""
            ).fetchone()[0]
            if review and direct_selection:
                placeholders = ",".join("?" for _ in selected_ids)
                suggestion_rows = conn.execute(
                    f"""SELECT t.*, r.label root_label, r.path root_path,
                               s.title_id suggestion_id, s.candidate_json,
                               s.confidence_score, s.confidence_label,
                               s.result_count, s.exact, s.error analysis_error
                        FROM titles t JOIN roots r ON r.id=t.root_id
                        LEFT JOIN tv_match_suggestions s ON s.title_id=t.id
                        WHERE t.kind='tv' AND t.tvdb_id IS NULL
                          AND t.id IN ({placeholders})
                        ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE
                        LIMIT 50 OFFSET ?""",
                    [*selected_ids, max(0, offset)],
                ).fetchall()
                cached_count = len(selected_ids)
            else:
                suggestion_rows = conn.execute(
                    """SELECT t.*, r.label root_label, r.path root_path,
                              s.title_id suggestion_id, s.candidate_json,
                              s.confidence_score, s.confidence_label,
                              s.result_count, s.exact, s.error analysis_error
                       FROM tv_match_suggestions s JOIN titles t ON t.id=s.title_id
                       JOIN roots r ON r.id=t.root_id WHERE t.kind='tv' AND t.tvdb_id IS NULL
                       ORDER BY s.analyzed_at, COALESCE(t.metadata_title,t.title) COLLATE NOCASE
                       LIMIT 50 OFFSET ?""", (max(0, offset),)
                ).fetchall() if review else []
        suggestions = []
        for row in suggestion_rows:
            suggestions.append({
                "title": row,
                "candidate": json.loads(row["candidate_json"]) if row["candidate_json"] else None,
                "confidence": ({"score": row["confidence_score"], "label": row["confidence_label"]}
                               if row["confidence_score"] is not None else None),
                "exact": bool(row["exact"]), "result_count": row["result_count"],
                "error": row["analysis_error"], "pending": row["suggestion_id"] is None,
            })
        return templates.TemplateResponse(request, "bulk_tv_match.html", {
            "shows": available, "available_count": len(available), "suggestions": suggestions,
            "analyzed": review, "cached_count": cached_count, "offset": max(0, offset),
            "job": job, "direct_selection": direct_selection,
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/shows/bulk-match/analyze")
    def start_bulk_tv_analysis(mode: str = Form("selected"), selected: list[int] = Form(default=[])):
        with tv_match_lock:
            if tv_match_job.get("status") in {"starting", "running"}:
                return redirect("/shows/bulk-match", "TV series analysis is already running")
        selected_ids = list(dict.fromkeys(selected))
        with db.connect() as conn:
            if mode == "all":
                rows = conn.execute(
                    """SELECT t.id FROM titles t LEFT JOIN tv_match_suggestions s ON s.title_id=t.id
                       WHERE t.kind='tv' AND t.tvdb_id IS NULL AND s.title_id IS NULL ORDER BY t.title COLLATE NOCASE"""
                ).fetchall()
            elif mode == "next":
                rows = conn.execute(
                    """SELECT t.id FROM titles t LEFT JOIN tv_match_suggestions s ON s.title_id=t.id
                       WHERE t.kind='tv' AND t.tvdb_id IS NULL AND s.title_id IS NULL
                       ORDER BY t.title COLLATE NOCASE LIMIT 20"""
                ).fetchall()
            else:
                if not selected_ids:
                    return redirect("/shows/bulk-match", "Select at least one TV series")
                placeholders = ",".join("?" for _ in selected_ids)
                rows = conn.execute(
                    f"SELECT id FROM titles WHERE kind='tv' AND tvdb_id IS NULL AND id IN ({placeholders}) ORDER BY title COLLATE NOCASE",
                    selected_ids,
                ).fetchall()
        title_ids = [row["id"] for row in rows]
        if not title_ids:
            message = "No unmatched selected TV series remain" if mode == "selected" else "No unanalyzed TV series remain"
            return redirect("/shows/bulk-match?review=true", message)
        with tv_match_lock:
            tv_match_job.clear()
            tv_match_job.update({"status": "starting", "total": len(title_ids), "processed": 0, "matched": 0, "errors": 0, "mode": mode, "title_ids": title_ids})
        threading.Thread(target=run_tv_match_analysis, args=(title_ids,), daemon=True).start()
        destination = "/shows/bulk-match?review=true&selected=true" if mode == "selected" else "/shows/bulk-match"
        return redirect(destination, f"Finding matches for {len(title_ids):,} selected TV series" if mode == "selected" else f"Background analysis started for {len(title_ids):,} TV series")

    @librarian_post("/shows/bulk-match")
    def bulk_tv_match_apply(
        matches: list[str] = Form(default=[]), selected_scope: str = Form(""),
    ):
        applied = failed = 0
        for value in matches[:50]:
            try:
                title_id, series_id = (int(part) for part in value.split(":", 1))
                store_tv_match(title_id, series_id)
                with db.connect() as conn:
                    conn.execute("DELETE FROM tv_match_suggestions WHERE title_id=?", (title_id,))
                applied += 1
            except (ValueError, TVDBError):
                failed += 1
        message = f"Matched {applied} TV series"
        if failed:
            message += f"; {failed} failed"
        destination = "/shows/bulk-match?review=true&selected=true" if selected_scope else "/shows/bulk-match?review=true"
        return redirect(destination, message)

    @librarian_get("/movies/bulk-match", response_class=HTMLResponse)
    def bulk_movie_match_review(
        request: Request, review: bool = False, offset: int = 0, selected: bool = False,
    ):
        with movie_match_lock:
            job = dict(movie_match_job)
        selected_ids = job.get("title_ids", []) if selected and job.get("mode") == "selected" else []
        direct_selection = bool(selected_ids)
        with db.connect() as conn:
            available = conn.execute(
                """SELECT t.*, r.label root_label, r.path root_path
                   FROM titles t JOIN roots r ON r.id=t.root_id
                   WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                   ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
            ).fetchall()
            cached_count = conn.execute(
                """SELECT COUNT(*) FROM movie_match_suggestions s
                   JOIN titles t ON t.id=s.title_id
                       WHERE t.kind='movie'"""
            ).fetchone()[0]
            unanalyzed_count = conn.execute(
                """SELECT COUNT(*) FROM titles t
                   LEFT JOIN movie_match_suggestions s ON s.title_id=t.id
                   WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                     AND s.title_id IS NULL"""
            ).fetchone()[0]
            no_result_count = conn.execute(
                """SELECT COUNT(*) FROM movie_match_suggestions s
                   JOIN titles t ON t.id=s.title_id
                   WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                     AND s.candidate_json IS NULL"""
            ).fetchone()[0]
            suggestion_rows = []
            if review:
                if direct_selection:
                    placeholders = ",".join("?" for _ in selected_ids)
                    suggestion_rows = conn.execute(
                        f"""SELECT t.*, r.label root_label, r.path root_path,
                                   s.title_id suggestion_id, s.candidate_json,
                                   s.confidence_score, s.confidence_label,
                                   s.result_count, s.exact, s.error analysis_error
                            FROM titles t JOIN roots r ON r.id=t.root_id
                            LEFT JOIN movie_match_suggestions s ON s.title_id=t.id
                            WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                              AND t.tmdb_id IS NULL AND t.imdb_id IS NULL
                              AND t.id IN ({placeholders})
                            ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE
                            LIMIT 50 OFFSET ?""",
                        [*selected_ids, max(0, offset)],
                    ).fetchall()
                    cached_count = len(selected_ids)
                else:
                    suggestion_rows = conn.execute(
                        """SELECT t.*, r.label root_label, r.path root_path,
                                  s.title_id suggestion_id, s.candidate_json,
                                  s.confidence_score, s.confidence_label,
                                  s.result_count, s.exact, s.error analysis_error
                           FROM movie_match_suggestions s
                           JOIN titles t ON t.id=s.title_id
                           JOIN roots r ON r.id=t.root_id
                           WHERE t.kind='movie'
                           ORDER BY s.analyzed_at, COALESCE(t.metadata_title,t.title) COLLATE NOCASE
                           LIMIT 50 OFFSET ?""",
                        (max(0, offset),),
                    ).fetchall()
        suggestions = []
        for row in suggestion_rows:
            candidate = json.loads(row["candidate_json"]) if row["candidate_json"] else None
            confidence = None
            if row["confidence_score"] is not None:
                confidence = {"score": row["confidence_score"], "label": row["confidence_label"]}
            suggestions.append({
                "title": row, "candidate": candidate, "confidence": confidence,
                "exact": bool(row["exact"]), "result_count": row["result_count"],
                "error": row["analysis_error"], "pending": row["suggestion_id"] is None,
            })
        return templates.TemplateResponse(request, "bulk_movie_match.html", {
            "movies": available, "available_count": len(available),
            "unanalyzed_count": unanalyzed_count,
            "suggestions": suggestions, "analyzed": review, "error": "",
            "cached_count": cached_count, "no_result_count": no_result_count,
            "offset": max(0, offset), "job": job,
            "direct_selection": direct_selection,
        })

    @librarian_post("/movies/bulk-match/analyze")
    def start_bulk_movie_analysis(
        mode: str = Form("selected"), selected: list[int] = Form(default=[]),
    ):
        with movie_match_lock:
            if movie_match_job.get("status") in {"starting", "running"}:
                return redirect("/movies/bulk-match", "Movie analysis is already running")
        selected_ids = list(dict.fromkeys(selected))
        with db.connect() as conn:
            if mode == "all":
                rows = conn.execute(
                    """SELECT t.id FROM titles t
                       LEFT JOIN movie_match_suggestions s ON s.title_id=t.id
                       WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL AND s.title_id IS NULL
                       ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
                ).fetchall()
            elif mode == "no_results":
                rows = conn.execute(
                    """SELECT t.id FROM movie_match_suggestions s
                       JOIN titles t ON t.id=s.title_id
                       WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                         AND s.candidate_json IS NULL
                       ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
                ).fetchall()
            elif mode == "next":
                rows = conn.execute(
                    """SELECT t.id FROM titles t
                       LEFT JOIN movie_match_suggestions s ON s.title_id=t.id
                       WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL AND s.title_id IS NULL
                       ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE LIMIT 20"""
                ).fetchall()
            else:
                if not selected_ids:
                    return redirect("/movies/bulk-match", "Select at least one movie")
                placeholders = ",".join("?" for _ in selected_ids)
                rows = conn.execute(
                    f"""SELECT id FROM titles WHERE kind='movie'
                          AND tvdb_movie_id IS NULL AND tmdb_id IS NULL AND imdb_id IS NULL
                          AND id IN ({placeholders}) ORDER BY title COLLATE NOCASE""",
                    selected_ids,
                ).fetchall()
        title_ids = [row["id"] for row in rows]
        if not title_ids:
            message = "No unmatched selected movies remain" if mode == "selected" else "No unanalyzed movies remain"
            return redirect("/movies/bulk-match?review=true", message)
        with movie_match_lock:
            movie_match_job.clear()
            movie_match_job.update({
                "status": "starting", "total": len(title_ids), "processed": 0,
                "matched": 0, "errors": 0, "mode": mode, "title_ids": title_ids,
            })
        threading.Thread(target=run_movie_match_analysis, args=(title_ids,), daemon=True).start()
        destination = "/movies/bulk-match?review=true&selected=true" if mode == "selected" else "/movies/bulk-match"
        return redirect(destination, f"Finding matches for {len(title_ids):,} selected movies" if mode == "selected" else f"Background analysis started for {len(title_ids):,} movies")

    @librarian_post("/movies/bulk-match")
    def bulk_movie_match_apply(
        matches: list[str] = Form(default=[]), selected_scope: str = Form(""),
    ):
        applied, failed = 0, 0
        for value in matches[:50]:
            try:
                title_id, movie_id = (int(part) for part in value.split(":", 1))
                store_movie_match(title_id, movie_id)
                with db.connect() as conn:
                    conn.execute("DELETE FROM movie_match_suggestions WHERE title_id=?", (title_id,))
                applied += 1
            except (ValueError, TVDBError):
                failed += 1
        message = f"Matched {applied} movies"
        if failed:
            message += f"; {failed} failed"
        destination = "/movies/bulk-match?review=true&selected=true" if selected_scope else "/movies/bulk-match?review=true"
        return redirect(destination, message)

    return router, {
        "library_health": library_health,
        "storage_intelligence": storage_intelligence,
        "title_identity": title_identity,
        "analyze_library_health": analyze_library_health,
        "preview_library_health_remediation": preview_library_health_remediation,
        "apply_library_health_remediation": apply_library_health_remediation,
        "batch_library_health_remediation": batch_library_health_remediation,
        "save_library_quality_profile": save_library_quality_profile,
        "save_library_health_calibration": save_library_health_calibration,
        "delete_library_quality_profile": delete_library_quality_profile,
        "dismiss_library_health_finding": dismiss_library_health_finding,
        "restore_library_health_finding": restore_library_health_finding,
        "delete_library_health_feedback": delete_library_health_feedback,
        "duplicate_review": duplicate_review,
        "bulk_duplicate_action": bulk_duplicate_action,
        "preview_duplicate_trash": preview_duplicate_trash,
        "move_duplicate_to_trash": move_duplicate_to_trash,
        "verify_duplicate_removed": verify_duplicate_removed,
        "duplicate_trash_page": duplicate_trash_page,
        "update_duplicate_trash_retention": update_duplicate_trash_retention,
        "restore_duplicate_trash": restore_duplicate_trash,
        "decide_duplicate": decide_duplicate,
        "verify_duplicate": verify_duplicate,
        "intake": intake,
        "bulk_match_home": bulk_match_home,
        "bulk_tv_match_review": bulk_tv_match_review,
        "start_bulk_tv_analysis": start_bulk_tv_analysis,
        "bulk_tv_match_apply": bulk_tv_match_apply,
        "bulk_movie_match_review": bulk_movie_match_review,
        "start_bulk_movie_analysis": start_bulk_movie_analysis,
        "bulk_movie_match_apply": bulk_movie_match_apply,
    }

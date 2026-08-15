from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Anchor not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1. Server-backed inspector and fast personal actions.
library_path = ROOT / "app/routes/library.py"
library = library_path.read_text(encoding="utf-8")
anchor = '''    @router.get("/movies", response_class=HTMLResponse)\n    def movies(\n'''
inspector_code = r'''    def workspace_inspector_context(request: Request, title_id: int) -> dict:
        """Build the read-only Workspace Inspector from catalog state only."""
        with db.connect() as conn:
            title_row = conn.execute(
                """SELECT t.*,r.label source_label,r.path source_path,
                          r.health_status source_health,r.last_scanned_at source_scanned_at,
                          COALESCE(uts.favorite,0) favorite,uts.personal_rating,uts.sort_title
                   FROM titles t JOIN roots r ON r.id=t.root_id
                   LEFT JOIN user_title_state uts
                     ON uts.title_id=t.id AND uts.user_id=?
                   WHERE t.id=?""",
                (request.state.user.id, title_id),
            ).fetchone()
            if not title_row:
                raise HTTPException(404, "Title not found")
            title = dict(title_row)
            file_rows = conn.execute(
                """SELECT * FROM files WHERE title_id=?
                   ORDER BY version_preferred DESC,identity_confirmed DESC,id
                   LIMIT 12""",
                (title_id,),
            ).fetchall()
            tags = conn.execute(
                """SELECT ut.id,ut.name,ut.color,
                          CASE WHEN tt.title_id IS NULL THEN 0 ELSE 1 END selected
                   FROM user_tags ut LEFT JOIN title_tags tt
                     ON tt.tag_id=ut.id AND tt.title_id=?
                   WHERE ut.user_id=? ORDER BY ut.name COLLATE NOCASE""",
                (title_id, request.state.user.id),
            ).fetchall() if request.state.user.id > 0 else []
            collections = conn.execute(
                """SELECT c.id,c.name FROM collections c
                   JOIN collection_titles ct ON ct.collection_id=c.id
                   WHERE ct.title_id=? ORDER BY c.name COLLATE NOCASE""",
                (title_id,),
            ).fetchall()
            libraries = conn.execute(
                """SELECT l.id,l.name FROM custom_libraries l
                   JOIN custom_library_titles lt ON lt.library_id=l.id
                   WHERE lt.title_id=? ORDER BY l.name COLLATE NOCASE""",
                (title_id,),
            ).fetchall()
            findings = conn.execute(
                """SELECT id,severity,category,summary,recommendation,last_seen_at
                   FROM mie_findings WHERE title_id=? AND status='active'
                   ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                            last_seen_at DESC LIMIT 5""",
                (title_id,),
            ).fetchall()
            finding_counts = conn.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) critical,
                          SUM(CASE WHEN severity='warning' THEN 1 ELSE 0 END) warning
                   FROM mie_findings WHERE title_id=? AND status='active'""",
                (title_id,),
            ).fetchone()
            missing_count = conn.execute(
                """SELECT COUNT(*) count FROM expected_episodes e
                   WHERE e.title_id=? AND e.season>0
                     AND (e.aired IS NULL OR e.aired<=date('now'))
                     AND NOT EXISTS (
                       SELECT 1 FROM files owned WHERE owned.title_id=e.title_id
                         AND owned.season=e.season
                         AND e.episode BETWEEN owned.episode_start
                           AND COALESCE(owned.episode_end,owned.episode_start)
                     )""",
                (title_id,),
            ).fetchone()["count"] if title["kind"] == "tv" else 0
            duplicate_count = conn.execute(
                """SELECT COUNT(*) count FROM duplicate_reviews dr
                   JOIN files a ON a.id=dr.file_a_id
                   JOIN files b ON b.id=dr.file_b_id
                   WHERE dr.decision='active' AND (a.title_id=? OR b.title_id=?)""",
                (title_id, title_id),
            ).fetchone()["count"]
            metadata_queue = conn.execute(
                "SELECT status,provider,error,requested_at,completed_at FROM metadata_refresh_queue WHERE title_id=?",
                (title_id,),
            ).fetchone()

        def size_label(value: int | float | None) -> str:
            amount = float(value or 0)
            units = ("B", "KB", "MB", "GB", "TB", "PB")
            unit = units[0]
            for candidate in units:
                unit = candidate
                if amount < 1024 or candidate == units[-1]:
                    break
                amount /= 1024
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"

        def runtime_label(value: int | float | None) -> str:
            seconds = int(value or 0)
            if seconds <= 0:
                return ""
            minutes = max(1, round(seconds / 60))
            hours, minutes = divmod(minutes, 60)
            return f"{hours}h {minutes}m" if hours else f"{minutes}m"

        files = []
        total_size = 0
        total_runtime = 0.0
        for row in file_rows:
            item = dict(row)
            total_size += int(item.get("size_bytes") or 0)
            total_runtime += float(item.get("runtime_seconds") or 0)
            item["size_display"] = size_label(item.get("size_bytes"))
            item["runtime_display"] = runtime_label(item.get("runtime_seconds"))
            width, height = item.get("width"), item.get("height")
            item["resolution_display"] = f"{width}×{height}" if width and height else ""
            files.append(item)
        primary = files[0] if files else None
        matched = bool(
            title.get("tvdb_id") if title["kind"] == "tv"
            else title.get("tvdb_movie_id") or title.get("tmdb_id") or title.get("imdb_id")
        )
        provider_ids = []
        for label, value in (
            ("TVDB", title.get("tvdb_id") or title.get("tvdb_movie_id")),
            ("TMDB", title.get("tmdb_id")),
            ("IMDb", title.get("imdb_id")),
        ):
            if value:
                provider_ids.append({"label": label, "value": str(value)})
        return {
            "title": title,
            "display_title": title.get("metadata_title") or title.get("title") or "Untitled",
            "display_year": title.get("metadata_year") or title.get("year"),
            "matched": matched,
            "provider_ids": provider_ids,
            "files": files,
            "primary_file": primary,
            "file_count": len(files),
            "total_size_display": size_label(total_size),
            "total_runtime_display": runtime_label(total_runtime),
            "tags": tags,
            "collections": collections,
            "libraries": libraries,
            "findings": findings,
            "finding_counts": dict(finding_counts) if finding_counts else {"total": 0, "critical": 0, "warning": 0},
            "missing_count": missing_count,
            "duplicate_count": duplicate_count,
            "metadata_queue": dict(metadata_queue) if metadata_queue else None,
            "message": "",
        }

    @router.get("/library/inspector/{title_id}", response_class=HTMLResponse)
    def workspace_inspector(request: Request, title_id: int):
        response = templates.TemplateResponse(
            request, "_workspace_inspector.html",
            workspace_inspector_context(request, title_id),
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/api/titles/{title_id}/favorite")
    def workspace_toggle_favorite(request: Request, title_id: int) -> dict:
        if request.state.user.id <= 0:
            raise HTTPException(403, "Favorites require a signed-in account")
        with db.connect() as conn:
            title = conn.execute(
                "SELECT id,COALESCE(NULLIF(metadata_title,''),title) name FROM titles WHERE id=?",
                (title_id,),
            ).fetchone()
            if not title:
                raise HTTPException(404, "Title not found")
            current = conn.execute(
                "SELECT favorite FROM user_title_state WHERE user_id=? AND title_id=?",
                (request.state.user.id, title_id),
            ).fetchone()
            favorite = not bool(current and current["favorite"])
            conn.execute(
                """INSERT INTO user_title_state(user_id,title_id,favorite,updated_at)
                   VALUES (?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id,title_id) DO UPDATE SET
                     favorite=excluded.favorite,updated_at=CURRENT_TIMESTAMP""",
                (request.state.user.id, title_id, int(favorite)),
            )
        record_event(
            "library", "Title added to favorites." if favorite else "Title removed from favorites.",
            user_id=request.state.user.id, context={"title_id": title_id, "source": "workspace-inspector"},
        )
        return {"title_id": title_id, "favorite": favorite}

    @router.post("/api/titles/{title_id}/tags/{tag_id}")
    def workspace_toggle_tag(request: Request, title_id: int, tag_id: int) -> dict:
        if request.state.user.id <= 0:
            raise HTTPException(403, "Tags require a signed-in account")
        with db.connect() as conn:
            if not conn.execute("SELECT id FROM titles WHERE id=?", (title_id,)).fetchone():
                raise HTTPException(404, "Title not found")
            tag = conn.execute(
                "SELECT id,name FROM user_tags WHERE id=? AND user_id=?",
                (tag_id, request.state.user.id),
            ).fetchone()
            if not tag:
                raise HTTPException(404, "Tag not found")
            existing = conn.execute(
                "SELECT 1 FROM title_tags WHERE title_id=? AND tag_id=?",
                (title_id, tag_id),
            ).fetchone()
            selected = not bool(existing)
            if selected:
                conn.execute(
                    "INSERT OR IGNORE INTO title_tags(title_id,tag_id) VALUES (?,?)",
                    (title_id, tag_id),
                )
            else:
                conn.execute(
                    "DELETE FROM title_tags WHERE title_id=? AND tag_id=?",
                    (title_id, tag_id),
                )
        record_event(
            "library", f'Tag "{tag["name"]}" {"added to" if selected else "removed from"} title.',
            user_id=request.state.user.id,
            context={"title_id": title_id, "tag_id": tag_id, "source": "workspace-inspector"},
        )
        return {"title_id": title_id, "tag_id": tag_id, "selected": selected}

'''
if anchor not in library:
    raise RuntimeError("Could not locate movies route anchor")
library = library.replace(anchor, inspector_code + anchor, 1)
return_anchor = '''        "library": library,\n        "movies": movies,\n'''
return_replacement = '''        "library": library,\n        "workspace_inspector": workspace_inspector,\n        "workspace_toggle_favorite": workspace_toggle_favorite,\n        "workspace_toggle_tag": workspace_toggle_tag,\n        "movies": movies,\n'''
if return_anchor not in library:
    raise RuntimeError("Could not locate library route return map")
library = library.replace(return_anchor, return_replacement, 1)
library_path.write_text(library, encoding="utf-8")


# 2. Rich server-rendered Inspector partial.
partial = r'''<div class="workspace-inspector-panel" data-workspace-inspector-panel data-title-id="{{ title.id }}" data-detail-href="/titles/{{ title.id }}">
  <input type="hidden" data-workspace-csrf value="{{ csrf_token }}">
  <div class="workspace-inspector-summary">
    <div class="workspace-inspector-art">
      {% if title.poster_url %}<img src="{{ title.poster_url }}" alt="" loading="lazy">{% else %}<span class="workspace-inspector-art-placeholder">{{ display_title[:1]|upper }}</span>{% endif %}
    </div>
    <div class="workspace-inspector-identity">
      <p class="workspace-inspector-kicker">{{ 'TV SHOW' if title.kind == 'tv' else 'MOVIE' }}</p>
      <h2 class="workspace-inspector-title">{{ display_title }}</h2>
      <p class="workspace-inspector-subtitle">{% if display_year %}{{ display_year }} · {% endif %}{{ title.source_label or title.source_path }}</p>
      <div class="workspace-inspector-statuses">
        <span class="workspace-status{% if matched %} good{% else %} warning{% endif %}">{{ 'Matched' if matched else 'Unmatched' }}</span>
        <span class="workspace-status{% if title.source_health == 'healthy' %} good{% elif title.source_health in ['degraded','offline'] %} warning{% endif %}">Source {{ title.source_health|title }}</span>
        {% if title.metadata_status %}<span class="workspace-status">{{ title.metadata_status|replace('_',' ')|title }}</span>{% endif %}
      </div>
    </div>
    {% if current_user.id > 0 %}<button type="button" class="workspace-inspector-favorite{% if title.favorite %} active{% endif %}" data-workspace-favorite aria-pressed="{{ 'true' if title.favorite else 'false' }}" title="{{ 'Remove from favorites' if title.favorite else 'Add to favorites' }}"><span>★</span><small>{{ 'Favorite' if title.favorite else 'Favorite' }}</small></button>{% endif %}
  </div>

  {% if title.overview %}<p class="workspace-inspector-overview">{{ title.overview }}</p>{% endif %}

  <section class="workspace-inspector-section" aria-labelledby="inspector-health-title">
    <div class="workspace-inspector-section-head"><span><small>REVIEW</small><strong id="inspector-health-title">Health &amp; attention</strong></span><a href="/library-health">Open Review →</a></div>
    <div class="workspace-inspector-stat-grid">
      <div><strong>{{ finding_counts.total or 0 }}</strong><span>MIE findings</span></div>
      {% if title.kind == 'tv' %}<div><strong>{{ missing_count }}</strong><span>Missing episodes</span></div>{% endif %}
      <div><strong>{{ duplicate_count }}</strong><span>Duplicate decisions</span></div>
    </div>
    {% if findings %}<div class="workspace-inspector-findings">
      {% for finding in findings %}<a href="/library-health?category={{ finding.category|urlencode }}" class="workspace-inspector-finding {{ finding.severity }}"><span>{{ finding.severity|upper }}</span><strong>{{ finding.summary }}</strong></a>{% endfor %}
    </div>{% elif not missing_count and not duplicate_count %}<p class="workspace-inspector-empty-state">No active catalog issues are attached to this title.</p>{% endif %}
  </section>

  <section class="workspace-inspector-section" aria-labelledby="inspector-media-title">
    <div class="workspace-inspector-section-head"><span><small>ON DISK</small><strong id="inspector-media-title">Media</strong></span><em>{{ file_count }} file{% if file_count != 1 %}s{% endif %} · {{ total_size_display }}</em></div>
    {% if primary_file %}<div class="workspace-inspector-media-grid">
      <div><small>Resolution</small><strong>{{ primary_file.resolution_display or 'Unknown' }}</strong></div>
      <div><small>Video</small><strong>{{ primary_file.video_codec or 'Unknown' }}</strong></div>
      <div><small>Audio</small><strong>{{ primary_file.audio_codec or 'Unknown' }}{% if primary_file.audio_channels %} · {{ primary_file.audio_channels }}ch{% endif %}</strong></div>
      <div><small>Range</small><strong>{{ primary_file.dynamic_range or 'Unknown' }}</strong></div>
      <div><small>Container</small><strong>{{ primary_file.container or primary_file.extension or 'Unknown' }}</strong></div>
      <div><small>Runtime</small><strong>{{ total_runtime_display or primary_file.runtime_display or 'Unknown' }}</strong></div>
    </div>{% else %}<p class="workspace-inspector-empty-state">No indexed files are currently attached to this title.</p>{% endif %}
    {% if files %}<div class="workspace-inspector-files">
      {% for file in files[:5] %}<article>
        <div><strong>{{ file.filename }}</strong><small>{{ file.size_display }}{% if file.resolution_display %} · {{ file.resolution_display }}{% endif %}{% if file.dynamic_range %} · {{ file.dynamic_range }}{% endif %}</small></div>
        {% if file.edition_name or file.version_name or file.version_preferred %}<span class="workspace-file-version">{% if file.version_preferred %}Preferred{% endif %}{% if file.edition_name %} · {{ file.edition_name }}{% endif %}{% if file.version_name %} · {{ file.version_name }}{% endif %}</span>{% endif %}
      </article>{% endfor %}
      {% if files|length > 5 %}<small class="workspace-inspector-more">+ {{ files|length - 5 }} more indexed files</small>{% endif %}
    </div>{% endif %}
  </section>

  <section class="workspace-inspector-section" aria-labelledby="inspector-metadata-title">
    <div class="workspace-inspector-section-head"><span><small>IDENTITY</small><strong id="inspector-metadata-title">Metadata</strong></span>{% if title.metadata_provider %}<em>{{ title.metadata_provider }}</em>{% endif %}</div>
    <dl class="workspace-inspector-meta compact">
      {% if provider_ids %}<div><dt>IDs</dt><dd>{% for item in provider_ids %}<span>{{ item.label }} {{ item.value }}</span>{% if not loop.last %} · {% endif %}{% endfor %}</dd></div>{% endif %}
      <div><dt>Refresh</dt><dd>{% if metadata_queue %}{{ metadata_queue.status|title }}{% elif title.metadata_refreshed_at %}{{ title.metadata_refreshed_at }}{% else %}Not refreshed{% endif %}</dd></div>
      {% if title.genres %}<div><dt>Genres</dt><dd>{{ title.genres|replace(',', ', ') }}</dd></div>{% endif %}
      {% if title.imdb_rating %}<div><dt>IMDb</dt><dd>★ {{ '%.1f'|format(title.imdb_rating) }}{% if title.imdb_votes %} · {{ '{:,}'.format(title.imdb_votes) }} votes{% endif %}</dd></div>{% endif %}
    </dl>
  </section>

  <section class="workspace-inspector-section" aria-labelledby="inspector-organize-title">
    <div class="workspace-inspector-section-head"><span><small>PERSONAL</small><strong id="inspector-organize-title">Organization</strong></span><a href="/titles/{{ title.id }}/organize" data-organize-dialog>Manage →</a></div>
    {% if current_user.id > 0 and tags %}<div class="workspace-inspector-tags" aria-label="Custom tags">
      {% for tag in tags %}<button type="button" data-workspace-tag="{{ tag.id }}" class="workspace-inspector-tag{% if tag.selected %} active{% endif %}" aria-pressed="{{ 'true' if tag.selected else 'false' }}">{{ tag.name }}</button>{% endfor %}
    </div>{% elif current_user.id > 0 %}<p class="workspace-inspector-empty-state">No custom tags yet. Manage this title to create one.</p>{% endif %}
    <dl class="workspace-inspector-meta compact">
      {% if title.personal_rating is not none %}<div><dt>Your rating</dt><dd>{{ title.personal_rating }}/10</dd></div>{% endif %}
      {% if collections %}<div><dt>Collections</dt><dd>{{ collections|map(attribute='name')|join(', ') }}</dd></div>{% endif %}
      {% if libraries %}<div><dt>Libraries</dt><dd>{{ libraries|map(attribute='name')|join(', ') }}</dd></div>{% endif %}
      {% if title.sort_title %}<div><dt>Sort title</dt><dd>{{ title.sort_title }}</dd></div>{% endif %}
    </dl>
  </section>

  <div class="workspace-inspector-actions workspace-inspector-footer-actions">
    <a class="button primary" href="/titles/{{ title.id }}">Open full details</a>
    <div class="workspace-inspector-action-list">
      <a href="/titles/{{ title.id }}/organize" data-organize-dialog>Organize &amp; Tags</a>
      <a href="/titles/{{ title.id }}/libraries" data-organize-dialog>Custom Libraries</a>
      {% if current_user.is_librarian %}<a href="/titles/{{ title.id }}/tvdb">{{ 'Fix Match' if matched else 'Match' }}</a>{% endif %}
      {% if current_user.is_librarian and title.kind == 'movie' and primary_file %}<a href="/files/{{ primary_file.id }}/edition-version" data-organize-dialog>Edition &amp; Version</a>{% endif %}
    </div>
  </div>
  <p class="workspace-inspector-hint">Single-click inspects · Ctrl/Cmd-click selects · Shift-click selects a range · ↑/↓ moves · Enter opens details · Esc closes</p>
</div>
'''
(ROOT / "app/templates/_workspace_inspector.html").write_text(partial, encoding="utf-8")


# 3. Replace W1 DOM-scraping Inspector with the W2 server-backed controller.
workspace_js_path = ROOT / "app/static/workspace.js"
script = workspace_js_path.read_text(encoding="utf-8")
start = script.index("  const enhanceLibraryInspector = () => {")
end = script.index("\n  const enhanceCreditHoverCards = () => {", start)
new_inspector_js = r'''  const enhanceLibraryInspector = () => {
    const libraryTable = document.querySelector(".library-table");
    const coverLibrary = document.getElementById("cover-library");
    if (!libraryTable && !coverLibrary) return;

    const inspector = document.createElement("aside");
    inspector.id = "workspace-inspector";
    inspector.className = "workspace-inspector";
    inspector.hidden = true;
    inspector.setAttribute("aria-label", "Selected library item");
    inspector.innerHTML = `
      <div class="workspace-inspector-head">
        <span>Inspector</span>
        <button class="workspace-inspector-close" type="button" aria-label="Close inspector">×</button>
      </div>
      <div class="workspace-inspector-body"></div>`;
    document.body.append(inspector);

    const body = inspector.querySelector(".workspace-inspector-body");
    const close = inspector.querySelector(".workspace-inspector-close");
    let selected = null;
    let selectedTitleId = "";
    let detailHref = "";
    let requestController = null;
    let rangeAnchorId = "";

    const interactive = (target) => target.closest("input, button, summary, details, form, select, textarea, .item-action-menu");
    const titleIdFor = (item) => {
      if (!item) return "";
      if (item.dataset.workspaceTitleId) return item.dataset.workspaceTitleId;
      const href = item.querySelector(".title-link, .cover-card-link")?.getAttribute("href") || "";
      return href.match(/\/titles\/(\d+)/)?.[1] || "";
    };
    const visibleItems = () => {
      const selector = libraryTable && !libraryTable.hidden ? ".library-title-row" : ".cover-card";
      return [...document.querySelectorAll(selector)].filter(item => titleIdFor(item));
    };
    const itemForTitle = (titleId) => visibleItems().find(item => titleIdFor(item) === String(titleId))
      || document.querySelector(`[data-workspace-title-id="${CSS.escape(String(titleId))}"]`);
    const choiceFor = (item) => item?.querySelector(".library-title-choice");

    const updateInspectorUrl = (titleId, mode = "push") => {
      const url = new URL(window.location.href);
      if (titleId) url.searchParams.set("inspect", titleId);
      else url.searchParams.delete("inspect");
      const state = {...(history.state || {}), workspaceInspectorTitleId: titleId || null};
      history[mode === "replace" ? "replaceState" : "pushState"](state, "", url.pathname + url.search + url.hash);
    };

    const closeInspector = ({historyMode = "replace"} = {}) => {
      requestController?.abort();
      selected?.classList.remove("workspace-selected");
      selected = null;
      selectedTitleId = "";
      detailHref = "";
      document.body.classList.remove("workspace-inspector-open");
      window.setTimeout(() => { inspector.hidden = true; }, 190);
      if (historyMode === "back" && history.state?.workspaceInspectorTitleId) history.back();
      else if (historyMode === "replace") updateInspectorUrl("", "replace");
    };

    const renderState = (message, className = "") => {
      body.innerHTML = `<div class="workspace-inspector-state ${className}"><span></span><p></p></div>`;
      body.querySelector("p").textContent = message;
    };

    const postInspectorAction = async (url, csrf) => {
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {"X-CSRF-Token": csrf, "Content-Type": "application/x-www-form-urlencoded"},
        body: "",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    };

    const syncFavoriteUI = (titleId, favorite) => {
      document.querySelectorAll(`[data-workspace-title-id="${CSS.escape(String(titleId))}"]`).forEach(item => {
        item.querySelectorAll(".cover-favorite-button, .favorite-action").forEach(control => control.classList.toggle("active", favorite));
        const star = item.querySelector(".title-organization .favorite-star");
        if (star) star.classList.toggle("active", favorite);
      });
    };

    const enhanceInspectorActions = () => {
      const panel = body.querySelector("[data-workspace-inspector-panel]");
      if (!panel) return;
      detailHref = panel.dataset.detailHref || detailHref;
      const csrf = panel.querySelector("[data-workspace-csrf]")?.value || "";
      const favorite = panel.querySelector("[data-workspace-favorite]");
      favorite?.addEventListener("click", async () => {
        favorite.disabled = true;
        try {
          const data = await postInspectorAction(`/api/titles/${selectedTitleId}/favorite`, csrf);
          favorite.classList.toggle("active", data.favorite);
          favorite.setAttribute("aria-pressed", String(Boolean(data.favorite)));
          favorite.title = data.favorite ? "Remove from favorites" : "Add to favorites";
          syncFavoriteUI(selectedTitleId, Boolean(data.favorite));
        } catch (_error) {
          favorite.classList.add("save-error");
          window.setTimeout(() => favorite.classList.remove("save-error"), 1200);
        } finally {
          favorite.disabled = false;
        }
      });
      panel.querySelectorAll("[data-workspace-tag]").forEach(tag => {
        tag.addEventListener("click", async () => {
          tag.disabled = true;
          try {
            const data = await postInspectorAction(
              `/api/titles/${selectedTitleId}/tags/${tag.dataset.workspaceTag}`, csrf,
            );
            tag.classList.toggle("active", data.selected);
            tag.setAttribute("aria-pressed", String(Boolean(data.selected)));
          } catch (_error) {
            tag.classList.add("save-error");
            window.setTimeout(() => tag.classList.remove("save-error"), 1200);
          } finally {
            tag.disabled = false;
          }
        });
      });
    };

    const inspectTitle = async (titleId, item = null, historyMode = "push") => {
      if (!titleId) return;
      requestController?.abort();
      requestController = new AbortController();
      selected?.classList.remove("workspace-selected");
      selected = item || itemForTitle(titleId);
      selected?.classList.add("workspace-selected");
      selectedTitleId = String(titleId);
      detailHref = `/titles/${titleId}`;
      inspector.hidden = false;
      renderState("Loading title details…", "loading");
      requestAnimationFrame(() => document.body.classList.add("workspace-inspector-open"));
      if (historyMode !== "none") updateInspectorUrl(titleId, historyMode);
      try {
        const response = await fetch(`/library/inspector/${encodeURIComponent(titleId)}`, {
          credentials: "same-origin",
          cache: "no-store",
          signal: requestController.signal,
          headers: {"X-Workspace-Inspector": "1"},
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        body.innerHTML = await response.text();
        enhanceInspectorActions();
      } catch (error) {
        if (error.name !== "AbortError") renderState("Inspector details could not be loaded. Open the full title page or try again.", "error");
      }
    };

    const toggleSelection = (item, force = null) => {
      const choice = choiceFor(item);
      if (!choice) return;
      choice.checked = force === null ? !choice.checked : force;
      choice.dispatchEvent(new Event("change", {bubbles: true}));
      rangeAnchorId = titleIdFor(item);
    };

    const selectRange = (item) => {
      const items = visibleItems();
      const targetId = titleIdFor(item);
      const anchorId = rangeAnchorId || selectedTitleId || targetId;
      const start = items.findIndex(candidate => titleIdFor(candidate) === anchorId);
      const finish = items.findIndex(candidate => titleIdFor(candidate) === targetId);
      if (start < 0 || finish < 0) {
        toggleSelection(item, true);
        return;
      }
      const [low, high] = start < finish ? [start, finish] : [finish, start];
      items.slice(low, high + 1).forEach(candidate => {
        const choice = choiceFor(candidate);
        if (choice && !choice.checked) {
          choice.checked = true;
          choice.dispatchEvent(new Event("change", {bubbles: true}));
        }
      });
      rangeAnchorId = targetId;
    };

    document.addEventListener("click", (event) => {
      const item = event.target.closest(".library-title-row, .cover-card");
      if (!item || interactive(event.target)) return;
      const titleId = titleIdFor(item);
      if (!titleId) return;
      if (event.metaKey || event.ctrlKey) {
        event.preventDefault();
        toggleSelection(item);
        return;
      }
      if (event.shiftKey) {
        event.preventDefault();
        selectRange(item);
        return;
      }
      const titleLink = event.target.closest(".title-link, .cover-card-link");
      if (titleLink || !interactive(event.target)) {
        event.preventDefault();
        rangeAnchorId = titleId;
        inspectTitle(titleId, item, "push");
      }
    });

    document.addEventListener("dblclick", (event) => {
      const item = event.target.closest(".library-title-row, .cover-card");
      if (!item || interactive(event.target)) return;
      const titleId = titleIdFor(item);
      if (titleId) window.location.assign(`/titles/${titleId}`);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && selectedTitleId) {
        event.preventDefault();
        closeInspector({historyMode: history.state?.workspaceInspectorTitleId ? "back" : "replace"});
        return;
      }
      if (event.key === "Enter" && selectedTitleId && detailHref && !event.target.matches("input,textarea,select,button,a")) {
        window.location.assign(detailHref);
        return;
      }
      if ((event.key === "ArrowDown" || event.key === "ArrowUp") && selectedTitleId && !event.target.matches("input,textarea,select")) {
        const items = visibleItems();
        const current = items.findIndex(item => titleIdFor(item) === selectedTitleId);
        if (current < 0 || !items.length) return;
        event.preventDefault();
        const offset = event.key === "ArrowDown" ? 1 : -1;
        const next = Math.min(items.length - 1, Math.max(0, current + offset));
        if (next !== current) inspectTitle(titleIdFor(items[next]), items[next], "replace");
      }
    });

    close.addEventListener("click", () => closeInspector({historyMode: history.state?.workspaceInspectorTitleId ? "back" : "replace"}));
    window.addEventListener("popstate", () => {
      const titleId = new URL(window.location.href).searchParams.get("inspect");
      if (titleId) inspectTitle(titleId, itemForTitle(titleId), "none");
      else if (selectedTitleId) closeInspector({historyMode: "none"});
    });
    document.addEventListener("infomancer:library-results-updated", () => {
      if (selectedTitleId && !itemForTitle(selectedTitleId)) closeInspector({historyMode: "replace"});
    });

    const initialTitleId = new URL(window.location.href).searchParams.get("inspect");
    if (initialTitleId) inspectTitle(initialTitleId, itemForTitle(initialTitleId), "none");
  };
'''
script = script[:start] + new_inspector_js + script[end:]
workspace_js_path.write_text(script, encoding="utf-8")


# 4. Give rows/cards stable IDs and preserve bulk selection through reload/live filtering.
replace_once(
    "app/templates/library.html",
    '<article class="cover-card" id="title-{{ row.id }}">',
    '<article class="cover-card" id="title-{{ row.id }}" data-workspace-title-id="{{ row.id }}">',
)
replace_once(
    "app/templates/library.html",
    '<tr class="library-title-row" id="title-{{ row.id }}">',
    '<tr class="library-title-row" id="title-{{ row.id }}" data-workspace-title-id="{{ row.id }}">',
)
replace_once(
    "app/templates/library.html",
    '    let selectionOrder = [];\n    const savedLibraryView = localStorage.getItem("infomancer-library-view");',
    '''    const selectionStorageKey = `infomancer-library-selection:${window.location.pathname}`;\n    let selectionOrder = [];\n    try {\n      const storedSelection = JSON.parse(sessionStorage.getItem(selectionStorageKey) || "[]");\n      if (Array.isArray(storedSelection)) selectionOrder = storedSelection.filter(value => /^\\d+$/.test(String(value))).map(String).slice(0, 1000);\n    } catch (_error) {}\n    const restoreStoredSelection = () => {\n      const selected = new Set(selectionOrder);\n      document.querySelectorAll(".library-title-choice").forEach(choice => {\n        choice.checked = selected.has(choice.value);\n      });\n    };\n    restoreStoredSelection();\n    const savedLibraryView = localStorage.getItem("infomancer-library-view");''',
)
replace_once(
    "app/templates/library.html",
    '''      selected.forEach(choice => {\n        if (!selectionOrder.includes(choice.value)) selectionOrder.push(choice.value);\n      });\n      const unmatched = selected.filter(choice => choice.dataset.matched !== "true");''',
    '''      selected.forEach(choice => {\n        if (!selectionOrder.includes(choice.value)) selectionOrder.push(choice.value);\n      });\n      try { sessionStorage.setItem(selectionStorageKey, JSON.stringify(selectionOrder)); } catch (_error) {}\n      const unmatched = selected.filter(choice => choice.dataset.matched !== "true");''',
)
replace_once(
    "app/templates/library.html",
    '''        if (coverLibrary && replacementCovers) coverLibrary.replaceChildren(...replacementCovers.childNodes);\n        updateTitleSelection();''',
    '''        if (coverLibrary && replacementCovers) coverLibrary.replaceChildren(...replacementCovers.childNodes);\n        restoreStoredSelection();\n        updateTitleSelection();''',
)
replace_once(
    "app/templates/library.html",
    '''        history.replaceState({}, "", url.pathname + url.search);\n        const filters =''',
    '''        url.searchParams.delete("inspect");\n        history.replaceState({...history.state, workspaceInspectorTitleId: null}, "", url.pathname + url.search);\n        document.dispatchEvent(new CustomEvent("infomancer:library-results-updated"));\n        const filters =''',
)


# 5. W2 styling.
css_path = ROOT / "app/static/workspace.css"
css = css_path.read_text(encoding="utf-8")
css = css.replace("--im-inspector-width: min(380px, 34vw);", "--im-inspector-width: min(440px, 38vw);", 1)
css += r'''

/* W2: server-backed Inspector */
.workspace-inspector-body {
  padding: 18px 20px 26px;
}

.workspace-inspector-state {
  display: grid;
  min-height: 180px;
  place-items: center;
  align-content: center;
  gap: 12px;
  color: var(--muted);
  text-align: center;
}

.workspace-inspector-state.loading span {
  width: 28px;
  height: 28px;
  border: 2px solid #354454;
  border-top-color: var(--cyan);
  border-radius: 50%;
  animation: workspace-inspector-spin .8s linear infinite;
}

.workspace-inspector-state.error { color: #e7a8a8; }
@keyframes workspace-inspector-spin { to { transform: rotate(360deg); } }

.workspace-inspector-panel {
  display: grid;
  gap: 14px;
}

.workspace-inspector-summary {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr) auto;
  gap: 14px;
  align-items: start;
}

.workspace-inspector-summary .workspace-inspector-art {
  width: 92px;
  margin: 0;
}

.workspace-inspector-art-placeholder {
  display: grid;
  width: 100%;
  height: 100%;
  place-items: center;
  color: var(--muted);
  font: 700 34px/1 var(--serif);
}

.workspace-inspector-identity { min-width: 0; padding-top: 3px; }
.workspace-inspector-identity .workspace-inspector-title { font-size: 26px; }
.workspace-inspector-subtitle {
  margin: 6px 0 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-inspector-statuses {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 10px;
}

.workspace-status {
  padding: 3px 6px;
  border: 1px solid #33414f;
  border-radius: 999px;
  color: #a9b6c4;
  background: rgba(255,255,255,.025);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: .05em;
  text-transform: uppercase;
}
.workspace-status.good { border-color: rgba(185,245,66,.28); color: var(--lime); }
.workspace-status.warning { border-color: rgba(244,190,79,.34); color: #f1c675; }

.workspace-inspector-favorite {
  display: grid;
  min-width: 48px;
  place-items: center;
  gap: 1px;
  padding: 7px 8px;
  border: 1px solid #344351;
  border-radius: var(--im-radius-sm);
  background: rgba(255,255,255,.02);
  color: #83909d;
}
.workspace-inspector-favorite span { font-size: 19px; line-height: 1; }
.workspace-inspector-favorite small { font-size: 8px; text-transform: uppercase; }
.workspace-inspector-favorite.active { border-color: rgba(185,245,66,.34); color: var(--lime); background: rgba(185,245,66,.06); }
.workspace-inspector-favorite.save-error,
.workspace-inspector-tag.save-error { animation: workspace-save-error .3s ease 2; }
@keyframes workspace-save-error { 50% { transform: translateX(3px); } }

.workspace-inspector-overview {
  margin: 0;
  color: #b9c4cf;
  font-size: 12px;
  line-height: 1.55;
}

.workspace-inspector-section {
  padding: 14px;
  border: 1px solid #283642;
  border-radius: var(--im-radius-md);
  background: rgba(9,14,19,.48);
}

.workspace-inspector-section-head {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 11px;
}
.workspace-inspector-section-head > span { display: grid; gap: 2px; }
.workspace-inspector-section-head small {
  color: var(--cyan);
  font-size: 8px;
  font-weight: 900;
  letter-spacing: .14em;
}
.workspace-inspector-section-head strong { font-size: 14px; }
.workspace-inspector-section-head a,
.workspace-inspector-section-head em {
  color: var(--muted);
  font-size: 10px;
  font-style: normal;
  text-decoration: none;
}
.workspace-inspector-section-head a:hover { color: var(--lime); }

.workspace-inspector-stat-grid,
.workspace-inspector-media-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}
.workspace-inspector-stat-grid > div,
.workspace-inspector-media-grid > div {
  min-width: 0;
  padding: 9px;
  border-radius: var(--im-radius-xs);
  background: rgba(255,255,255,.025);
}
.workspace-inspector-stat-grid strong { display: block; font-size: 20px; }
.workspace-inspector-stat-grid span,
.workspace-inspector-media-grid small { color: var(--muted); font-size: 9px; }
.workspace-inspector-media-grid strong {
  display: block;
  margin-top: 2px;
  overflow: hidden;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-inspector-findings,
.workspace-inspector-files { display: grid; gap: 5px; margin-top: 9px; }
.workspace-inspector-finding {
  display: grid;
  grid-template-columns: 58px minmax(0,1fr);
  gap: 7px;
  align-items: center;
  padding: 7px 8px;
  border-radius: var(--im-radius-xs);
  color: var(--text);
  background: rgba(255,255,255,.025);
  text-decoration: none;
}
.workspace-inspector-finding span { color: var(--muted); font-size: 8px; font-weight: 900; }
.workspace-inspector-finding.critical span { color: #f18a8a; }
.workspace-inspector-finding.warning span { color: #f1c675; }
.workspace-inspector-finding strong { overflow: hidden; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }

.workspace-inspector-files article {
  display: grid;
  grid-template-columns: minmax(0,1fr) auto;
  gap: 8px;
  align-items: center;
  padding: 8px 9px;
  border-radius: var(--im-radius-xs);
  background: rgba(255,255,255,.025);
}
.workspace-inspector-files article > div { min-width: 0; }
.workspace-inspector-files strong { display:block; overflow:hidden; font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.workspace-inspector-files small { color:var(--muted); font-size:9px; }
.workspace-file-version { max-width: 120px; color:var(--cyan); font-size:8px; text-align:right; }
.workspace-inspector-more { color: var(--muted); font-size: 9px; }

.workspace-inspector-meta.compact { margin: 0; border-top: 0; }
.workspace-inspector-meta.compact div { grid-template-columns: 72px minmax(0,1fr); padding: 7px 0; }
.workspace-inspector-meta.compact div:last-child { border-bottom: 0; padding-bottom: 0; }
.workspace-inspector-meta.compact dt { font-size: 9px; }
.workspace-inspector-meta.compact dd { font-size: 10px; }

.workspace-inspector-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.workspace-inspector-tag {
  padding: 5px 8px;
  border: 1px solid #344351;
  border-radius: 999px;
  background: rgba(255,255,255,.02);
  color: #aab6c2;
  font-size: 9px;
}
.workspace-inspector-tag.active { border-color: rgba(81,214,230,.38); background: rgba(81,214,230,.08); color: var(--cyan); }
.workspace-inspector-empty-state { margin: 6px 0 0; color: var(--muted); font-size: 10px; }
.workspace-inspector-footer-actions { margin-top: 2px; }

@media (max-width: 900px) {
  .workspace-inspector { max-height: 82vh; }
  .workspace-inspector-summary { grid-template-columns: 74px minmax(0, 1fr) auto; }
  .workspace-inspector-summary .workspace-inspector-art { width: 74px; }
}
'''
css_path.write_text(css, encoding="utf-8")


# 6. Authorization contract: these are reviewed personal self-service mutations.
auth_test = ROOT / "tests/test_route_authorization.py"
auth = auth_test.read_text(encoding="utf-8")
old = '''            re.fullmatch(r"/titles/\\{title_id\\}/(?:favorite|organize)", path)\n            or re.fullmatch(r"/files/\\{file_id\\}/favorite", path)\n'''
new = '''            re.fullmatch(r"/titles/\\{title_id\\}/(?:favorite|organize)", path)\n            or re.fullmatch(r"/api/titles/\\{title_id\\}/(?:favorite|tags/\\{tag_id\\})", path)\n            or re.fullmatch(r"/files/\\{file_id\\}/favorite", path)\n'''
if old not in auth:
    raise RuntimeError("Authorization test anchor missing")
auth_test.write_text(auth.replace(old, new, 1), encoding="utf-8")


# 7. W2 contract tests.
workspace_test = ROOT / "tests/test_workspace_ui.py"
test_text = workspace_test.read_text(encoding="utf-8")
anchor = '''    def test_detail_workspace_adds_local_people_previews(self):\n'''
w2_test = '''    def test_w2_inspector_is_server_backed_and_history_aware(self):\n        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")\n        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")\n        library = (ROOT / "app" / "templates" / "library.html").read_text(encoding="utf-8")\n        partial = (ROOT / "app" / "templates" / "_workspace_inspector.html").read_text(encoding="utf-8")\n        routes = (ROOT / "app" / "routes" / "library.py").read_text(encoding="utf-8")\n        self.assertIn('/library/inspector/{title_id}', routes)\n        self.assertIn('/api/titles/{title_id}/favorite', routes)\n        self.assertIn('/api/titles/{title_id}/tags/{tag_id}', routes)\n        self.assertIn('fetch(`/library/inspector/', script)\n        self.assertIn('workspaceInspectorTitleId', script)\n        self.assertIn('popstate', script)\n        self.assertIn('event.shiftKey', script)\n        self.assertIn('event.metaKey || event.ctrlKey', script)\n        self.assertIn('infomancer-library-selection:', library)\n        self.assertIn('data-workspace-title-id', library)\n        self.assertIn('Health &amp; attention', partial)\n        self.assertIn('Edition &amp; Version', partial)\n        self.assertIn('data-workspace-tag', partial)\n        self.assertIn('server-backed Inspector', styles)\n\n'''
if anchor not in test_text:
    raise RuntimeError("Workspace test anchor missing")
workspace_test.write_text(test_text.replace(anchor, w2_test + anchor, 1), encoding="utf-8")

functional_test = r'''import os
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
from app.app_settings import AppSettings
from app.auth import AuthService
from app.db import Database
from app.engagement import EngagementService


class WorkspaceInspectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = replace(
            main.settings,
            database=Path(self.temporary.name) / "inspector.db",
            auth_mode="local",
            cookie_secure="false",
            sandbox=True,
            media_browse_roots=(Path(self.temporary.name),),
        )
        database = Database(settings.database)
        database.initialize()
        self.original = (main.db, main.settings, main.auth_service, main.app_settings, main.engagement)
        main.db, main.settings = database, settings
        main.auth_service = AuthService(database, settings)
        main.app_settings = AppSettings(database, settings.search_url_template)
        main.engagement = EngagementService(database)
        main.engagement.seed_official()
        self.database = database
        self.user = main.auth_service.create_user(
            "inspector-tester", "inspector@example.com", "Inspector Tester", "x", role="librarian",
        )
        with database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label,health_status) VALUES ('/movies','movie','Movies','healthy')"
            ).lastrowid
            self.title_id = conn.execute(
                """INSERT INTO titles(
                     root_id,kind,title,year,folder_path,tmdb_id,metadata_title,metadata_year,
                     metadata_provider,metadata_status,overview,genres,imdb_rating,imdb_votes
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (root_id, "movie", "Inspector Film", 2024, "/movies/inspector-film", "42",
                 "Inspector Film", 2024, "tmdb", "complete", "A useful inspector test.",
                 "Drama,Thriller", 8.2, 12345),
            ).lastrowid
            self.file_id = conn.execute(
                """INSERT INTO files(
                     title_id,path,filename,extension,size_bytes,runtime_seconds,width,height,
                     video_codec,audio_codec,audio_channels,container,dynamic_range,edition_name,
                     version_name,identity_confirmed,version_preferred,seen_scan
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (self.title_id, "/movies/inspector-film/movie.mkv", "movie.mkv", ".mkv",
                 8_000_000_000, 7200, 3840, 2160, "hevc", "eac3", 6, "matroska", "HDR10",
                 "Director's Cut", "4K", 1, 1, "test"),
            ).lastrowid
            self.tag_id = conn.execute(
                "INSERT INTO user_tags(user_id,name) VALUES (?,?)", (self.user.id, "Keep"),
            ).lastrowid
            conn.execute(
                """INSERT INTO mie_findings(
                     fingerprint,rule_key,category,severity,title_id,summary,explanation,recommendation
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                ("w2-test", "w2", "quality", "warning", self.title_id,
                 "Quality deserves review", "Test evidence", "Inspect the file"),
            )
        self.client = TestClient(main.app, follow_redirects=False)
        login = self.client.get("/login")
        token = re.search(r'name="preauth_token" value="([^"]+)', login.text).group(1)
        signed_in = self.client.post("/login", data={
            "preauth_token": token, "identity": "inspector-tester", "password": "x", "next": "/",
        })
        self.assertEqual(signed_in.status_code, 303)
        session = main.auth_service.session_from_token(self.client.cookies["infomancer_session"])
        self.csrf = session.csrf_token

    def tearDown(self):
        self.client.close()
        main.db, main.settings, main.auth_service, main.app_settings, main.engagement = self.original
        self.temporary.cleanup()

    def test_inspector_renders_catalog_health_media_and_metadata(self):
        response = self.client.get(f"/library/inspector/{self.title_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        for expected in (
            "Inspector Film", "Health &amp; attention", "Quality deserves review",
            "3840×2160", "HDR10", "Director's Cut", "TMDB 42", "Drama, Thriller",
        ):
            self.assertIn(expected, response.text)

    def test_inspector_personal_actions_update_without_redirect(self):
        favorite = self.client.post(
            f"/api/titles/{self.title_id}/favorite", headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(favorite.status_code, 200)
        self.assertTrue(favorite.json()["favorite"])
        tagged = self.client.post(
            f"/api/titles/{self.title_id}/tags/{self.tag_id}", headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(tagged.status_code, 200)
        self.assertTrue(tagged.json()["selected"])
        with self.database.connect() as conn:
            state = conn.execute(
                "SELECT favorite FROM user_title_state WHERE user_id=? AND title_id=?",
                (self.user.id, self.title_id),
            ).fetchone()
            tag = conn.execute(
                "SELECT 1 FROM title_tags WHERE title_id=? AND tag_id=?", (self.title_id, self.tag_id),
            ).fetchone()
        self.assertEqual(state["favorite"], 1)
        self.assertIsNotNone(tag)

    def test_missing_inspector_title_is_404(self):
        self.assertEqual(self.client.get("/library/inspector/999999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
'''
(ROOT / "tests/test_workspace_inspector.py").write_text(functional_test, encoding="utf-8")


# 8. Record W2 as a completed Workspace stage after validation lands.
doc_path = ROOT / "docs/WORKSPACE.md"
doc = doc_path.read_text(encoding="utf-8")
doc = doc.replace(
    '2. **W2 Library**: server-backed inspector partials, richer file/edition/quality information, history-aware selection state, instantaneous favorite/tag actions.',
    '2. **W2 Library (complete)**: server-backed inspector partials, richer file/edition/quality information, history-aware selection state, instantaneous favorite/tag actions.',
    1,
)
doc += '''\n## W2 Library Inspector\n\nW2 replaces the W1 DOM-scraping Inspector with a dedicated read-only `/library/inspector/{title_id}` partial. The Inspector now exposes catalog identity, provider IDs, source health, MIE findings, missing episodes, duplicate review counts, media characteristics, editions/versions, organization state, and indexed file details without performing provider network work on GET. Personal favorite and existing-tag toggles use small CSRF-protected JSON mutations while the full server-rendered organization flows remain available as fallbacks. Library title selection persists for the current library view across reloads and live result replacement, and Inspector state is represented by the `inspect` query parameter so browser history can restore the current inspected title.\n'''
doc_path.write_text(doc, encoding="utf-8")

print("W2 Workspace patch applied")

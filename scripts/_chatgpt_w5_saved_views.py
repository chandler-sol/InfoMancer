from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def add_saved_views_service() -> None:
    path = ROOT / "app" / "saved_views.py"
    if path.exists():
        raise RuntimeError("app/saved_views.py already exists")
    path.write_text('''from __future__ import annotations\n\nimport re\nfrom urllib.parse import parse_qsl, urlencode\n\nfrom .db import Database\n\n\nclass SavedViewError(ValueError):\n    pass\n\n\nclass SavedViewService:\n    ALLOWED_PATHS = {"/library", "/movies", "/shows"}\n    ALLOWED_SORTS = {\n        "title", "release_new", "release_old", "rating", "personal_rating",\n        "date_added", "runtime", "resolution", "bitrate", "file_size",\n        "favorites", "random",\n    }\n    MAX_VIEWS = 50\n    MAX_PINNED = 8\n\n    def __init__(self, database: Database) -> None:\n        self.database = database\n\n    @staticmethod\n    def _clean_name(name: str) -> str:\n        cleaned = " ".join(name.strip().split())[:60]\n        if not cleaned:\n            raise SavedViewError("Enter a name for this saved view.")\n        return cleaned\n\n    @classmethod\n    def normalize_target(cls, path: str, query_string: str) -> tuple[str, str]:\n        target_path = path if path in cls.ALLOWED_PATHS else "/library"\n        try:\n            raw = dict(parse_qsl(query_string, keep_blank_values=False, max_num_fields=30))\n        except ValueError as exc:\n            raise SavedViewError("That library view contains too many filter values.") from exc\n\n        normalized: dict[str, str] = {}\n        q = str(raw.get("q", "")).strip()[:200]\n        if q:\n            normalized["q"] = q\n        letter = str(raw.get("letter", "")).upper()\n        if letter == "#" or (len(letter) == 1 and letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"):\n            normalized["letter"] = letter\n        for key, maximum in (("genre", 100), ("title_type", 100), ("person_name", 200)):\n            value = str(raw.get(key, "")).strip()[:maximum]\n            if value:\n                normalized[key] = value\n        root = str(raw.get("root", ""))\n        if root.isdigit() and int(root) > 0:\n            normalized["root"] = str(int(root))\n        person = str(raw.get("person", ""))\n        if re.fullmatch(r"nm\\d+", person):\n            normalized["person"] = person\n        credit_role = str(raw.get("credit_role", ""))\n        if credit_role in {"actor", "director", "writer"}:\n            normalized["credit_role"] = credit_role\n        match = str(raw.get("match", ""))\n        if match in {"matched", "unmatched"}:\n            normalized["match"] = match\n        gaps = str(raw.get("gaps", ""))\n        if target_path != "/movies" and gaps in {"missing", "complete"}:\n            normalized["gaps"] = gaps\n        if raw.get("favorite") == "favorites":\n            normalized["favorite"] = "favorites"\n        tag = str(raw.get("tag", ""))\n        if tag.isdigit() and int(tag) > 0:\n            normalized["tag"] = str(int(tag))\n        sort = str(raw.get("sort", ""))\n        if sort in cls.ALLOWED_SORTS and sort != "title":\n            normalized["sort"] = sort\n        return target_path, urlencode(normalized)\n\n    @staticmethod\n    def _view(row) -> dict:\n        item = dict(row)\n        item["href"] = item["path"] + (f'?{item["query_string"]}' if item["query_string"] else "")\n        item["pinned"] = bool(item["pinned"])\n        return item\n\n    def list_for_user(self, user_id: int, *, pinned_only: bool = False) -> list[dict]:\n        if user_id <= 0:\n            return []\n        where = " AND pinned=1" if pinned_only else ""\n        with self.database.connect() as conn:\n            rows = conn.execute(\n                f"""SELECT id,user_id,name,path,query_string,pinned,created_at,updated_at\n                    FROM user_saved_views WHERE user_id=?{where}\n                    ORDER BY pinned DESC,name COLLATE NOCASE,id""",\n                (user_id,),\n            ).fetchall()\n        return [self._view(row) for row in rows]\n\n    def save(\n        self, user_id: int, name: str, path: str, query_string: str, *, pinned: bool = False,\n    ) -> tuple[dict, bool]:\n        if user_id <= 0:\n            raise SavedViewError("Saved views require a signed-in account.")\n        cleaned = self._clean_name(name)\n        target_path, target_query = self.normalize_target(path, query_string)\n        with self.database.connect() as conn:\n            existing = conn.execute(\n                "SELECT * FROM user_saved_views WHERE user_id=? AND name=? COLLATE NOCASE",\n                (user_id, cleaned),\n            ).fetchone()\n            if not existing:\n                total = int(conn.execute(\n                    "SELECT COUNT(*) FROM user_saved_views WHERE user_id=?", (user_id,)\n                ).fetchone()[0])\n                if total >= self.MAX_VIEWS:\n                    raise SavedViewError(\n                        f"You can save up to {self.MAX_VIEWS} Library views. Delete one before saving another."\n                    )\n            if pinned and not (existing and existing["pinned"]):\n                pinned_count = int(conn.execute(\n                    "SELECT COUNT(*) FROM user_saved_views WHERE user_id=? AND pinned=1",\n                    (user_id,),\n                ).fetchone()[0])\n                if pinned_count >= self.MAX_PINNED:\n                    raise SavedViewError(\n                        f"Pin up to {self.MAX_PINNED} saved views. Unpin one before adding another."\n                    )\n            if existing:\n                conn.execute(\n                    """UPDATE user_saved_views SET name=?,path=?,query_string=?,pinned=?,\n                         updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?""",\n                    (cleaned, target_path, target_query, int(pinned), existing["id"], user_id),\n                )\n                view_id = int(existing["id"])\n                created = False\n            else:\n                view_id = int(conn.execute(\n                    """INSERT INTO user_saved_views(user_id,name,path,query_string,pinned)\n                       VALUES (?,?,?,?,?)""",\n                    (user_id, cleaned, target_path, target_query, int(pinned)),\n                ).lastrowid)\n                created = True\n            row = conn.execute(\n                "SELECT * FROM user_saved_views WHERE id=? AND user_id=?", (view_id, user_id)\n            ).fetchone()\n        return self._view(row), created\n\n    def rename(self, user_id: int, view_id: int, name: str) -> dict:\n        cleaned = self._clean_name(name)\n        try:\n            with self.database.connect() as conn:\n                result = conn.execute(\n                    """UPDATE user_saved_views SET name=?,updated_at=CURRENT_TIMESTAMP\n                       WHERE id=? AND user_id=?""",\n                    (cleaned, view_id, user_id),\n                )\n                if not result.rowcount:\n                    raise SavedViewError("That saved view no longer exists.")\n                row = conn.execute(\n                    "SELECT * FROM user_saved_views WHERE id=? AND user_id=?",\n                    (view_id, user_id),\n                ).fetchone()\n        except Exception as exc:\n            if exc.__class__.__name__ == "IntegrityError":\n                raise SavedViewError(f'A saved view named "{cleaned}" already exists.') from exc\n            raise\n        return self._view(row)\n\n    def toggle_pin(self, user_id: int, view_id: int) -> dict:\n        with self.database.connect() as conn:\n            row = conn.execute(\n                "SELECT * FROM user_saved_views WHERE id=? AND user_id=?",\n                (view_id, user_id),\n            ).fetchone()\n            if not row:\n                raise SavedViewError("That saved view no longer exists.")\n            pinned = not bool(row["pinned"])\n            if pinned:\n                pinned_count = int(conn.execute(\n                    "SELECT COUNT(*) FROM user_saved_views WHERE user_id=? AND pinned=1",\n                    (user_id,),\n                ).fetchone()[0])\n                if pinned_count >= self.MAX_PINNED:\n                    raise SavedViewError(\n                        f"Pin up to {self.MAX_PINNED} saved views. Unpin one before adding another."\n                    )\n            conn.execute(\n                "UPDATE user_saved_views SET pinned=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",\n                (int(pinned), view_id),\n            )\n            updated = conn.execute("SELECT * FROM user_saved_views WHERE id=?", (view_id,)).fetchone()\n        return self._view(updated)\n\n    def delete(self, user_id: int, view_id: int) -> str:\n        with self.database.connect() as conn:\n            row = conn.execute(\n                "SELECT name FROM user_saved_views WHERE id=? AND user_id=?",\n                (view_id, user_id),\n            ).fetchone()\n            if not row:\n                raise SavedViewError("That saved view no longer exists.")\n            conn.execute(\n                "DELETE FROM user_saved_views WHERE id=? AND user_id=?", (view_id, user_id)\n            )\n        return str(row["name"])\n''', encoding="utf-8")


def patch_db() -> None:
    path = "app/db.py"
    text = read(path)
    anchor = '''CREATE TABLE IF NOT EXISTS user_search_history (\n    id INTEGER PRIMARY KEY,\n    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n    query TEXT NOT NULL COLLATE NOCASE,\n    searched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n    UNIQUE(user_id, query)\n);\n'''
    addition = anchor + '''\nCREATE TABLE IF NOT EXISTS user_saved_views (\n    id INTEGER PRIMARY KEY,\n    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n    name TEXT NOT NULL COLLATE NOCASE,\n    path TEXT NOT NULL CHECK(path IN ('/library','/movies','/shows')),\n    query_string TEXT NOT NULL DEFAULT '',\n    pinned INTEGER NOT NULL DEFAULT 0,\n    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n    UNIQUE(user_id, name)\n);\n'''
    text = replace_once(text, anchor, addition, "saved views schema")
    idx_anchor = '''CREATE INDEX IF NOT EXISTS idx_user_search_history_recent\n    ON user_search_history(user_id, searched_at DESC, id DESC);\n'''
    idx_addition = idx_anchor + '''CREATE INDEX IF NOT EXISTS idx_user_saved_views_user\n    ON user_saved_views(user_id, pinned DESC, name COLLATE NOCASE);\n'''
    text = replace_once(text, idx_anchor, idx_addition, "saved views index")
    write(path, text)


def patch_migrations() -> None:
    path = "app/migrations.py"
    text = read(path)
    anchor = '''def _login_lockouts(conn: sqlite3.Connection) -> None:\n    conn.execute(\n        """CREATE TABLE IF NOT EXISTS login_lockouts (\n             scope TEXT NOT NULL CHECK(scope IN ('identity','ip')),\n             lock_key TEXT NOT NULL,\n             locked_until TEXT NOT NULL,\n             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n             PRIMARY KEY(scope,lock_key)\n           )"""\n    )\n    conn.execute(\n        "CREATE INDEX IF NOT EXISTS idx_login_lockouts_until ON login_lockouts(locked_until)"\n    )\n\n\n'''
    addition = anchor + '''def _user_saved_views(conn: sqlite3.Connection) -> None:\n    conn.execute(\n        """CREATE TABLE IF NOT EXISTS user_saved_views (\n             id INTEGER PRIMARY KEY,\n             user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n             name TEXT NOT NULL COLLATE NOCASE,\n             path TEXT NOT NULL CHECK(path IN ('/library','/movies','/shows')),\n             query_string TEXT NOT NULL DEFAULT '',\n             pinned INTEGER NOT NULL DEFAULT 0,\n             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n             updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n             UNIQUE(user_id,name)\n           )"""\n    )\n    conn.execute(\n        """CREATE INDEX IF NOT EXISTS idx_user_saved_views_user\n           ON user_saved_views(user_id,pinned DESC,name COLLATE NOCASE)"""\n    )\n\n\n'''
    text = replace_once(text, anchor, addition, "saved views migration function")
    migration_anchor = '    Migration(11, "persistent aggregate login lockouts", _login_lockouts),\n'
    text = replace_once(
        text, migration_anchor,
        migration_anchor + '    Migration(12, "user saved library views", _user_saved_views),\n',
        "saved views migration registration",
    )
    write(path, text)


def patch_app_settings() -> None:
    path = "app/app_settings.py"
    text = read(path)
    text = replace_once(
        text,
        '        "default_cover_size",\n',
        '        "default_cover_size",\n        "default_season_display",\n',
        "editable season display",
    )
    text = replace_once(
        text,
        '            "default_cover_size": "180",\n',
        '            "default_cover_size": "180",\n            "default_season_display": "collapsed",\n',
        "season display default",
    )
    logging_anchor = '''    def validate_logging(self, log_level: str) -> dict[str, str]:\n        level = log_level.strip().casefold()\n        if level not in {"info", "verbose", "debug"}:\n            raise AppSettingError("Choose Standard, Verbose, or Debug logging.")\n        return {"log_level": level}\n\n'''
    text = replace_once(
        text, logging_anchor,
        logging_anchor + '''    def validate_season_display(self, value: str) -> dict[str, str]:\n        display = value.strip().casefold()\n        if display not in {"collapsed", "expanded"}:\n            raise AppSettingError("Choose Collapsed or Expanded for the default TV season display.")\n        return {"default_season_display": display}\n\n''',
        "season display validation",
    )
    import_anchor = '''        if "log_level" in text_values:\n            validated.update(self.validate_logging(text_values["log_level"]))\n'''
    text = replace_once(
        text, import_anchor,
        import_anchor + '''        if "default_season_display" in text_values:\n            validated.update(\n                self.validate_season_display(text_values["default_season_display"])\n            )\n''',
        "season display import",
    )
    write(path, text)


def patch_settings_route() -> None:
    path = "app/routes/settings.py"
    text = read(path)
    old = '''    def save_general_settings(\n        request: Request, timezone_name: str = Form(...),\n        default_library_view: str = Form(...), default_cover_size: str = Form(...),\n    ):\n        submitted = {\n            "timezone": timezone_name,\n            "default_library_view": default_library_view,\n            "default_cover_size": default_cover_size,\n        }\n        try:\n            validated = app_settings.validate_general(\n                app_settings.get("installation_name"), timezone_name,\n                default_library_view, default_cover_size,\n            )\n            changed = app_settings.update(validated, request.state.user.id)\n'''
    new = '''    def save_general_settings(\n        request: Request, timezone_name: str = Form(...),\n        default_library_view: str = Form(...), default_cover_size: str = Form(...),\n        default_season_display: str = Form("collapsed"),\n    ):\n        submitted = {\n            "timezone": timezone_name,\n            "default_library_view": default_library_view,\n            "default_cover_size": default_cover_size,\n            "default_season_display": default_season_display,\n        }\n        try:\n            validated = app_settings.validate_general(\n                app_settings.get("installation_name"), timezone_name,\n                default_library_view, default_cover_size,\n            )\n            validated.update(app_settings.validate_season_display(default_season_display))\n            changed = app_settings.update(validated, request.state.user.id)\n'''
    text = replace_once(text, old, new, "general settings season display")
    write(path, text)


def patch_settings_template() -> None:
    path = "app/templates/settings.html"
    text = read(path)
    anchor = '''    <label>Default library view<select name="default_library_view"><option value="list" {% if preferences.default_library_view == 'list' %}selected{% endif %}>List</option><option value="covers" {% if preferences.default_library_view == 'covers' %}selected{% endif %}>Covers</option></select></label>\n    <label>Default cover size <output id="settings-cover-size-output">{{ preferences.default_cover_size }}px</output><input id="settings-cover-size" name="default_cover_size" type="range" min="120" max="300" step="10" value="{{ preferences.default_cover_size }}"></label>\n'''
    replacement = anchor + '''    <label>TV season groups<select name="default_season_display"><option value="collapsed" {% if preferences.default_season_display != 'expanded' %}selected{% endif %}>Collapsed by default</option><option value="expanded" {% if preferences.default_season_display == 'expanded' %}selected{% endif %}>Expanded by default</option></select><small>This controls the starting state on the full TV title page. Expand all and Collapse all remain available there.</small></label>\n'''
    text = replace_once(text, anchor, replacement, "season display settings control")
    write(path, text)


def patch_title_route() -> None:
    path = "app/routes/titles.py"
    text = read(path)
    text = replace_once(
        text,
        '    clean_label = ctx.live("clean_label")\n',
        '    app_settings = ctx.live("app_settings")\n    clean_label = ctx.live("clean_label")\n',
        "title route app settings",
    )
    context_anchor = '''            "title_state": title_state, "title_tags": title_tags,\n            "tvdb_enabled": bool(getattr(tvdb, "api_key", settings.tvdb_api_key)),\n'''
    text = replace_once(
        text, context_anchor,
        '''            "title_state": title_state, "title_tags": title_tags,\n            "default_season_display": app_settings.get("default_season_display"),\n            "tvdb_enabled": bool(getattr(tvdb, "api_key", settings.tvdb_api_key)),\n''',
        "title detail season display context",
    )
    write(path, text)


def patch_detail_template() -> None:
    path = "app/templates/detail.html"
    text = read(path)
    text = replace_once(
        text,
        '<div class="season-collapse-toolbar" aria-label="Season display controls"><span>Season groups start collapsed</span>',
        '<div class="season-collapse-toolbar" aria-label="Season display controls"><span>Default: {{ default_season_display|title }}</span>',
        "season toolbar default label",
    )
    text = replace_once(
        text,
        '  const expandedSeasons = new Set();\n  let activeSeason = "all";\n',
        '''  const defaultSeasonDisplay = {{ default_season_display|tojson }};\n  const expandedSeasons = new Set(\n    defaultSeasonDisplay === "expanded"\n      ? seasonHeadings.map((heading) => heading.dataset.seasonHeading)\n      : []\n  );\n  let activeSeason = "all";\n''',
        "season default initialization",
    )
    text = text.replace(
        '  // Chandler\'s installation preference: season groups are collapsed until the\n  // viewer explicitly opens one or chooses Expand all.\n  updateSeasonView();\n',
        '  updateSeasonView();\n',
        1,
    )
    write(path, text)


def patch_library_route() -> None:
    path = "app/routes/library.py"
    text = read(path)
    text = replace_once(
        text,
        'from ..access import require_librarian\n',
        'from ..access import require_librarian\nfrom ..saved_views import SavedViewError, SavedViewService\n',
        "saved view imports",
    )
    text = replace_once(
        text,
        '    db = ctx.live("db")\n',
        '    db = ctx.live("db")\n    saved_views = SavedViewService(db)\n',
        "saved view service",
    )

    route_anchor = '''    @router.get("/library", response_class=HTMLResponse)\n    def library(\n'''
    routes = '''    @router.post("/library/views")\n    def save_library_view(\n        request: Request, name: str = Form(...), view_path: str = Form("/library"),\n        view_query: str = Form(""), pinned: str = Form("0"),\n    ):\n        try:\n            view, created = saved_views.save(\n                request.state.user.id, name, view_path, view_query, pinned=pinned == "1",\n            )\n        except SavedViewError as exc:\n            return redirect(view_path if view_path in SavedViewService.ALLOWED_PATHS else "/library", str(exc))\n        message = (\n            f'Saved view "{view["name"]}" created.'\n            if created else f'Saved view "{view["name"]}" updated.'\n        )\n        record_event(\n            "library", message, user_id=request.state.user.id,\n            context={"saved_view_id": view["id"], "pinned": view["pinned"]},\n        )\n        return redirect(view["href"], message)\n\n    @router.post("/library/views/{view_id}/pin")\n    def toggle_saved_view_pin(request: Request, view_id: int):\n        try:\n            view = saved_views.toggle_pin(request.state.user.id, view_id)\n        except SavedViewError as exc:\n            return redirect("/library", str(exc))\n        message = f'{"Pinned" if view["pinned"] else "Unpinned"} saved view "{view["name"]}".'\n        record_event(\n            "library", message, user_id=request.state.user.id,\n            context={"saved_view_id": view_id, "pinned": view["pinned"]},\n        )\n        return redirect("/library", message)\n\n    @router.post("/library/views/{view_id}/rename")\n    def rename_saved_view(request: Request, view_id: int, name: str = Form(...)):\n        try:\n            view = saved_views.rename(request.state.user.id, view_id, name)\n        except SavedViewError as exc:\n            return redirect("/library", str(exc))\n        message = f'Saved view renamed to "{view["name"]}".'\n        record_event(\n            "library", message, user_id=request.state.user.id,\n            context={"saved_view_id": view_id},\n        )\n        return redirect("/library", message)\n\n    @router.post("/library/views/{view_id}/delete")\n    def delete_saved_view(request: Request, view_id: int):\n        try:\n            name = saved_views.delete(request.state.user.id, view_id)\n        except SavedViewError as exc:\n            return redirect("/library", str(exc))\n        message = f'Saved view "{name}" deleted.'\n        record_event(\n            "library", message, user_id=request.state.user.id,\n            context={"saved_view_id": view_id},\n        )\n        return redirect("/library", message)\n\n'''
    text = replace_once(text, route_anchor, routes + route_anchor, "saved view routes")

    before_return = '''        return templates.TemplateResponse(request, "library.html", {\n            "rows": rows, "q": q, "kind": kind, "letter": normalized_letter,\n'''
    replacement = '''        current_view_path = {"movie": "/movies", "tv": "/shows"}.get(kind, "/library")\n        current_view_query = urlencode({\n            key: value for key, value in {\n                "q": q, "letter": normalized_letter, "genre": genre,\n                "title_type": title_type, "root": root_id, "person": person_id,\n                "person_name": (selected_person["person_name"] if selected_person else person_name),\n                "credit_role": credit_role, "match": match_status, "gaps": gap_status,\n                "favorite": favorite_status, "tag": tag_id, "sort": sort_key,\n            }.items() if value and not (key == "sort" and value == "title")\n        })\n        user_saved_views = saved_views.list_for_user(request.state.user.id)\n        return templates.TemplateResponse(request, "library.html", {\n            "rows": rows, "q": q, "kind": kind, "letter": normalized_letter,\n'''
    text = replace_once(text, before_return, replacement, "saved views library context prelude")
    context_anchor = '''            "tag_options": tag_options,\n            "root_options": root_options,\n'''
    text = replace_once(
        text, context_anchor,
        '''            "tag_options": tag_options,\n            "saved_views": user_saved_views,\n            "pinned_saved_views": [view for view in user_saved_views if view["pinned"]],\n            "current_view_path": current_view_path,\n            "current_view_query": current_view_query,\n            "root_options": root_options,\n''',
        "saved views library template context",
    )

    return_anchor = '''        "delete_tag": delete_tag,\n        "library": library,\n'''
    text = replace_once(
        text, return_anchor,
        '''        "delete_tag": delete_tag,\n        "save_library_view": save_library_view,\n        "toggle_saved_view_pin": toggle_saved_view_pin,\n        "rename_saved_view": rename_saved_view,\n        "delete_saved_view": delete_saved_view,\n        "library": library,\n''',
        "saved view compatibility aliases",
    )
    write(path, text)


def patch_library_template() -> None:
    path = "app/templates/library.html"
    text = read(path)
    anchor = '''<div class="catalog-tabs"><a href="/library{% if source_query %}?{{ source_query }}{% endif %}" {% if kind=='all' %}class="active"{% endif %}>All</a><a href="/movies{% if source_query %}?{{ source_query }}{% endif %}" {% if kind=='movie' %}class="active"{% endif %}>Movies</a><a href="/shows{% if source_query %}?{{ source_query }}{% endif %}" {% if kind=='tv' %}class="active"{% endif %}>TV Shows</a></div>\n'''
    saved_ui = anchor + '''{% if current_user.id > 0 %}\n<section class="saved-view-bar" aria-label="Saved library views">\n  <div class="saved-view-pins">\n    <span class="saved-view-label">SAVED VIEWS</span>\n    {% for view in pinned_saved_views %}<a class="saved-view-chip" href="{{ view.href }}" title="Open {{ view.name }}">{{ view.name }}</a>{% endfor %}\n    {% if not pinned_saved_views %}<span class="saved-view-empty">Pin a saved view to keep it here and on Dashboard.</span>{% endif %}\n  </div>\n  <details class="saved-view-manager">\n    <summary class="button">Saved views{% if saved_views %} <span>{{ saved_views|length }}</span>{% endif %}</summary>\n    <div class="saved-view-panel">\n      <form class="saved-view-create" method="post" action="/library/views">\n        <strong>Save this view</strong>\n        <input name="name" maxlength="60" placeholder="For example, Unmatched movies" required>\n        <input type="hidden" name="view_path" value="{{ current_view_path }}">\n        <input type="hidden" name="view_query" value="{{ current_view_query }}">\n        <label class="saved-view-pin-choice"><input type="checkbox" name="pinned" value="1"> Pin to Library and Dashboard</label>\n        <button class="button primary">Save view</button>\n      </form>\n      {% if saved_views %}<div class="saved-view-list">{% for view in saved_views %}\n        <article>\n          <a class="saved-view-open" href="{{ view.href }}"><strong>{{ view.name }}</strong><small>{{ 'Pinned' if view.pinned else 'Saved' }} · {{ 'Movies' if view.path == '/movies' else 'TV Shows' if view.path == '/shows' else 'All Media' }}</small></a>\n          <div class="saved-view-actions">\n            <form method="post" action="/library/views/{{ view.id }}/pin"><button type="submit" title="{{ 'Unpin' if view.pinned else 'Pin' }} {{ view.name }}" aria-label="{{ 'Unpin' if view.pinned else 'Pin' }} {{ view.name }}">{{ '★' if view.pinned else '☆' }}</button></form>\n            <details class="saved-view-rename"><summary title="Rename {{ view.name }}" aria-label="Rename {{ view.name }}">Rename</summary><form method="post" action="/library/views/{{ view.id }}/rename"><input name="name" value="{{ view.name }}" maxlength="60" required><button class="button small">Save</button></form></details>\n            <form method="post" action="/library/views/{{ view.id }}/delete" data-workspace-confirm="Delete the saved view {{ view.name }}? Your media and Library settings will not be changed."><button class="saved-view-delete" type="submit" title="Delete {{ view.name }}" aria-label="Delete {{ view.name }}">×</button></form>\n          </div>\n        </article>\n      {% endfor %}</div>{% endif %}\n    </div>\n  </details>\n</section>\n{% endif %}\n'''
    text = replace_once(text, anchor, saved_ui, "library saved views UI")
    write(path, text)


def patch_dashboard_route() -> None:
    path = "app/routes/dashboard.py"
    text = read(path)
    text = replace_once(
        text,
        'from ..access import require_librarian\n',
        'from ..access import require_librarian\nfrom ..saved_views import SavedViewService\n',
        "dashboard saved view import",
    )
    text = replace_once(
        text,
        '    db = ctx.live("db")\n',
        '    db = ctx.live("db")\n    saved_views = SavedViewService(db)\n',
        "dashboard saved view service",
    )
    context_anchor = '''            "counts": counts, "roots": roots, "recent": recent, "favorites": favorites,\n            "jobs": scan_jobs,\n'''
    text = replace_once(
        text, context_anchor,
        '''            "counts": counts, "roots": roots, "recent": recent, "favorites": favorites,\n            "saved_views": saved_views.list_for_user(request.state.user.id, pinned_only=True),\n            "jobs": scan_jobs,\n''',
        "dashboard saved views context",
    )
    write(path, text)


def patch_dashboard_template() -> None:
    path = "app/templates/dashboard.html"
    text = read(path)
    anchor = '''  <section class="home-organize">\n'''
    section = '''  {% if saved_views %}\n  <section class="home-saved-views" aria-label="Pinned saved library views">\n    <div class="home-section-head"><div><p class="eyebrow">SAVED VIEWS</p><h2>Your Library shortcuts</h2></div><a href="/library">Manage in Library &rarr;</a></div>\n    <div class="home-saved-view-grid">{% for view in saved_views %}<a href="{{ view.href }}"><span>{{ 'MOVIES' if view.path == '/movies' else 'TV SHOWS' if view.path == '/shows' else 'ALL MEDIA' }}</span><strong>{{ view.name }}</strong><small>Open saved filters and sort &rarr;</small></a>{% endfor %}</div>\n  </section>\n  {% endif %}\n\n'''
    text = replace_once(text, anchor, section + anchor, "dashboard saved view section")
    write(path, text)


def patch_workspace_css() -> None:
    path = "app/static/workspace.css"
    text = read(path)
    marker = "/* W5 saved Library views */"
    if marker in text:
        raise RuntimeError("W5 CSS already exists")
    text += '''\n\n/* W5 saved Library views */\n.saved-view-bar {\n  align-items: center;\n  background: color-mix(in srgb, var(--panel) 90%, transparent);\n  border: 1px solid var(--line);\n  border-radius: 10px;\n  display: flex;\n  gap: 12px;\n  justify-content: space-between;\n  margin: 10px 0 14px;\n  padding: 8px 10px;\n  position: relative;\n}\n.saved-view-pins {align-items:center;display:flex;gap:7px;min-width:0;overflow:auto;scrollbar-width:thin}\n.saved-view-label {color:var(--lime);font-size:10px;font-weight:800;letter-spacing:.12em;white-space:nowrap}\n.saved-view-empty {color:var(--muted);font-size:11px;white-space:nowrap}\n.saved-view-chip {background:#101820;border:1px solid var(--line);border-radius:999px;color:var(--text);font-size:12px;padding:6px 10px;text-decoration:none;white-space:nowrap}\n.saved-view-chip:hover,.saved-view-chip:focus-visible {border-color:var(--lime);color:var(--lime);outline:none}\n.saved-view-manager {position:relative;flex:0 0 auto}\n.saved-view-manager > summary {cursor:pointer;list-style:none}\n.saved-view-manager > summary::-webkit-details-marker {display:none}\n.saved-view-manager > summary span {color:var(--muted);font-size:10px;margin-left:4px}\n.saved-view-panel {background:#0c131a;border:1px solid var(--line);border-radius:10px;box-shadow:0 18px 48px rgba(0,0,0,.35);display:grid;gap:10px;padding:12px;position:absolute;right:0;top:calc(100% + 7px);width:min(430px,calc(100vw - 40px));z-index:45}\n.saved-view-create {display:grid;gap:8px}\n.saved-view-create > input,.saved-view-rename input {background:var(--bg);border:1px solid var(--line);border-radius:5px;color:var(--text);font:inherit;padding:8px 9px}\n.saved-view-pin-choice {align-items:center;color:var(--muted);display:flex;font-size:12px;gap:7px}\n.saved-view-list {border-top:1px solid var(--line);display:grid;padding-top:6px}\n.saved-view-list article {align-items:center;border-bottom:1px solid color-mix(in srgb,var(--line) 65%,transparent);display:flex;gap:8px;padding:7px 2px}\n.saved-view-list article:last-child {border-bottom:0}\n.saved-view-open {display:grid;flex:1;min-width:0;text-decoration:none}\n.saved-view-open strong {color:var(--text);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\n.saved-view-open small {color:var(--muted);font-size:10px}\n.saved-view-open:hover strong {color:var(--lime)}\n.saved-view-actions {align-items:center;display:flex;gap:3px}\n.saved-view-actions form {margin:0}\n.saved-view-actions button,.saved-view-rename > summary {background:transparent;border:0;border-radius:4px;color:var(--muted);cursor:pointer;font:inherit;font-size:11px;padding:6px}\n.saved-view-actions button:hover,.saved-view-rename > summary:hover {background:#141e27;color:var(--lime)}\n.saved-view-delete:hover {color:#ff6b6b!important}\n.saved-view-rename {position:relative}\n.saved-view-rename > summary {list-style:none}\n.saved-view-rename > summary::-webkit-details-marker {display:none}\n.saved-view-rename form {align-items:center;background:#0c131a;border:1px solid var(--line);border-radius:7px;display:flex;gap:6px;padding:7px;position:absolute;right:0;top:calc(100% + 4px);width:260px;z-index:48}\n.saved-view-rename input {min-width:0;width:100%}\n.home-saved-views {margin-top:26px}\n.home-saved-view-grid {display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));margin-top:12px}\n.home-saved-view-grid > a {background:var(--panel);border:1px solid var(--line);border-radius:10px;display:grid;gap:4px;padding:14px;text-decoration:none}\n.home-saved-view-grid > a span {color:var(--lime);font-size:9px;font-weight:800;letter-spacing:.12em}\n.home-saved-view-grid > a strong {color:var(--text);font-size:15px}\n.home-saved-view-grid > a small {color:var(--muted);font-size:11px}\n.home-saved-view-grid > a:hover,.home-saved-view-grid > a:focus-visible {border-color:var(--lime);outline:none;transform:translateY(-1px)}\n@media (max-width:760px) {\n  .saved-view-bar {align-items:stretch;flex-direction:column}\n  .saved-view-manager > summary {width:100%}\n  .saved-view-panel {left:0;right:auto;width:min(430px,calc(100vw - 48px))}\n}\n'''
    write(path, text)


def patch_route_authorization_test() -> None:
    path = "tests/test_route_authorization.py"
    text = read(path)
    anchor = '''        if path.startswith(("/activate/", "/recovery/", "/account/", "/engagement/")):\n            return True\n'''
    replacement = '''        if path.startswith((\n            "/activate/", "/recovery/", "/account/", "/engagement/", "/library/views",\n        )):\n            return True\n'''
    text = replace_once(text, anchor, replacement, "saved views member-safe routes")
    write(path, text)


def patch_app_settings_tests() -> None:
    path = "tests/test_app_settings.py"
    text = read(path)
    text = replace_once(
        text,
        '        self.assertEqual(self.settings.get("lockdown_mode"), "0")\n',
        '        self.assertEqual(self.settings.get("lockdown_mode"), "0")\n        self.assertEqual(self.settings.get("default_season_display"), "collapsed")\n',
        "season display default test",
    )
    anchor = '''    def test_external_search_update(self):\n'''
    test = '''    def test_tv_season_display_default_is_explicit_and_portable(self):\n        self.assertEqual(\n            self.settings.validate_season_display("Expanded"),\n            {"default_season_display": "expanded"},\n        )\n        self.assertEqual(\n            self.settings.validate_import({"default_season_display": "collapsed"}),\n            {"default_season_display": "collapsed"},\n        )\n        with self.assertRaisesRegex(AppSettingError, "Collapsed or Expanded"):\n            self.settings.validate_season_display("sometimes")\n\n'''
    text = replace_once(text, anchor, test + anchor, "season display tests")
    write(path, text)


def patch_collapsible_tests() -> None:
    path = "tests/test_collapsible_seasons.py"
    text = read(path)
    text = replace_once(
        text,
        "        self.assertIn('const expandedSeasons = new Set();', template)\n",
        "        self.assertIn('const defaultSeasonDisplay = {{ default_season_display|tojson }};', template)\n        self.assertIn('defaultSeasonDisplay === \"expanded\"', template)\n",
        "collapsible default contract test",
    )
    write(path, text)


def add_saved_view_tests() -> None:
    path = ROOT / "tests" / "test_saved_views.py"
    if path.exists():
        raise RuntimeError("tests/test_saved_views.py already exists")
    path.write_text('''from __future__ import annotations\n\nimport tempfile\nimport unittest\nfrom pathlib import Path\n\nfrom app.db import Database\nfrom app.saved_views import SavedViewError, SavedViewService\n\n\nclass SavedViewServiceTests(unittest.TestCase):\n    def setUp(self):\n        self.temporary = tempfile.TemporaryDirectory()\n        self.database = Database(Path(self.temporary.name) / "catalog.db")\n        self.database.initialize()\n        with self.database.connect() as conn:\n            self.user_one = int(conn.execute(\n                """INSERT INTO users(username,display_name,role,password_hash)\n                   VALUES ('one','One','member','test')"""\n            ).lastrowid)\n            self.user_two = int(conn.execute(\n                """INSERT INTO users(username,display_name,role,password_hash)\n                   VALUES ('two','Two','member','test')"""\n            ).lastrowid)\n        self.views = SavedViewService(self.database)\n\n    def tearDown(self):\n        self.temporary.cleanup()\n\n    def test_saved_view_normalizes_filters_and_never_keeps_arbitrary_parameters(self):\n        path, query = self.views.normalize_target(\n            "/movies",\n            "q=Alien&sort=rating&root=7&gaps=missing&record_search=1&next=https%3A%2F%2Fevil.test",\n        )\n        self.assertEqual(path, "/movies")\n        self.assertEqual(query, "q=Alien&root=7&sort=rating")\n        fallback_path, fallback_query = self.views.normalize_target(\n            "https://evil.test", "sort=not-real&letter=A&favorite=favorites"\n        )\n        self.assertEqual(fallback_path, "/library")\n        self.assertEqual(fallback_query, "letter=A&favorite=favorites")\n\n    def test_views_are_private_to_the_account_and_same_name_updates(self):\n        first, created = self.views.save(\n            self.user_one, "Needs matching", "/movies", "match=unmatched", pinned=True\n        )\n        self.assertTrue(created)\n        self.assertTrue(first["pinned"])\n        updated, created = self.views.save(\n            self.user_one, "Needs matching", "/shows", "gaps=missing", pinned=False\n        )\n        self.assertFalse(created)\n        self.assertEqual(updated["href"], "/shows?gaps=missing")\n        self.assertFalse(updated["pinned"])\n        self.assertEqual(len(self.views.list_for_user(self.user_one)), 1)\n        self.assertEqual(self.views.list_for_user(self.user_two), [])\n        with self.assertRaises(SavedViewError):\n            self.views.delete(self.user_two, first["id"])\n\n    def test_pin_limit_and_rename_are_enforced_per_user(self):\n        for index in range(self.views.MAX_PINNED):\n            self.views.save(\n                self.user_one, f"Pinned {index}", "/library", f"q={index}", pinned=True\n            )\n        with self.assertRaisesRegex(SavedViewError, "Pin up to"):\n            self.views.save(self.user_one, "One too many", "/library", "q=extra", pinned=True)\n        item = self.views.list_for_user(self.user_one)[0]\n        renamed = self.views.rename(self.user_one, item["id"], "Renamed view")\n        self.assertEqual(renamed["name"], "Renamed view")\n        self.assertEqual(len(self.views.list_for_user(self.user_one, pinned_only=True)), 8)\n\n\nclass SavedViewUiContractTests(unittest.TestCase):\n    def test_library_and_dashboard_surface_saved_views(self):\n        library = (Path(__file__).resolve().parents[1] / "app/templates/library.html").read_text(encoding="utf-8")\n        dashboard = (Path(__file__).resolve().parents[1] / "app/templates/dashboard.html").read_text(encoding="utf-8")\n        self.assertIn('action="/library/views"', library)\n        self.assertIn("Pin to Library and Dashboard", library)\n        self.assertIn("saved-view-chip", library)\n        self.assertIn("home-saved-view-grid", dashboard)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")


def patch_migration_test() -> None:
    path = "tests/test_migrations.py"
    text = read(path)
    anchor = '                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=11").fetchone())\n'
    replacement = anchor + '''                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=12").fetchone())\n                saved_view_columns = {\n                    row["name"] for row in upgraded.execute("PRAGMA table_info(user_saved_views)")\n                }\n                self.assertTrue({"user_id", "name", "path", "query_string", "pinned"}.issubset(saved_view_columns))\n'''
    text = replace_once(text, anchor, replacement, "saved views migration test")
    write(path, text)


def patch_workspace_docs() -> None:
    path = "docs/WORKSPACE.md"
    text = read(path)
    text = replace_once(
        text,
        "5. **W5 Saved Views**: named filter/sort workspaces that can be pinned to Library and Dashboard.\n",
        "5. **W5 Saved Views (complete)**: named filter/sort workspaces that can be pinned to Library and Dashboard.\n",
        "W5 roadmap completion",
    )
    marker = "## W3 + W4: Unified Review and application interactions\n"
    section = '''## W5 Saved Views\n\nW5 turns the current Library filter/sort state into a personal reusable workspace.\nSigned-in users can save the normalized current Library, Movies, or TV Shows view,\nrename it, pin or unpin it, and delete it without affecting media or global settings.\nOnly the known Library filter keys are stored; arbitrary query parameters and external\npaths are discarded before persistence. Pinned views appear both above the Library\nfilters and on Dashboard. Saved views are private to each account and capped to keep\nthe navigation surfaces manageable.\n\nThe full TV title view also gains an installation-wide collapsed/expanded default for\nseason groups. Collapsed remains the default, while Librarians can switch the starting\nstate under General Settings; per-page Expand all and Collapse all controls remain\navailable.\n\n'''
    text = replace_once(text, marker, section + marker, "W5 documentation section")
    write(path, text)


def main() -> None:
    add_saved_views_service()
    patch_db()
    patch_migrations()
    patch_app_settings()
    patch_settings_route()
    patch_settings_template()
    patch_title_route()
    patch_detail_template()
    patch_library_route()
    patch_library_template()
    patch_dashboard_route()
    patch_dashboard_template()
    patch_workspace_css()
    patch_route_authorization_test()
    patch_app_settings_tests()
    patch_collapsible_tests()
    add_saved_view_tests()
    patch_migration_test()
    patch_workspace_docs()


if __name__ == "__main__":
    main()

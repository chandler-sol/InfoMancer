from __future__ import annotations

import time
from datetime import datetime, timezone
from threading import RLock

from .db import Database


TOUR_KEY = "welcome-v1"

OFFICIAL_ANNOUNCEMENTS = (
    {
        "source_key": "release-notes-since-0.4-2026-08-06",
        "title": "Since InfoMancer 0.4: explainable library intelligence",
        "body": (
            "Release notes since 0.4\n\n"
            "MEDIA INTELLIGENCE\n"
            "- Library Health now explains identity, completeness, quality, "
            "freshness, storage, and unreadable-media findings with supporting "
            "evidence and recommended next steps.\n"
            "- Librarians can calibrate identity and stale-source thresholds, "
            "see transparent category scores and analysis history, and teach MIE "
            "from corrections scoped to one finding, title, or source.\n"
            "- Per-source quality profiles detect files below your preferred "
            "technical standards and files that differ from a title's usual profile.\n\n"
            "SAFER DUPLICATE REVIEW\n"
            "- Duplicate Intelligence compares episode coverage and technical "
            "quality, recommends the stronger copy, and can verify exact matches "
            "with SHA-256. It never deletes media automatically.\n\n"
            "EVERYDAY EXPERIENCE\n"
            "- The dashboard, responsive library, navigation, persistent sidebar, "
            "branding, search, filters, guided tour, and background-task feedback "
            "have been redesigned and polished.\n"
            "- Local account recovery, sessions, Librarian administration, profile "
            "choices, and home-layout preferences are more capable and clearer.\n\n"
            "This remains an alpha release. Back up the InfoMancer database before "
            "updating an important library and review every proposed filesystem change."
        ),
        "category": "update",
        "starts_at": "2026-08-06 00:00:00",
    },
    {
        "source_key": "release-0.5.0-alpha.1",
        "title": "InfoMancer 0.5: your library, understood",
        "body": (
            "This alpha introduces the first Media Intelligence Engine health "
            "analysis, a redesigned dashboard and navigation system, improved "
            "library discovery and search, clearer background-task feedback, "
            "and a more polished guided tour. It also strengthens local account "
            "recovery and administration while preserving InfoMancer's "
            "explainable, review-first approach."
        ),
        "category": "update",
        "starts_at": "2026-07-30 00:00:00",
    },
    {
        "source_key": "release-0.4.0-alpha.1",
        "title": "InfoMancer 0.4: safer administration and richer libraries",
        "body": (
            "This alpha adds Collections and Favorites for movies, series, and "
            "individual episodes; improved library search and mobile layouts; "
            "portable settings; validated database backup and restore; release "
            "checking; clearer diagnostics; and extensive matching, account, "
            "source, and navigation refinements. The next development direction "
            "is the explainable Media Intelligence Engine described in the "
            "0.4 release notes."
        ),
        "category": "update",
        "starts_at": "2026-07-29 00:00:00",
    },
    {
        "source_key": "release-0.3.0",
        "title": "New: App Settings, guided tours, and announcements",
        "body": (
            "InfoMancer now includes Librarian-controlled application settings, "
            "a replayable new-user tour, and an announcement center. Open "
            "Announcements from the main menu whenever you want to review this update."
        ),
        "category": "update",
        "starts_at": "2026-07-22 00:00:00",
    },
)


class EngagementError(ValueError):
    pass


class EngagementService:
    # shared_template_context asks for tour, due count, setup state, then due item in
    # quick succession. Hand those reads across one short-lived snapshot instead of
    # opening SQLite four times for every rendered page. The window is intentionally
    # tiny and every service write invalidates it immediately.
    PAGE_STATE_TTL_SECONDS = 0.25

    def __init__(self, database: Database) -> None:
        self.database = database
        self._page_state_lock = RLock()
        self._page_state_cache: dict[int, tuple[float, dict]] = {}

    def _invalidate_page_state(self, user_id: int | None = None) -> None:
        with self._page_state_lock:
            if user_id is None:
                self._page_state_cache.clear()
            else:
                self._page_state_cache.pop(int(user_id), None)

    def _page_state(self, user_id: int, role: str = "") -> dict:
        if user_id <= 0:
            return {
                "role": role,
                "tour_pending": False,
                "setup_choice_pending": False,
                "due_count": 0,
                "due": None,
            }
        now = time.monotonic()
        with self._page_state_lock:
            cached = self._page_state_cache.get(int(user_id))
            if cached and cached[0] >= now:
                state = cached[1]
                if not role or state["role"] == role:
                    return state

        with self.database.connect() as conn:
            overview = conn.execute(
                """SELECT
                     COALESCE((SELECT role FROM users WHERE id=?),'') role,
                     (SELECT COUNT(*) FROM user_tour_state
                      WHERE user_id=? AND tour_key=?
                        AND (completed_at IS NOT NULL OR dismissed_at IS NOT NULL)) tour_done,
                     (SELECT COUNT(*) FROM user_setup_state WHERE user_id=?) setup_exists,
                     (SELECT COUNT(*) FROM roots) root_count""",
                (user_id, user_id, TOUR_KEY, user_id),
            ).fetchone()
            resolved_role = role if role in {"member", "librarian"} else str(overview["role"] or "")
            audience = self._audience_clause(resolved_role)
            due = conn.execute(
                """SELECT a.*, r.last_seen_at, r.delivery_count,
                          COUNT(*) OVER() due_count
                   FROM announcements a
                   LEFT JOIN announcement_receipts r
                     ON r.announcement_id=a.id AND r.user_id=?
                   WHERE a.active=1 AND a.starts_at<=CURRENT_TIMESTAMP
                     AND (a.ends_at IS NULL OR a.ends_at>CURRENT_TIMESTAMP)
                     AND a.audience IN ('all', ?)
                     AND (
                       r.last_seen_at IS NULL OR
                       (a.recurrence_days IS NOT NULL AND
                        datetime(r.last_seen_at, '+' || a.recurrence_days || ' days')
                          <= CURRENT_TIMESTAMP)
                     )
                   ORDER BY CASE a.category WHEN 'important' THEN 0 WHEN 'update' THEN 1 ELSE 2 END,
                     a.starts_at, a.id
                   LIMIT 1""",
                (user_id, audience),
            ).fetchone()

        state = {
            "role": resolved_role,
            "tour_pending": not bool(overview["tour_done"]),
            "setup_choice_pending": bool(
                resolved_role == "librarian"
                and not overview["setup_exists"]
                and not overview["root_count"]
            ),
            "due_count": int(due["due_count"]) if due else 0,
            "due": due,
        }
        with self._page_state_lock:
            self._page_state_cache[int(user_id)] = (
                time.monotonic() + self.PAGE_STATE_TTL_SECONDS,
                state,
            )
            if len(self._page_state_cache) > 128:
                oldest_user = next(iter(self._page_state_cache))
                if oldest_user != int(user_id):
                    self._page_state_cache.pop(oldest_user, None)
        return state

    def seed_official(self) -> None:
        with self.database.connect() as conn:
            for item in OFFICIAL_ANNOUNCEMENTS:
                conn.execute(
                    """INSERT INTO announcements
                       (source,source_key,title,body,category,audience,starts_at)
                       VALUES ('official',?,?,?,?, 'all',?)
                       ON CONFLICT(source_key) DO UPDATE SET
                         title=excluded.title,body=excluded.body,
                         category=excluded.category,starts_at=excluded.starts_at,
                         updated_at=CURRENT_TIMESTAMP
                       WHERE announcements.title<>excluded.title
                          OR announcements.body<>excluded.body
                          OR announcements.category<>excluded.category
                          OR announcements.starts_at<>excluded.starts_at""",
                    (
                        item["source_key"], item["title"], item["body"],
                        item["category"], item["starts_at"],
                    ),
                )
        self._invalidate_page_state()

    def tour_pending(self, user_id: int) -> bool:
        return bool(self._page_state(user_id)["tour_pending"])

    def set_tour_state(self, user_id: int, completed: bool) -> None:
        if user_id <= 0:
            return
        completed_at = "CURRENT_TIMESTAMP" if completed else "NULL"
        dismissed_at = "NULL" if completed else "CURRENT_TIMESTAMP"
        with self.database.connect() as conn:
            conn.execute(
                f"""INSERT INTO user_tour_state
                    (user_id,tour_key,completed_at,dismissed_at,updated_at)
                    VALUES (?,?,{completed_at},{dismissed_at},CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id,tour_key) DO UPDATE SET
                      completed_at={completed_at},dismissed_at={dismissed_at},
                      updated_at=CURRENT_TIMESTAMP""",
                (user_id, TOUR_KEY),
            )
        self._invalidate_page_state(user_id)

    def setup_state(self, user_id: int):
        if user_id <= 0:
            return None
        with self.database.connect() as conn:
            return conn.execute(
                "SELECT * FROM user_setup_state WHERE user_id=?", (user_id,)
            ).fetchone()

    def setup_choice_pending(self, user_id: int, role: str) -> bool:
        if user_id <= 0 or role != "librarian":
            return False
        return bool(self._page_state(user_id, role)["setup_choice_pending"])

    def begin_setup(self, user_id: int, mode: str = "guided") -> None:
        if user_id <= 0 or mode not in {"guided", "manual"}:
            raise EngagementError("InfoMancer could not start that setup option.")
        completed = "CURRENT_TIMESTAMP" if mode == "manual" else "NULL"
        with self.database.connect() as conn:
            conn.execute(
                f"""INSERT INTO user_setup_state
                    (user_id,mode,current_step,completed_at,updated_at)
                    VALUES (?,?,'general',{completed},CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                      mode=excluded.mode,current_step='general',
                      completed_at={completed},updated_at=CURRENT_TIMESTAMP""",
                (user_id, mode),
            )
        self._invalidate_page_state(user_id)

    def set_setup_step(self, user_id: int, step: str) -> None:
        if step not in {"general", "metadata", "sources", "finish"}:
            raise EngagementError("That setup step is not available.")
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO user_setup_state(user_id,mode,current_step,updated_at)
                   VALUES (?,'guided',?,CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id) DO UPDATE SET mode='guided',
                     current_step=excluded.current_step,completed_at=NULL,
                     updated_at=CURRENT_TIMESTAMP""",
                (user_id, step),
            )
        self._invalidate_page_state(user_id)

    def complete_setup(self, user_id: int) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO user_setup_state
                   (user_id,mode,current_step,completed_at,updated_at)
                   VALUES (?,'guided','finish',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id) DO UPDATE SET current_step='finish',
                     completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP""",
                (user_id,),
            )
        self._invalidate_page_state(user_id)

    @staticmethod
    def _audience_clause(role: str) -> str:
        return "all" if role not in {"member", "librarian"} else f"{role}s"

    def due(self, user_id: int, role: str):
        if user_id <= 0:
            return None
        state = self._page_state(user_id, role)
        # due() is the last engagement read in shared_template_context when an
        # announcement may be displayed. Drop the handoff after consumption so an
        # unrelated later read is never held to the tiny coalescing window.
        with self._page_state_lock:
            self._page_state_cache.pop(int(user_id), None)
        return state["due"]

    def due_count(self, user_id: int, role: str) -> int:
        if user_id <= 0:
            return 0
        return int(self._page_state(user_id, role)["due_count"])

    def mark_seen(self, announcement_id: int, user_id: int, role: str) -> None:
        if user_id <= 0:
            return
        audience = self._audience_clause(role)
        with self.database.connect() as conn:
            allowed = conn.execute(
                """SELECT id FROM announcements
                   WHERE id=? AND active=1
                     AND starts_at<=CURRENT_TIMESTAMP
                     AND (ends_at IS NULL OR ends_at>CURRENT_TIMESTAMP)
                     AND audience IN ('all', ?)""",
                (announcement_id, audience),
            ).fetchone()
            if not allowed:
                raise EngagementError("That announcement is not available to this account.")
            conn.execute(
                """INSERT INTO announcement_receipts
                   (announcement_id,user_id) VALUES (?,?)
                   ON CONFLICT(announcement_id,user_id) DO UPDATE SET
                     last_seen_at=CURRENT_TIMESTAMP,
                     delivery_count=announcement_receipts.delivery_count+1""",
                (announcement_id, user_id),
            )
        self._invalidate_page_state(user_id)

    def list_for_user(self, user_id: int, role: str):
        audience = self._audience_clause(role)
        with self.database.connect() as conn:
            return conn.execute(
                """SELECT a.*, u.display_name created_by_name,
                     r.first_seen_at,r.last_seen_at,r.delivery_count,
                     CASE WHEN a.active=1 AND a.starts_at<=CURRENT_TIMESTAMP
                       AND (a.ends_at IS NULL OR a.ends_at>CURRENT_TIMESTAMP)
                       THEN 1 ELSE 0 END currently_active
                     ,CASE WHEN a.active=1 AND a.starts_at<=CURRENT_TIMESTAMP
                       AND (a.ends_at IS NULL OR a.ends_at>CURRENT_TIMESTAMP)
                       AND (r.last_seen_at IS NULL OR
                         (a.recurrence_days IS NOT NULL AND
                          datetime(r.last_seen_at, '+' || a.recurrence_days || ' days')
                            <= CURRENT_TIMESTAMP))
                       THEN 1 ELSE 0 END due_now
                   FROM announcements a
                   LEFT JOIN users u ON u.id=a.created_by
                   LEFT JOIN announcement_receipts r
                     ON r.announcement_id=a.id AND r.user_id=?
                   WHERE a.audience IN ('all', ?)
                   ORDER BY a.starts_at DESC,a.id DESC""",
                (user_id, audience),
            ).fetchall()

    def list_managed(self):
        with self.database.connect() as conn:
            return conn.execute(
                """SELECT a.*,u.display_name created_by_name,
                     (SELECT COUNT(*) FROM announcement_receipts r
                      WHERE r.announcement_id=a.id) recipient_count
                   FROM announcements a
                   LEFT JOIN users u ON u.id=a.created_by
                   WHERE a.source='installation'
                   ORDER BY a.created_at DESC,a.id DESC"""
            ).fetchall()

    def create(
        self, title: str, body: str, category: str, audience: str,
        starts_at: str, ends_at: str | None, recurrence_days: int | None,
        created_by: int,
    ) -> int:
        title = " ".join(title.strip().split())
        body = body.strip()
        if not 1 <= len(title) <= 120:
            raise EngagementError("Announcement title must contain between 1 and 120 characters.")
        if not 1 <= len(body) <= 4000:
            raise EngagementError("Announcement message must contain between 1 and 4,000 characters.")
        if category not in {"information", "update", "important"}:
            raise EngagementError("Choose Information, Update, or Important for the announcement style.")
        if audience not in {"all", "members", "librarians"}:
            raise EngagementError("Choose Members, Librarians, or Everyone for the audience.")
        if recurrence_days not in {None, 1, 7}:
            raise EngagementError("Choose Once, Daily, or Weekly for announcement delivery.")
        if recurrence_days and not ends_at:
            raise EngagementError("Recurring announcements need an end date so they do not repeat forever.")
        if ends_at and ends_at <= starts_at:
            raise EngagementError("The announcement end date must be later than its start date.")
        with self.database.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO announcements
                   (source,title,body,category,audience,starts_at,ends_at,
                    recurrence_days,created_by)
                   VALUES ('installation',?,?,?,?,?,?,?,?)""",
                (
                    title, body, category, audience, starts_at, ends_at,
                    recurrence_days, created_by,
                ),
            )
            announcement_id = int(cursor.lastrowid)
        self._invalidate_page_state()
        return announcement_id

    def deactivate(self, announcement_id: int) -> None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT source FROM announcements WHERE id=?", (announcement_id,)
            ).fetchone()
            if not row:
                raise EngagementError("That announcement could not be found.")
            if row["source"] == "official":
                raise EngagementError("Official release announcements cannot be disabled locally.")
            conn.execute(
                """UPDATE announcements SET active=0,updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (announcement_id,),
            )
        self._invalidate_page_state()


def utc_from_local(value: str, timezone_name: str) -> str:
    from zoneinfo import ZoneInfo

    try:
        local = datetime.fromisoformat(value.strip())
        aware = local.replace(tzinfo=ZoneInfo(timezone_name))
    except (ValueError, TypeError) as exc:
        raise EngagementError("Enter a complete date and time for the announcement.") from exc
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

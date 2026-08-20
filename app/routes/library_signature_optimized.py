from __future__ import annotations


def library_signature(db, user_id: int) -> tuple:
    """Read the render-cache revision in one SQLite statement.

    The previous cache check executed six independent SELECT statements on every
    Library landing, including cache hits. Scalar subqueries keep the same revision
    semantics while paying SQLite's prepare/execute round-trip only once.
    """
    with db.connect() as conn:
        row = conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM titles) title_count,
                 (SELECT COALESCE(MAX(updated_at),'') FROM titles) title_updated,
                 (SELECT COUNT(*) FROM roots) root_count,
                 (SELECT COALESCE(MAX(last_checked_at),'') FROM roots) root_checked,
                 (SELECT COUNT(*) FROM user_title_state WHERE user_id=?) state_count,
                 (SELECT COALESCE(MAX(updated_at),'') FROM user_title_state WHERE user_id=?) state_updated,
                 (SELECT COUNT(*) FROM user_tags WHERE user_id=?) tag_count,
                 (SELECT COALESCE(MAX(updated_at),'') FROM app_settings) settings_updated,
                 (SELECT COUNT(*) FROM announcements) announcement_count,
                 (SELECT COALESCE(MAX(updated_at),'') FROM announcements) announcement_updated""",
            (user_id, user_id, user_id),
        ).fetchone()
    return (
        int(row["title_count"]), str(row["title_updated"]),
        int(row["root_count"]), str(row["root_checked"]),
        int(row["state_count"]), str(row["state_updated"]),
        int(row["tag_count"]), str(row["settings_updated"]),
        int(row["announcement_count"]), str(row["announcement_updated"]),
    )

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Expected marker missing from {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Keep status tabs consistent with the normalized queue rather than counting raw
# duplicate-derived MIE rows that the queue deliberately replaces with live pairs.
replace_once(
    "app/review_queue.py",
    '''        with self.database.connect() as conn:\n            status_rows = conn.execute(\n                "SELECT status,COUNT(*) count FROM mie_findings GROUP BY status"\n            ).fetchall()\n        status_counts = {"active": 0, "dismissed": 0, "resolved": 0}\n        for row in status_rows:\n            status_counts[row["status"]] = int(row["count"] or 0)\n        if include_librarian:\n            status_counts["active"] += len(self._metadata_items()) + len(self.duplicates.candidates(status="active"))\n        else:\n            status_counts["active"] = len(self._all_items(status="active", include_librarian=False))\n''',
    '''        status_counts = {\n            review_status: len(self._all_items(\n                status=review_status, include_librarian=include_librarian,\n            ))\n            for review_status in ("active", "dismissed", "resolved")\n        }\n''',
)
replace_once("app/review_queue.py", "import json\n", "")

# Do not rebuild the command result DOM on every pointer movement. Update only
# the active visual row so hover remains smooth even in a remote desktop WebView.
replace_once(
    "app/static/workspace-ui.js",
    '''        button.addEventListener("mouseenter", () => { activeIndex = index; render(); });\n''',
    '''        button.addEventListener("mouseenter", () => {\n          activeIndex = index;\n          results.querySelectorAll('button[role="option"]').forEach((candidate, candidateIndex) => {\n            candidate.classList.toggle("active", candidateIndex === activeIndex);\n          });\n        });\n''',
)

# Avoid recomputing the full normalized Review queue twice after a dismissal.
replace_once(
    "app/routes/review.py",
    '''        counts = review_queue.view(include_librarian=True)["counts"]\n        counts["buckets"] = review_queue.view(include_librarian=True)["bucket_counts"]\n        return {"ok": True, "message": message, "remove_key": f"finding:{finding_id}", "counts": counts}\n''',
    '''        active = review_queue.view(include_librarian=True)\n        counts = dict(active["counts"])\n        counts["buckets"] = active["bucket_counts"]\n        return {"ok": True, "message": message, "remove_key": f"finding:{finding_id}", "counts": counts}\n''',
)

# Strengthen the service contract around normalized status counts.
test_path = ROOT / "tests/test_review_workspace.py"
text = test_path.read_text(encoding="utf-8")
marker = '''        self.assertGreaterEqual(view["bucket_counts"]["duplicates"], 1)\n'''
if marker not in text:
    raise RuntimeError("Review queue count test marker missing")
text = text.replace(
    marker,
    marker + '''        self.assertEqual(view["status_counts"]["active"], view["counts"]["total"])\n''',
    1,
)
marker = '''        self.assertIn("workspace-command-palette", ui)\n'''
if marker not in text:
    raise RuntimeError("W4 command palette test marker missing")
text = text.replace(
    marker,
    marker + '''        self.assertIn("candidateIndex === activeIndex", ui)\n''',
    1,
)
test_path.write_text(text, encoding="utf-8")

print("W3/W4 polish applied.")

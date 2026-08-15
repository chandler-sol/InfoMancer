from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Expected marker not found in {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


review_queue_py = r'''from __future__ import annotations

import json
from collections import Counter
from typing import Any


SEVERITY_ORDER = {"critical": 0, "warning": 1, "information": 2}
BUCKET_ORDER = (
    "health", "matching", "missing", "duplicates", "metadata",
    "quality", "editions", "sources", "storage",
)
BUCKET_LABELS = {
    "health": "Health",
    "matching": "Matching",
    "missing": "Missing",
    "duplicates": "Duplicates",
    "metadata": "Metadata",
    "quality": "Quality",
    "editions": "Editions",
    "sources": "Sources",
    "storage": "Storage",
}
RULE_BUCKETS = {
    "missing-episodes": "missing",
    "identity-confidence-low": "matching",
    "unmatched-title": "matching",
    "metadata-identifiers-missing": "matching",
    "duplicate-candidates": "duplicates",
    "duplicate-storage-recovery": "duplicates",
    "metadata-artwork-missing": "metadata",
    "metadata-credits-missing": "metadata",
    "metadata-episodes-incomplete": "metadata",
    "metadata-stale": "metadata",
    "quality-preference": "quality",
    "quality-consistency": "quality",
    "media-identity-unreviewed": "editions",
    "source-stale": "sources",
    "source-offline": "sources",
    "source-degraded": "sources",
    "technical-details-missing": "quality",
    "media-unreadable": "health",
}
DIRECT_DUPLICATE_RULES = {"duplicate-candidates", "duplicate-storage-recovery"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, dict):
        return ", ".join(f"{key}: {_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_text(item) for item in value)
    return str(value)


def _label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip().title()


def _evidence_rows(evidence: dict[str, Any] | None) -> list[dict[str, str]]:
    return [
        {"label": _label(str(key)), "value": _text(value) or "Not recorded"}
        for key, value in (evidence or {}).items()
    ]


class ReviewQueue:
    """Normalize existing review signals into one read-only Workspace queue.

    The queue is an adapter over current sources of truth. It does not create a new
    review-state table, so Library Health feedback, duplicate decisions, and metadata
    jobs keep their existing ownership and security boundaries.
    """

    def __init__(self, database, mie, duplicates) -> None:
        self.database = database
        self.mie = mie
        self.duplicates = duplicates

    @staticmethod
    def _bucket(finding: dict[str, Any]) -> str:
        rule_key = str(finding.get("rule_key") or "")
        if rule_key in RULE_BUCKETS:
            return RULE_BUCKETS[rule_key]
        category = str(finding.get("category") or "health")
        return {
            "identity": "matching",
            "completeness": "metadata",
            "quality": "quality",
            "freshness": "metadata",
            "storage": "storage",
            "health": "health",
        }.get(category, "health")

    def _finding_item(self, finding: dict[str, Any]) -> dict[str, Any]:
        bucket = self._bucket(finding)
        title_id = finding.get("title_id")
        root_id = finding.get("root_id")
        affected = (
            finding.get("title_name") or finding.get("filename")
            or finding.get("root_label") or "Library"
        )
        item = {
            "key": f"finding:{finding['id']}",
            "source": "finding",
            "source_label": "Media Intelligence",
            "item_id": str(finding["id"]),
            "status": finding.get("status") or "active",
            "severity": finding.get("severity") or "information",
            "bucket": bucket,
            "bucket_label": BUCKET_LABELS.get(bucket, _label(bucket)),
            "summary": finding.get("summary") or "Review finding",
            "explanation": finding.get("explanation") or "",
            "recommendation": finding.get("recommendation") or "",
            "title_id": title_id,
            "title_name": finding.get("title_name") or "",
            "title_kind": finding.get("title_kind") or "",
            "root_id": root_id,
            "root_label": finding.get("root_label") or "",
            "affected": affected,
            "href": finding.get("href") or "/library",
            "review_label": finding.get("review_label") or "Open affected media",
            "last_seen_at": finding.get("last_seen_at") or "",
            "rule_key": finding.get("rule_key") or "",
            "evidence": finding.get("evidence") or {},
            "evidence_rows": _evidence_rows(finding.get("evidence") or {}),
            "files": [],
        }
        item["drawer_url"] = f"/review/items/finding/{item['item_id']}"
        return item

    def _duplicate_item(self, candidate: dict[str, Any]) -> dict[str, Any]:
        file_a = candidate["file_a"]
        file_b = candidate["file_b"]
        left, right = sorted((int(file_a["id"]), int(file_b["id"])))
        severity = "warning" if candidate.get("classification") in {"verified_exact", "likely"} else "information"
        evidence = {
            "classification": candidate.get("label"),
            "recommended keep": candidate.get("recommended_keep"),
            "recoverable bytes": candidate.get("recoverable_bytes"),
            "verification": candidate.get("hash_state"),
            "source A": file_a.get("root_label"),
            "source B": file_b.get("root_label"),
        }
        item = {
            "key": f"duplicate:{left}:{right}",
            "source": "duplicate",
            "source_label": "Duplicate Review",
            "item_id": f"{left}:{right}",
            "status": candidate.get("status") or "active",
            "severity": severity,
            "bucket": "duplicates",
            "bucket_label": "Duplicates",
            "summary": f"{candidate.get('label')}: {candidate.get('title_name')}",
            "explanation": candidate.get("explanation") or "",
            "recommendation": candidate.get("recommendation") or "",
            "title_id": candidate.get("title_id"),
            "title_name": candidate.get("title_name") or "",
            "title_kind": candidate.get("kind") or "",
            "root_id": None,
            "root_label": "",
            "affected": candidate.get("title_name") or "Library",
            "href": "/duplicates",
            "review_label": "Open Duplicate Review",
            "last_seen_at": candidate.get("verified_at") or "",
            "rule_key": "duplicate-pair",
            "evidence": evidence,
            "evidence_rows": _evidence_rows(evidence),
            "files": [file_a, file_b],
            "file_a_id": left,
            "file_b_id": right,
        }
        item["drawer_url"] = f"/review/items/duplicate/{left}:{right}"
        return item

    def _metadata_items(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT q.title_id,q.status,q.provider,q.error,q.requested_at,
                          q.completed_at,t.kind,
                          COALESCE(NULLIF(t.metadata_title,''),t.title) title_name,
                          t.root_id,r.label root_label
                   FROM metadata_refresh_queue q
                   JOIN titles t ON t.id=q.title_id
                   JOIN roots r ON r.id=t.root_id
                   WHERE q.status='failed'
                   ORDER BY COALESCE(q.completed_at,q.requested_at) DESC,q.title_id"""
            ).fetchall()
        items = []
        for row in rows:
            evidence = {
                "provider": row["provider"] or "Default provider",
                "requested at": row["requested_at"],
                "completed at": row["completed_at"],
                "error": row["error"] or "No provider error was stored",
            }
            item = {
                "key": f"metadata:{row['title_id']}",
                "source": "metadata",
                "source_label": "Metadata Queue",
                "item_id": str(row["title_id"]),
                "status": "active",
                "severity": "warning",
                "bucket": "metadata",
                "bucket_label": "Metadata",
                "summary": f"Metadata refresh failed for {row['title_name']}",
                "explanation": row["error"] or "The most recent metadata refresh did not complete successfully.",
                "recommendation": "Open the title, verify its provider identity, and retry the metadata refresh when the provider is available.",
                "title_id": row["title_id"],
                "title_name": row["title_name"],
                "title_kind": row["kind"],
                "root_id": row["root_id"],
                "root_label": row["root_label"] or "",
                "affected": row["title_name"],
                "href": f"/titles/{row['title_id']}",
                "review_label": "Open title",
                "last_seen_at": row["completed_at"] or row["requested_at"] or "",
                "rule_key": "metadata-refresh-failed",
                "evidence": evidence,
                "evidence_rows": _evidence_rows(evidence),
                "files": [],
            }
            item["drawer_url"] = f"/review/items/metadata/{row['title_id']}"
            items.append(item)
        return items

    def _all_items(self, *, status: str, include_librarian: bool) -> list[dict[str, Any]]:
        status = status if status in {"active", "dismissed", "resolved"} else "active"
        items = []
        for finding in self.mie.findings(status=status):
            if finding.get("rule_key") in DIRECT_DUPLICATE_RULES:
                if include_librarian:
                    continue
                # Members cannot open Duplicate Review, so omit duplicate cleanup work.
                continue
            items.append(self._finding_item(finding))
        if status == "active":
            items.extend(self._metadata_items())
            if include_librarian:
                items.extend(self._duplicate_item(item) for item in self.duplicates.candidates(status="active"))
        return items

    def view(
        self, *, status: str = "active", severity: str = "", bucket: str = "",
        q: str = "", sort: str = "priority", include_librarian: bool = False,
    ) -> dict[str, Any]:
        status = status if status in {"active", "dismissed", "resolved"} else "active"
        severity = severity if severity in SEVERITY_ORDER else ""
        bucket = bucket if bucket in BUCKET_LABELS else ""
        sort = sort if sort in {"priority", "newest", "title"} else "priority"
        query = q.strip().casefold()[:200]
        all_items = self._all_items(status=status, include_librarian=include_librarian)
        counts = Counter(item["severity"] for item in all_items)
        bucket_counts = Counter(item["bucket"] for item in all_items)
        items = all_items
        if severity:
            items = [item for item in items if item["severity"] == severity]
        if bucket:
            items = [item for item in items if item["bucket"] == bucket]
        if query:
            items = [item for item in items if query in " ".join((
                item["summary"], item["explanation"], item["recommendation"],
                item["affected"], item["source_label"], item["bucket_label"],
            )).casefold()]
        bucket_rank = {key: index for index, key in enumerate(BUCKET_ORDER)}
        if sort == "newest":
            items.sort(key=lambda item: (item["last_seen_at"], item["summary"].casefold()), reverse=True)
        elif sort == "title":
            items.sort(key=lambda item: (item["affected"].casefold(), SEVERITY_ORDER[item["severity"]]))
        else:
            items.sort(key=lambda item: (
                SEVERITY_ORDER[item["severity"]],
                bucket_rank.get(item["bucket"], 99),
                item["affected"].casefold(), item["summary"].casefold(),
            ))
        with self.database.connect() as conn:
            status_rows = conn.execute(
                "SELECT status,COUNT(*) count FROM mie_findings GROUP BY status"
            ).fetchall()
        status_counts = {"active": 0, "dismissed": 0, "resolved": 0}
        for row in status_rows:
            status_counts[row["status"]] = int(row["count"] or 0)
        if include_librarian:
            status_counts["active"] += len(self._metadata_items()) + len(self.duplicates.candidates(status="active"))
        else:
            status_counts["active"] = len(self._all_items(status="active", include_librarian=False))
        summary = self.mie.summary()
        return {
            "items": items,
            "visible_count": len(items),
            "total": len(all_items),
            "counts": {
                "total": len(all_items),
                "critical": int(counts["critical"]),
                "warning": int(counts["warning"]),
                "information": int(counts["information"]),
            },
            "bucket_counts": {key: int(bucket_counts[key]) for key in BUCKET_ORDER},
            "buckets": [
                {"key": key, "label": BUCKET_LABELS[key], "count": int(bucket_counts[key])}
                for key in BUCKET_ORDER if bucket_counts[key]
            ],
            "status_counts": status_counts,
            "last_analyzed_at": summary.get("last_analyzed_at"),
            "overall_score": summary.get("overall_score"),
            "filters": {"status": status, "severity": severity, "bucket": bucket, "q": q.strip()[:200], "sort": sort},
        }

    def get_item(self, source: str, item_id: str, *, include_librarian: bool) -> dict[str, Any] | None:
        if source == "finding" and item_id.isdigit():
            target = int(item_id)
            for status in ("active", "dismissed", "resolved"):
                for finding in self.mie.findings(status=status):
                    if int(finding["id"]) == target:
                        if finding.get("rule_key") in DIRECT_DUPLICATE_RULES:
                            return None
                        return self._finding_item(finding)
            return None
        if source == "duplicate" and include_librarian:
            parts = item_id.split(":", 1)
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                return None
            pair = tuple(sorted((int(parts[0]), int(parts[1]))))
            for candidate in self.duplicates.candidates(status="active"):
                ids = tuple(sorted((int(candidate["file_a"]["id"]), int(candidate["file_b"]["id"]))))
                if ids == pair:
                    return self._duplicate_item(candidate)
            return None
        if source == "metadata" and item_id.isdigit():
            target = f"metadata:{int(item_id)}"
            return next((item for item in self._metadata_items() if item["key"] == target), None)
        return None
'''

review_html = r'''{% extends "base.html" %}
{% block title %}Review · {{ app_name }}{% endblock %}
{% block content %}
<section class="review-workspace" data-review-queue data-review-status="{{ filters.status }}">
  <header class="review-page-head">
    <div>
      <p class="eyebrow">REVIEW WORKSPACE</p>
      <h1>Review</h1>
      <p>One queue for the things InfoMancer thinks deserve a decision. Open an item to see the evidence, then act without losing your place.</p>
    </div>
    <div class="review-page-actions">
      {% if current_user.is_librarian %}
      <form method="post" action="/review/analyze" data-workspace-confirm="Refresh Media Intelligence now? This updates review findings but does not change media files.">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button class="button primary" data-workspace-command="Refresh review analysis">Refresh analysis</button>
      </form>
      {% endif %}
      <a class="button" href="/library-health">Health details</a>
    </div>
  </header>

  {% if message %}<div class="notice review-inline-notice">{{ message }}</div>{% endif %}

  <section class="review-summary-strip" aria-label="Review summary">
    <div><strong data-review-count="total">{{ queue.counts.total }}</strong><span>Needs attention</span></div>
    <div class="critical"><strong data-review-count="critical">{{ queue.counts.critical }}</strong><span>Critical</span></div>
    <div class="warning"><strong data-review-count="warning">{{ queue.counts.warning }}</strong><span>Warnings</span></div>
    <div><strong data-review-count="information">{{ queue.counts.information }}</strong><span>Information</span></div>
    <div class="review-health-score"><strong>{{ queue.overall_score if queue.overall_score is not none else '—' }}</strong><span>Health score</span></div>
  </section>

  <div class="review-meta-line">
    {% if queue.last_analyzed_at %}Media Intelligence last analyzed {{ local_time(queue.last_analyzed_at) }}.{% else %}Media Intelligence has not been analyzed yet.{% endif %}
    <span>{{ queue.status_counts.active }} active · {{ queue.status_counts.dismissed }} dismissed · {{ queue.status_counts.resolved }} resolved</span>
  </div>

  <form class="review-bucket-tabs" method="get" action="/review" aria-label="Review category filters">
    <input type="hidden" name="status" value="{{ filters.status }}">
    <input type="hidden" name="severity" value="{{ filters.severity }}">
    <input type="hidden" name="sort" value="{{ filters.sort }}">
    <button name="bucket" value="" class="{% if not filters.bucket %}active{% endif %}" data-workspace-command="Review all items">All <b>{{ queue.counts.total }}</b></button>
    {% for item in queue.buckets %}
    <button name="bucket" value="{{ item.key }}" class="{% if filters.bucket == item.key %}active{% endif %}" data-workspace-command="Review {{ item.label }}">{{ item.label }} <b data-review-bucket-count="{{ item.key }}">{{ item.count }}</b></button>
    {% endfor %}
  </form>

  <form class="review-filterbar" method="get" action="/review">
    <input type="hidden" name="bucket" value="{{ filters.bucket }}">
    <label class="review-search"><span class="sr-only">Search review queue</span><input type="search" name="q" value="{{ filters.q }}" placeholder="Search this queue"></label>
    <label>Status<select name="status"><option value="active" {% if filters.status == 'active' %}selected{% endif %}>Active</option><option value="dismissed" {% if filters.status == 'dismissed' %}selected{% endif %}>Dismissed</option><option value="resolved" {% if filters.status == 'resolved' %}selected{% endif %}>Resolved</option></select></label>
    <label>Severity<select name="severity"><option value="">All severities</option><option value="critical" {% if filters.severity == 'critical' %}selected{% endif %}>Critical</option><option value="warning" {% if filters.severity == 'warning' %}selected{% endif %}>Warning</option><option value="information" {% if filters.severity == 'information' %}selected{% endif %}>Information</option></select></label>
    <label>Sort<select name="sort"><option value="priority" {% if filters.sort == 'priority' %}selected{% endif %}>Priority</option><option value="newest" {% if filters.sort == 'newest' %}selected{% endif %}>Newest signal</option><option value="title" {% if filters.sort == 'title' %}selected{% endif %}>Title</option></select></label>
    <button class="button">Apply</button>
    {% if filters.q or filters.severity or filters.bucket or filters.status != 'active' or filters.sort != 'priority' %}<a class="button ghost" href="/review">Clear</a>{% endif %}
  </form>

  <div class="review-results-head">
    <strong>{{ queue.visible_count }} item{{ '' if queue.visible_count == 1 else 's' }}</strong>
    <span>Click an item to inspect it. Direct URLs still open the full specialist view.</span>
  </div>

  <section class="review-queue-list" aria-live="polite" data-review-list>
    {% for item in queue.items %}
    <article class="review-queue-item severity-{{ item.severity }}" data-review-item data-review-key="{{ item.key }}" data-review-severity="{{ item.severity }}" data-review-bucket="{{ item.bucket }}">
      <button class="review-item-open" type="button" data-workspace-drawer-url="{{ item.drawer_url }}" data-workspace-drawer-key="{{ item.key }}" data-workspace-drawer-param="review" aria-label="Inspect {{ item.summary }}">
        <span class="review-item-signal" aria-hidden="true"></span>
        <span class="review-item-copy">
          <span class="review-item-labels"><b>{{ item.severity|title }}</b><i>{{ item.bucket_label }}</i><i>{{ item.source_label }}</i></span>
          <strong>{{ item.summary }}</strong>
          <span>{{ item.affected }}{% if item.root_label and item.root_label != item.affected %} · {{ item.root_label }}{% endif %}</span>
        </span>
        <span class="review-item-recommendation">{{ item.recommendation }}</span>
      </button>
      <div class="workspace-context-menu" data-workspace-menu-root>
        <button class="workspace-context-toggle" type="button" data-workspace-menu-toggle aria-label="More actions for {{ item.summary }}" aria-expanded="false">•••</button>
        <div class="workspace-context-popover" data-workspace-menu hidden>
          <button type="button" data-workspace-drawer-url="{{ item.drawer_url }}" data-workspace-drawer-key="{{ item.key }}" data-workspace-drawer-param="review">Inspect evidence</button>
          <a href="{{ item.href }}">{{ item.review_label }}</a>
        </div>
      </div>
    </article>
    {% else %}
    <div class="review-empty-state">
      <strong>No review items match these filters.</strong>
      <p>{% if filters.status == 'active' %}That is a good sign. Clear filters or refresh analysis if you expected something here.{% else %}Try another review status or clear the current filters.{% endif %}</p>
    </div>
    {% endfor %}
    <div class="review-empty-state" data-review-empty hidden><strong>This queue is clear.</strong><p>The remaining visible items were handled without leaving the Review workspace.</p></div>
  </section>
</section>
{% endblock %}
'''

review_drawer_html = r'''<section class="review-drawer-content" data-review-drawer-panel data-review-key="{{ item.key }}">
  <header class="review-drawer-heading">
    <div class="review-item-labels"><b class="severity-{{ item.severity }}">{{ item.severity|title }}</b><i>{{ item.bucket_label }}</i><i>{{ item.source_label }}</i></div>
    <h2>{{ item.summary }}</h2>
    {% if item.affected %}<p>{{ item.affected }}{% if item.root_label and item.root_label != item.affected %} · {{ item.root_label }}{% endif %}</p>{% endif %}
  </header>

  <section class="review-drawer-section">
    <h3>Why it is here</h3>
    <p>{{ item.explanation }}</p>
  </section>

  <section class="review-drawer-section review-next-step">
    <h3>Recommended next step</h3>
    <p>{{ item.recommendation }}</p>
  </section>

  {% if item.files %}
  <section class="review-drawer-section">
    <h3>Compared files</h3>
    <div class="review-file-compare">
      {% for file in item.files %}
      <article>
        <strong>{{ file.filename }}</strong>
        <span>{{ file.root_label }}</span>
        <dl><div><dt>Resolution</dt><dd>{{ file.resolution }}</dd></div><div><dt>Video</dt><dd>{{ file.video_codec }}</dd></div><div><dt>Dynamic range</dt><dd>{{ file.dynamic_range }}</dd></div><div><dt>Container</dt><dd>{{ file.container }}</dd></div></dl>
      </article>
      {% endfor %}
    </div>
  </section>
  {% endif %}

  {% if item.evidence_rows %}
  <details class="review-drawer-section review-evidence" open>
    <summary>Evidence</summary>
    <dl>{% for evidence in item.evidence_rows %}<div><dt>{{ evidence.label }}</dt><dd>{{ evidence.value }}</dd></div>{% endfor %}</dl>
  </details>
  {% endif %}

  <div class="review-drawer-actions">
    <a class="button primary" href="{{ item.href }}">{{ item.review_label }}</a>
    {% if item.title_id %}<a class="button" href="/titles/{{ item.title_id }}">Open title</a>{% endif %}
  </div>

  {% if current_user.is_librarian and item.source == 'finding' and item.status == 'active' %}
  <details class="review-feedback-panel">
    <summary>Correct or dismiss</summary>
    <form method="post" action="/api/review/findings/{{ item.item_id }}/dismiss" data-workspace-ajax data-workspace-remove-key="{{ item.key }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <label>Reason<select name="reason"><option value="expected">Expected for this media</option><option value="incorrect">Analysis is incorrect</option><option value="resolved_elsewhere">Already handled elsewhere</option><option value="other">Other</option></select></label>
      <label>Apply to<select name="scope"><option value="finding">This finding only</option>{% if item.title_id %}<option value="title">This rule for this title</option>{% endif %}{% if item.root_id %}<option value="source">This rule for this source</option>{% endif %}</select></label>
      <label>Optional note<textarea name="note" maxlength="500" rows="3" placeholder="What should MIE understand?"></textarea></label>
      <button class="button">Save feedback and dismiss</button>
    </form>
  </details>
  {% elif current_user.is_librarian and item.source == 'finding' and item.status == 'dismissed' %}
  <form class="review-restore-form" method="post" action="/api/review/findings/{{ item.item_id }}/restore" data-workspace-ajax data-workspace-remove-key="{{ item.key }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="button">Restore to active review</button>
  </form>
  {% endif %}

  {% if current_user.is_librarian and item.source == 'duplicate' %}
  <section class="review-drawer-section review-duplicate-decisions">
    <h3>Review decision</h3>
    <p>These choices only change InfoMancer's review state. They do not move or delete either file.</p>
    <div class="button-row">
      <form method="post" action="/api/review/duplicates/{{ item.file_a_id }}/{{ item.file_b_id }}/decision" data-workspace-ajax data-workspace-remove-key="{{ item.key }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="decision" value="ignored"><button class="button">Ignore for now</button></form>
      <form method="post" action="/api/review/duplicates/{{ item.file_a_id }}/{{ item.file_b_id }}/decision" data-workspace-ajax data-workspace-remove-key="{{ item.key }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="decision" value="not_duplicate"><button class="button">Intentional alternative</button></form>
    </div>
    <a class="review-secondary-link" href="/duplicates">Verify contents or manage Trash in Duplicate Review →</a>
  </section>
  {% endif %}
</section>
'''

workspace_ui_js = r'''(() => {
  const Workspace = window.InfoMancerWorkspace = window.InfoMancerWorkspace || {};

  const sameOriginUrl = (value) => {
    try {
      const url = new URL(value, window.location.origin);
      return url.origin === window.location.origin ? url : null;
    } catch (_error) {
      return null;
    }
  };

  const ensureToastHost = () => {
    let host = document.getElementById("workspace-toast-host");
    if (host) return host;
    host = document.createElement("div");
    host.id = "workspace-toast-host";
    host.className = "workspace-toast-host";
    host.setAttribute("aria-live", "polite");
    host.setAttribute("aria-atomic", "false");
    document.body.append(host);
    return host;
  };

  Workspace.toast = (message, type = "success", timeout = 4200) => {
    if (!message) return;
    const host = ensureToastHost();
    const toast = document.createElement("div");
    toast.className = `workspace-toast ${type}`;
    toast.setAttribute("role", type === "error" ? "alert" : "status");
    const copy = document.createElement("span");
    copy.textContent = message;
    const close = document.createElement("button");
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss notification");
    close.textContent = "×";
    close.addEventListener("click", () => toast.remove());
    toast.append(copy, close);
    host.append(toast);
    requestAnimationFrame(() => toast.classList.add("visible"));
    if (timeout > 0) window.setTimeout(() => {
      toast.classList.remove("visible");
      window.setTimeout(() => toast.remove(), 180);
    }, timeout);
  };

  const ensureConfirmDialog = () => {
    let dialog = document.getElementById("workspace-confirm-dialog");
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.id = "workspace-confirm-dialog";
    dialog.className = "workspace-confirm-dialog";
    dialog.innerHTML = `
      <form method="dialog" class="workspace-dialog-card">
        <p class="eyebrow">CONFIRM ACTION</p>
        <h2>Continue?</h2>
        <p data-workspace-confirm-copy></p>
        <div class="workspace-dialog-actions">
          <button class="button" value="cancel">Cancel</button>
          <button class="button primary" value="confirm">Continue</button>
        </div>
      </form>`;
    document.body.append(dialog);
    return dialog;
  };

  Workspace.confirm = (message) => new Promise((resolve) => {
    const dialog = ensureConfirmDialog();
    if (typeof dialog.showModal !== "function") {
      resolve(window.confirm(message));
      return;
    }
    dialog.querySelector("[data-workspace-confirm-copy]").textContent = message;
    const finish = () => {
      dialog.removeEventListener("close", finish);
      resolve(dialog.returnValue === "confirm");
    };
    dialog.addEventListener("close", finish);
    dialog.showModal();
  });

  const ensureDrawer = () => {
    let drawer = document.getElementById("workspace-drawer");
    if (drawer) return drawer;
    drawer = document.createElement("aside");
    drawer.id = "workspace-drawer";
    drawer.className = "workspace-drawer";
    drawer.hidden = true;
    drawer.setAttribute("aria-label", "Workspace details");
    drawer.innerHTML = `
      <button class="workspace-drawer-scrim" type="button" data-workspace-drawer-close aria-label="Close details"></button>
      <section class="workspace-drawer-panel">
        <header class="workspace-drawer-chrome"><strong>Details</strong><button type="button" data-workspace-drawer-close aria-label="Close details">×</button></header>
        <div class="workspace-drawer-body"></div>
      </section>`;
    document.body.append(drawer);
    return drawer;
  };

  const drawerState = {key: "", param: "drawer", url: "", controller: null};

  const setDrawerUrl = (key, param, mode = "push") => {
    const url = new URL(window.location.href);
    if (key) url.searchParams.set(param, key);
    else url.searchParams.delete(param);
    const state = {...(history.state || {}), workspaceDrawerKey: key || null, workspaceDrawerParam: param};
    history[mode === "replace" ? "replaceState" : "pushState"](state, "", url.pathname + url.search + url.hash);
  };

  const closeDrawer = ({historyMode = "replace"} = {}) => {
    const drawer = ensureDrawer();
    drawerState.controller?.abort();
    drawerState.controller = null;
    drawer.classList.remove("open");
    document.body.classList.remove("workspace-drawer-open");
    const key = drawerState.key;
    const param = drawerState.param;
    drawerState.key = "";
    drawerState.url = "";
    window.setTimeout(() => { if (!drawerState.key) drawer.hidden = true; }, 180);
    if (historyMode === "back" && history.state?.workspaceDrawerKey === key) history.back();
    else if (historyMode === "replace") setDrawerUrl("", param, "replace");
  };

  const enhanceDrawerBody = () => {
    const drawer = ensureDrawer();
    drawer.querySelectorAll("[data-workspace-drawer-url]").forEach((button) => {
      button.addEventListener("click", () => openDrawer(button));
    });
  };

  const openDrawer = async (trigger, historyMode = null) => {
    const rawUrl = trigger?.dataset?.workspaceDrawerUrl || trigger?.url;
    const key = trigger?.dataset?.workspaceDrawerKey || trigger?.key || "";
    const param = trigger?.dataset?.workspaceDrawerParam || trigger?.param || "drawer";
    const url = sameOriginUrl(rawUrl);
    if (!url || !key) return;
    const drawer = ensureDrawer();
    const body = drawer.querySelector(".workspace-drawer-body");
    drawerState.controller?.abort();
    drawerState.controller = new AbortController();
    const replacing = Boolean(drawerState.key);
    drawerState.key = key;
    drawerState.param = param;
    drawerState.url = url.pathname + url.search;
    drawer.hidden = false;
    body.innerHTML = '<div class="workspace-drawer-state loading"><span></span><p>Loading details…</p></div>';
    requestAnimationFrame(() => {
      drawer.classList.add("open");
      document.body.classList.add("workspace-drawer-open");
    });
    if (historyMode !== "none") setDrawerUrl(key, param, historyMode || (replacing ? "replace" : "push"));
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        signal: drawerState.controller.signal,
        headers: {"X-Workspace-Drawer": "1"},
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      body.innerHTML = await response.text();
      enhanceDrawerBody();
    } catch (error) {
      if (error.name !== "AbortError") {
        body.innerHTML = '<div class="workspace-drawer-state error"><p>Details could not be loaded. Open the full page or try again.</p></div>';
      }
    }
  };
  Workspace.openDrawer = openDrawer;
  Workspace.closeDrawer = closeDrawer;

  const updateReviewCounts = (counts) => {
    if (!counts) return;
    ["total", "critical", "warning", "information"].forEach((key) => {
      const node = document.querySelector(`[data-review-count="${key}"]`);
      if (node && counts[key] !== undefined) node.textContent = counts[key];
    });
    Object.entries(counts.buckets || {}).forEach(([key, value]) => {
      const node = document.querySelector(`[data-review-bucket-count="${CSS.escape(key)}"]`);
      if (node) node.textContent = value;
    });
  };

  const removeReviewItem = (key) => {
    if (!key) return;
    document.querySelector(`[data-review-item][data-review-key="${CSS.escape(key)}"]`)?.remove();
    const list = document.querySelector("[data-review-list]");
    if (list && !list.querySelector("[data-review-item]")) {
      const empty = list.querySelector("[data-review-empty]");
      if (empty) empty.hidden = false;
    }
  };

  const submitWorkspaceForm = async (form) => {
    const confirmMessage = form.dataset.workspaceConfirm;
    if (confirmMessage && !(await Workspace.confirm(confirmMessage))) return;
    const action = sameOriginUrl(form.action);
    if (!action) {
      form.submit();
      return;
    }
    const submitters = [...form.querySelectorAll('button[type="submit"], input[type="submit"]')];
    submitters.forEach(button => button.disabled = true);
    form.classList.add("workspace-action-busy");
    try {
      const response = await fetch(action, {
        method: (form.method || "POST").toUpperCase(),
        credentials: "same-origin",
        body: new FormData(form),
        headers: {"Accept": "application/json", "X-Workspace-Action": "1"},
      });
      let data = null;
      try { data = await response.json(); } catch (_error) {}
      if (!response.ok) throw new Error(data?.detail || data?.message || `HTTP ${response.status}`);
      Workspace.toast(data?.message || "Action completed.", data?.type || "success");
      const removeKey = data?.remove_key || form.dataset.workspaceRemoveKey;
      if (removeKey) {
        removeReviewItem(removeKey);
        if (drawerState.key === removeKey) closeDrawer({historyMode: "replace"});
      }
      updateReviewCounts(data?.counts);
      if (data?.reload_drawer && drawerState.url) {
        openDrawer({url: drawerState.url, key: drawerState.key, param: drawerState.param}, "none");
      }
      document.dispatchEvent(new CustomEvent("infomancer:workspace-action", {detail: data || {}}));
    } catch (error) {
      Workspace.toast(error.message || "The action could not be completed.", "error", 6500);
    } finally {
      form.classList.remove("workspace-action-busy");
      submitters.forEach(button => button.disabled = false);
    }
  };

  const enhanceAjaxForms = () => {
    document.addEventListener("submit", (event) => {
      const form = event.target.closest("form[data-workspace-ajax]");
      if (!form) return;
      event.preventDefault();
      submitWorkspaceForm(form);
    });
    document.addEventListener("submit", async (event) => {
      const form = event.target.closest("form[data-workspace-confirm]:not([data-workspace-ajax])");
      if (!form || form.dataset.workspaceConfirmed === "1") return;
      event.preventDefault();
      if (!(await Workspace.confirm(form.dataset.workspaceConfirm))) return;
      form.dataset.workspaceConfirmed = "1";
      form.requestSubmit(event.submitter || undefined);
    });
  };

  const enhanceDrawers = () => {
    document.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-workspace-drawer-url]");
      if (!trigger) return;
      event.preventDefault();
      openDrawer(trigger);
    });
    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-workspace-drawer-close]")) closeDrawer({historyMode: history.state?.workspaceDrawerKey ? "back" : "replace"});
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && drawerState.key) {
        event.preventDefault();
        closeDrawer({historyMode: history.state?.workspaceDrawerKey ? "back" : "replace"});
      }
    });
    window.addEventListener("popstate", () => {
      const param = history.state?.workspaceDrawerParam || drawerState.param || "review";
      const key = new URL(window.location.href).searchParams.get(param);
      if (!key) {
        if (drawerState.key) closeDrawer({historyMode: "none"});
        return;
      }
      const trigger = document.querySelector(`[data-workspace-drawer-key="${CSS.escape(key)}"]`);
      if (trigger) openDrawer(trigger, "none");
    });
    const params = new URL(window.location.href).searchParams;
    for (const param of ["review", "drawer"]) {
      const key = params.get(param);
      if (!key) continue;
      const trigger = document.querySelector(`[data-workspace-drawer-key="${CSS.escape(key)}"]`);
      if (trigger) openDrawer(trigger, "none");
      break;
    }
  };

  const closeMenus = (except = null) => {
    document.querySelectorAll("[data-workspace-menu-root].open").forEach((root) => {
      if (root === except) return;
      root.classList.remove("open");
      root.querySelector("[data-workspace-menu]")?.setAttribute("hidden", "");
      root.querySelector("[data-workspace-menu-toggle]")?.setAttribute("aria-expanded", "false");
    });
  };

  const enhanceContextMenus = () => {
    document.addEventListener("click", (event) => {
      const toggle = event.target.closest("[data-workspace-menu-toggle]");
      if (!toggle) {
        if (!event.target.closest("[data-workspace-menu]")) closeMenus();
        return;
      }
      event.stopPropagation();
      const root = toggle.closest("[data-workspace-menu-root]");
      const menu = root?.querySelector("[data-workspace-menu]");
      if (!root || !menu) return;
      const opening = !root.classList.contains("open");
      closeMenus(root);
      root.classList.toggle("open", opening);
      menu.hidden = !opening;
      toggle.setAttribute("aria-expanded", String(opening));
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMenus();
    });
  };

  const ensureCommandPalette = () => {
    let dialog = document.getElementById("workspace-command-palette");
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.id = "workspace-command-palette";
    dialog.className = "workspace-command-palette";
    dialog.innerHTML = `
      <section class="workspace-command-card">
        <header><span>Command palette</span><kbd>Esc</kbd></header>
        <input type="search" data-workspace-command-input placeholder="Type a command or search the library" autocomplete="off">
        <div class="workspace-command-results" data-workspace-command-results role="listbox"></div>
        <footer><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>⌘/Ctrl K</kbd> toggle</span></footer>
      </section>`;
    document.body.append(dialog);
    return dialog;
  };

  const commandEntries = () => {
    const seen = new Set();
    const entries = [];
    document.querySelectorAll("[data-workspace-nav] a[href], [data-workspace-command]").forEach((node) => {
      const href = node.getAttribute("href");
      const label = node.dataset.workspaceCommand || node.textContent.trim().replace(/\s+/g, " ");
      if (!label) return;
      const key = `${label}|${href || ""}`;
      if (seen.has(key)) return;
      seen.add(key);
      entries.push({label, href, node});
    });
    return entries;
  };

  const enhanceCommandPalette = () => {
    const dialog = ensureCommandPalette();
    const input = dialog.querySelector("[data-workspace-command-input]");
    const results = dialog.querySelector("[data-workspace-command-results]");
    let activeIndex = 0;
    let visible = [];

    const render = () => {
      const query = input.value.trim();
      const normalized = query.casefold ? query.casefold() : query.toLowerCase();
      visible = commandEntries().filter(entry => !normalized || entry.label.toLowerCase().includes(normalized)).slice(0, 12);
      if (query) visible.push({label: `Search library for “${query}”`, href: `/library?q=${encodeURIComponent(query)}&record_search=1`, search: true});
      activeIndex = Math.min(activeIndex, Math.max(0, visible.length - 1));
      results.replaceChildren();
      visible.forEach((entry, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.setAttribute("role", "option");
        button.classList.toggle("active", index === activeIndex);
        const strong = document.createElement("strong");
        strong.textContent = entry.label;
        const small = document.createElement("small");
        small.textContent = entry.search ? "Library search" : (entry.href || "Current page action");
        button.append(strong, small);
        button.addEventListener("mouseenter", () => { activeIndex = index; render(); });
        button.addEventListener("click", () => run(entry));
        results.append(button);
      });
      if (!visible.length) {
        const empty = document.createElement("p");
        empty.className = "workspace-command-empty";
        empty.textContent = "No matching commands.";
        results.append(empty);
      }
    };

    const run = (entry) => {
      dialog.close();
      if (entry.href) window.location.assign(entry.href);
      else entry.node?.click();
    };

    const open = () => {
      if (typeof dialog.showModal !== "function") return;
      if (dialog.open) {
        dialog.close();
        return;
      }
      input.value = "";
      activeIndex = 0;
      render();
      dialog.showModal();
      window.setTimeout(() => input.focus(), 0);
    };
    Workspace.openCommandPalette = open;

    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        open();
        return;
      }
      if (!dialog.open) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const offset = event.key === "ArrowDown" ? 1 : -1;
        activeIndex = Math.max(0, Math.min(visible.length - 1, activeIndex + offset));
        render();
      } else if (event.key === "Enter" && document.activeElement === input && visible[activeIndex]) {
        event.preventDefault();
        run(visible[activeIndex]);
      }
    });
    input.addEventListener("input", () => { activeIndex = 0; render(); });
  };

  const init = () => {
    enhanceAjaxForms();
    enhanceDrawers();
    enhanceContextMenus();
    enhanceCommandPalette();
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once: true});
  else init();
})();
'''

workspace_ui_css = r''':root {
  --workspace-drawer-width: min(470px, 92vw);
}

/* W4 application primitives */
.workspace-toast-host {
  position: fixed;
  z-index: 160;
  top: 18px;
  right: 18px;
  display: grid;
  width: min(390px, calc(100vw - 36px));
  gap: 9px;
  pointer-events: none;
}

.workspace-toast {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: start;
  gap: 12px;
  padding: 13px 14px;
  border: 1px solid #364554;
  border-radius: 11px;
  background: rgba(15, 22, 29, .97);
  box-shadow: 0 18px 50px rgba(0, 0, 0, .38);
  color: var(--text);
  opacity: 0;
  transform: translateY(-8px) scale(.98);
  transition: opacity var(--im-motion), transform var(--im-motion);
  pointer-events: auto;
}
.workspace-toast.visible { opacity: 1; transform: translateY(0) scale(1); }
.workspace-toast.success { box-shadow: inset 3px 0 0 var(--lime), 0 18px 50px rgba(0, 0, 0, .38); }
.workspace-toast.error { box-shadow: inset 3px 0 0 #ff667a, 0 18px 50px rgba(0, 0, 0, .38); }
.workspace-toast button { border: 0; background: transparent; color: var(--muted); font-size: 20px; line-height: 1; cursor: pointer; }

.workspace-confirm-dialog,
.workspace-command-palette {
  width: min(620px, calc(100vw - 32px));
  padding: 0;
  border: 1px solid #344453;
  border-radius: 15px;
  background: #111920;
  color: var(--text);
  box-shadow: 0 30px 90px rgba(0, 0, 0, .58);
}
.workspace-confirm-dialog::backdrop,
.workspace-command-palette::backdrop { background: rgba(3, 7, 10, .72); backdrop-filter: blur(5px); }
.workspace-dialog-card { display: grid; gap: 12px; padding: 24px; }
.workspace-dialog-card h2 { margin: 0; }
.workspace-dialog-card p { margin: 0; color: var(--muted); line-height: 1.55; }
.workspace-dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }

.workspace-drawer {
  position: fixed;
  z-index: 115;
  inset: 0;
  pointer-events: none;
}
.workspace-drawer-scrim {
  position: absolute;
  inset: 0;
  border: 0;
  background: rgba(2, 6, 9, .40);
  opacity: 0;
  transition: opacity var(--im-motion);
  cursor: default;
}
.workspace-drawer-panel {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  display: grid;
  grid-template-rows: auto 1fr;
  width: var(--workspace-drawer-width);
  border-left: 1px solid #2b3946;
  background: #0d141a;
  box-shadow: -24px 0 70px rgba(0, 0, 0, .38);
  transform: translateX(102%);
  transition: transform var(--im-motion);
}
.workspace-drawer.open { pointer-events: auto; }
.workspace-drawer.open .workspace-drawer-scrim { opacity: 1; }
.workspace-drawer.open .workspace-drawer-panel { transform: translateX(0); }
.workspace-drawer-chrome {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 58px;
  padding: 0 16px 0 20px;
  border-bottom: 1px solid var(--line);
}
.workspace-drawer-chrome strong { font-size: 12px; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); }
.workspace-drawer-chrome button { width: 36px; height: 36px; border: 0; border-radius: 9px; background: transparent; color: var(--muted); font-size: 24px; cursor: pointer; }
.workspace-drawer-chrome button:hover { background: rgba(255,255,255,.055); color: var(--text); }
.workspace-drawer-body { overflow: auto; overscroll-behavior: contain; padding: 20px; }
.workspace-drawer-state { min-height: 220px; display: grid; place-items: center; color: var(--muted); text-align: center; }
.workspace-drawer-state.loading span { width: 24px; height: 24px; border: 2px solid #364654; border-top-color: var(--cyan); border-radius: 50%; animation: workspace-spin .8s linear infinite; }
@keyframes workspace-spin { to { transform: rotate(360deg); } }

.workspace-context-menu { position: relative; }
.workspace-context-toggle { width: 34px; height: 34px; border: 0; border-radius: 8px; background: transparent; color: var(--muted); font-weight: 800; cursor: pointer; }
.workspace-context-toggle:hover,
.workspace-context-menu.open .workspace-context-toggle { background: rgba(255,255,255,.055); color: var(--text); }
.workspace-context-popover {
  position: absolute;
  z-index: 40;
  top: calc(100% + 5px);
  right: 0;
  display: grid;
  min-width: 205px;
  padding: 6px;
  border: 1px solid #344453;
  border-radius: 10px;
  background: #111920;
  box-shadow: 0 18px 50px rgba(0,0,0,.38);
}
.workspace-context-popover[hidden] { display: none; }
.workspace-context-popover a,
.workspace-context-popover button { display: block; width: 100%; padding: 9px 10px; border: 0; border-radius: 7px; background: transparent; color: var(--text); font: inherit; text-align: left; text-decoration: none; cursor: pointer; }
.workspace-context-popover a:hover,
.workspace-context-popover button:hover { background: rgba(255,255,255,.055); }

.workspace-action-busy { opacity: .72; }
.workspace-action-busy button { cursor: wait; }

.workspace-command-card { display: grid; }
.workspace-command-card > header,
.workspace-command-card > footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 14px; color: var(--muted); font-size: 12px; }
.workspace-command-card > header { border-bottom: 1px solid var(--line); }
.workspace-command-card > footer { border-top: 1px solid var(--line); justify-content: flex-start; }
.workspace-command-card footer span { display: inline-flex; align-items: center; gap: 4px; }
.workspace-command-card kbd { padding: 2px 6px; border: 1px solid #3b4a58; border-radius: 5px; background: #18212a; color: #c7d0d9; font: 11px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; }
.workspace-command-card input { width: 100%; padding: 17px 18px; border: 0; border-bottom: 1px solid var(--line); outline: 0; background: #0e151b; color: var(--text); font-size: 16px; }
.workspace-command-results { display: grid; max-height: min(52vh, 440px); overflow: auto; padding: 7px; }
.workspace-command-results button { display: grid; gap: 2px; padding: 10px 11px; border: 0; border-radius: 8px; background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.workspace-command-results button.active { background: rgba(81,214,230,.085); box-shadow: inset 2px 0 0 var(--cyan); }
.workspace-command-results small { color: var(--muted); }
.workspace-command-empty { padding: 18px; color: var(--muted); text-align: center; }

/* Align the alpha marker to the optical center of the lockup. */
body.has-app-sidebar .brand .workspace-nav-alpha {
  top: 50%;
  bottom: auto;
  transform: translateY(-50%);
}
@media (min-width: 981px) {
  body.has-app-sidebar.sidebar-collapsed .brand .workspace-nav-alpha {
    top: 50%;
    bottom: auto;
    transform: translateY(-50%);
  }
}

@media (max-width: 700px) {
  .workspace-drawer-panel { width: 100%; top: 12vh; border-top: 1px solid #344453; border-left: 0; border-radius: 16px 16px 0 0; }
  .workspace-toast-host { top: 10px; right: 10px; width: calc(100vw - 20px); }
}

@media (prefers-reduced-motion: reduce) {
  .workspace-toast,
  .workspace-drawer-panel,
  .workspace-drawer-scrim { transition: none; }
  .workspace-drawer-state.loading span { animation-duration: 1.5s; }
}
'''

review_css = r'''/* W3 unified Review Queue */
.review-workspace {
  width: min(100%, 1480px);
  margin-inline: auto;
  padding-bottom: 56px;
}
.review-page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 28px;
  margin: 4px 0 18px;
}
.review-page-head h1 { margin: 2px 0 7px; font-size: clamp(34px, 4vw, 54px); letter-spacing: -.045em; }
.review-page-head p:not(.eyebrow) { max-width: 760px; margin: 0; color: var(--muted); line-height: 1.55; }
.review-page-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.review-page-actions form { margin: 0; }
.review-inline-notice { margin-bottom: 14px; }

.review-summary-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(120px, 1fr));
  border: 1px solid #2b3946;
  border-radius: 13px;
  background: linear-gradient(145deg, rgba(18,25,33,.94), rgba(10,15,21,.96));
  overflow: hidden;
}
.review-summary-strip > div { display: grid; gap: 2px; min-height: 86px; align-content: center; padding: 13px 18px; border-right: 1px solid var(--line); }
.review-summary-strip > div:last-child { border-right: 0; }
.review-summary-strip strong { font-size: 27px; line-height: 1; }
.review-summary-strip span { color: var(--muted); font-size: 12px; }
.review-summary-strip .critical strong { color: #ff7a88; }
.review-summary-strip .warning strong { color: #f2c96f; }
.review-health-score strong { color: var(--cyan); }
.review-meta-line { display: flex; justify-content: space-between; gap: 16px; padding: 9px 3px 2px; color: var(--muted); font-size: 12px; }

.review-bucket-tabs {
  display: flex;
  gap: 5px;
  margin: 18px 0 9px;
  padding-bottom: 8px;
  overflow-x: auto;
  border-bottom: 1px solid var(--line);
}
.review-bucket-tabs button { display: inline-flex; align-items: center; gap: 7px; padding: 8px 11px; border: 0; border-radius: 8px; background: transparent; color: var(--muted); font: inherit; font-size: 13px; cursor: pointer; white-space: nowrap; }
.review-bucket-tabs button:hover { color: var(--text); background: rgba(255,255,255,.035); }
.review-bucket-tabs button.active { color: var(--text); background: rgba(185,245,66,.08); box-shadow: inset 0 -2px 0 var(--lime); }
.review-bucket-tabs b { min-width: 20px; padding: 1px 6px; border-radius: 999px; background: rgba(255,255,255,.06); font-size: 10px; text-align: center; }

.review-filterbar {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) repeat(3, minmax(125px, auto)) auto auto;
  align-items: end;
  gap: 8px;
  padding: 12px;
  border: 1px solid #283641;
  border-radius: 11px;
  background: rgba(14,20,26,.72);
}
.review-filterbar label { display: grid; gap: 5px; color: var(--muted); font-size: 10px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.review-filterbar input,
.review-filterbar select { min-height: 38px; border: 1px solid #344452; border-radius: 8px; background: #0b1218; color: var(--text); }
.review-filterbar input { width: 100%; padding: 7px 10px; }
.review-filterbar select { padding: 6px 28px 6px 9px; }
.review-results-head { display: flex; justify-content: space-between; gap: 16px; margin: 18px 3px 8px; color: var(--muted); font-size: 12px; }
.review-results-head strong { color: var(--text); }

.review-queue-list {
  border: 1px solid #2a3845;
  border-radius: 13px;
  background: rgba(12,18,24,.76);
  overflow: visible;
}
.review-queue-item {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 46px;
  align-items: stretch;
  border-bottom: 1px solid var(--line);
}
.review-queue-item:last-of-type { border-bottom: 0; }
.review-queue-item:hover { background: rgba(255,255,255,.024); }
.review-item-open {
  display: grid;
  grid-template-columns: 5px minmax(250px, .9fr) minmax(260px, 1.1fr);
  align-items: center;
  gap: 14px;
  width: 100%;
  min-height: 92px;
  padding: 13px 10px 13px 0;
  border: 0;
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.review-item-signal { align-self: stretch; width: 3px; margin-left: 1px; border-radius: 0 3px 3px 0; background: #71808e; }
.severity-critical .review-item-signal { background: #ff667a; }
.severity-warning .review-item-signal { background: #e7bb58; }
.review-item-copy { display: grid; min-width: 0; gap: 5px; }
.review-item-copy > strong { overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.review-item-copy > span:last-child { overflow: hidden; color: var(--muted); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.review-item-labels { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.review-item-labels b,
.review-item-labels i { padding: 2px 6px; border-radius: 999px; font-size: 9px; font-style: normal; font-weight: 850; letter-spacing: .07em; text-transform: uppercase; }
.review-item-labels b { color: #d7e0e7; background: rgba(255,255,255,.07); }
.review-item-labels i { color: #8293a3; background: rgba(255,255,255,.035); }
.review-item-recommendation { display: -webkit-box; overflow: hidden; color: #a9b6c1; font-size: 12px; line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.review-queue-item > .workspace-context-menu { align-self: center; justify-self: center; }
.review-empty-state { padding: 48px 24px; text-align: center; }
.review-empty-state strong { display: block; margin-bottom: 7px; font-size: 17px; }
.review-empty-state p { margin: 0; color: var(--muted); }

.review-drawer-content { display: grid; gap: 18px; }
.review-drawer-heading { padding-bottom: 16px; border-bottom: 1px solid var(--line); }
.review-drawer-heading h2 { margin: 9px 0 6px; font-size: 23px; line-height: 1.16; letter-spacing: -.025em; }
.review-drawer-heading p { margin: 0; color: var(--muted); }
.review-drawer-section { display: grid; gap: 7px; }
.review-drawer-section h3 { margin: 0; color: #d7e0e7; font-size: 11px; letter-spacing: .1em; text-transform: uppercase; }
.review-drawer-section p { margin: 0; color: #a8b4bf; line-height: 1.55; }
.review-next-step { padding: 13px 14px; border: 1px solid rgba(81,214,230,.18); border-radius: 10px; background: rgba(81,214,230,.045); }
.review-evidence { padding-top: 4px; }
.review-evidence summary { color: #d7e0e7; font-size: 11px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; cursor: pointer; }
.review-evidence dl,
.review-file-compare dl { display: grid; gap: 0; margin: 8px 0 0; }
.review-evidence dl > div,
.review-file-compare dl > div { display: grid; grid-template-columns: minmax(105px,.42fr) minmax(0,1fr); gap: 10px; padding: 7px 0; border-top: 1px solid rgba(255,255,255,.055); }
.review-evidence dt,
.review-file-compare dt { color: var(--muted); font-size: 11px; }
.review-evidence dd,
.review-file-compare dd { margin: 0; overflow-wrap: anywhere; color: #c4cdd5; font-size: 11px; }
.review-drawer-actions { display: flex; gap: 8px; flex-wrap: wrap; padding-top: 2px; }
.review-feedback-panel { border-top: 1px solid var(--line); padding-top: 14px; }
.review-feedback-panel > summary { color: #cbd4dc; font-weight: 700; cursor: pointer; }
.review-feedback-panel form { display: grid; gap: 10px; margin-top: 12px; }
.review-feedback-panel label { display: grid; gap: 5px; color: var(--muted); font-size: 11px; }
.review-feedback-panel select,
.review-feedback-panel textarea { width: 100%; border: 1px solid #344452; border-radius: 8px; background: #0b1218; color: var(--text); }
.review-feedback-panel select { min-height: 38px; }
.review-feedback-panel textarea { padding: 8px 9px; resize: vertical; }
.review-file-compare { display: grid; gap: 9px; }
.review-file-compare article { padding: 11px 12px; border: 1px solid #2c3a47; border-radius: 9px; background: rgba(255,255,255,.022); }
.review-file-compare article > strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.review-file-compare article > span { color: var(--muted); font-size: 11px; }
.review-duplicate-decisions { border-top: 1px solid var(--line); padding-top: 15px; }
.review-duplicate-decisions .button-row { margin-top: 4px; }
.review-duplicate-decisions form { margin: 0; }
.review-secondary-link { color: var(--cyan); font-size: 12px; text-decoration: none; }
.review-restore-form { border-top: 1px solid var(--line); padding-top: 14px; }

@media (max-width: 1000px) {
  .review-summary-strip { grid-template-columns: repeat(3, 1fr); }
  .review-summary-strip > div:nth-child(3) { border-right: 0; }
  .review-summary-strip > div:nth-child(n+4) { border-top: 1px solid var(--line); }
  .review-filterbar { grid-template-columns: minmax(220px,1fr) repeat(2, minmax(120px,auto)); }
  .review-item-open { grid-template-columns: 5px minmax(230px,.9fr) minmax(210px,1.1fr); }
}
@media (max-width: 760px) {
  .review-page-head { align-items: flex-start; flex-direction: column; }
  .review-page-actions { justify-content: flex-start; }
  .review-summary-strip { grid-template-columns: repeat(2,1fr); }
  .review-summary-strip > div { border-top: 1px solid var(--line); }
  .review-summary-strip > div:nth-child(-n+2) { border-top: 0; }
  .review-summary-strip > div:nth-child(even) { border-right: 0; }
  .review-filterbar { grid-template-columns: 1fr 1fr; }
  .review-search { grid-column: 1 / -1; }
  .review-item-open { grid-template-columns: 4px minmax(0,1fr); gap: 10px; }
  .review-item-recommendation { grid-column: 2; }
  .review-meta-line,
  .review-results-head { align-items: flex-start; flex-direction: column; }
}
'''

review_tests = r'''from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.duplicates import DuplicateService
from app.mie import MediaIntelligenceEngine
from app.review_queue import ReviewQueue


ROOT = Path(__file__).resolve().parent.parent


class ReviewQueueServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "review.db")
        self.db.initialize()
        with self.db.connect() as conn:
            conn.execute("INSERT INTO roots(id,path,kind,label,enabled) VALUES (1,'/media/movies','movie','Movies',1)")
            conn.execute("""INSERT INTO titles(id,root_id,kind,title,folder_path,updated_at)
                            VALUES (1,1,'movie','Example Movie','/media/movies/Example Movie',CURRENT_TIMESTAMP)""")
            conn.execute("""INSERT INTO files(id,title_id,path,filename,extension,size_bytes,modified_at,seen_scan)
                            VALUES (1,1,'/media/movies/Example Movie/a.mkv','a.mkv','mkv',1000,1,'scan')""")
            conn.execute("""INSERT INTO files(id,title_id,path,filename,extension,size_bytes,modified_at,seen_scan)
                            VALUES (2,1,'/media/movies/Example Movie/b.mkv','b.mkv','mkv',1000,1,'scan')""")
            conn.execute("""INSERT INTO mie_findings(
                         id,fingerprint,rule_key,category,severity,root_id,title_id,
                         summary,explanation,recommendation,evidence_json,status,
                         first_seen_at,last_seen_at)
                       VALUES (1,'test:1','metadata-identifiers-missing','identity','warning',1,1,
                         'Example Movie has no provider identifier','No provider ID is saved.',
                         'Review a provider match.','{}','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
            conn.execute("""INSERT INTO metadata_refresh_queue(
                         title_id,status,provider,error,requested_at,completed_at)
                       VALUES (1,'failed','tvdb','Provider unavailable',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        self.queue = ReviewQueue(self.db, MediaIntelligenceEngine(self.db), DuplicateService(self.db))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_librarian_queue_unifies_mie_metadata_and_duplicates(self):
        view = self.queue.view(include_librarian=True)
        sources = {item["source"] for item in view["items"]}
        self.assertIn("finding", sources)
        self.assertIn("metadata", sources)
        self.assertIn("duplicate", sources)
        self.assertGreaterEqual(view["counts"]["warning"], 2)
        self.assertGreaterEqual(view["bucket_counts"]["duplicates"], 1)

    def test_member_queue_excludes_duplicate_cleanup(self):
        view = self.queue.view(include_librarian=False)
        self.assertNotIn("duplicate", {item["source"] for item in view["items"]})
        self.assertTrue(any(item["bucket"] == "matching" for item in view["items"]))

    def test_queue_filters_and_drawer_lookup(self):
        view = self.queue.view(bucket="matching", severity="warning", q="example", include_librarian=True)
        self.assertEqual(view["visible_count"], 2)  # MIE finding plus failed metadata title search match
        finding = self.queue.get_item("finding", "1", include_librarian=True)
        self.assertIsNotNone(finding)
        self.assertEqual(finding["bucket"], "matching")
        duplicate = next(item for item in self.queue.view(include_librarian=True)["items"] if item["source"] == "duplicate")
        pair = duplicate["item_id"]
        self.assertIsNotNone(self.queue.get_item("duplicate", pair, include_librarian=True))
        self.assertIsNone(self.queue.get_item("duplicate", pair, include_librarian=False))


class ReviewWorkspaceContractTests(unittest.TestCase):
    def test_review_workspace_and_w4_assets_are_wired(self):
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        routes = (ROOT / "app" / "routes" / "review.py").read_text(encoding="utf-8")
        template = (ROOT / "app" / "templates" / "review.html").read_text(encoding="utf-8")
        drawer = (ROOT / "app" / "templates" / "_review_drawer.html").read_text(encoding="utf-8")
        ui = (ROOT / "app" / "static" / "workspace-ui.js").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace-ui.css").read_text(encoding="utf-8")
        self.assertIn("path='workspace-ui.css'", base)
        self.assertIn("path='review.css'", base)
        self.assertIn("path='workspace-ui.js'", base)
        self.assertIn('href="/review" title="Review"', base)
        self.assertIn('@router.get("/review"', routes)
        self.assertIn('/review/items/{source}/{item_id}', routes)
        self.assertIn('/api/review/findings/{finding_id}/dismiss', routes)
        self.assertIn('/api/review/duplicates/{file_a_id}/{file_b_id}/decision', routes)
        self.assertIn("data-review-queue", template)
        self.assertIn("data-workspace-drawer-url", template)
        self.assertIn("data-workspace-ajax", drawer)
        self.assertIn("Workspace.toast", ui)
        self.assertIn("Workspace.confirm", ui)
        self.assertIn("workspace-command-palette", ui)
        self.assertIn("data-workspace-menu-toggle", ui)
        self.assertIn("workspace-drawer", styles)
        self.assertIn("top: 50%", styles)


if __name__ == "__main__":
    unittest.main()
'''

write("app/review_queue.py", review_queue_py)
write("app/templates/review.html", review_html)
write("app/templates/_review_drawer.html", review_drawer_html)
write("app/static/workspace-ui.js", workspace_ui_js)
write("app/static/workspace-ui.css", workspace_ui_css)
write("app/static/review.css", review_css)
write("tests/test_review_workspace.py", review_tests)

base = ROOT / "app/templates/base.html"
replace_once(
    base,
    "  <link rel=\"stylesheet\" href=\"{{ url_for('static', path='workspace.css') }}?v={{ static_version }}\">\n",
    "  <link rel=\"stylesheet\" href=\"{{ url_for('static', path='workspace.css') }}?v={{ static_version }}\">\n"
    "  <link rel=\"stylesheet\" href=\"{{ url_for('static', path='workspace-ui.css') }}?v={{ static_version }}\">\n"
    "  <link rel=\"stylesheet\" href=\"{{ url_for('static', path='review.css') }}?v={{ static_version }}\">\n",
)
replace_once(
    base,
    "  <script src=\"{{ url_for('static', path='workspace.js') }}?v={{ static_version }}\" defer></script>\n",
    "  <script src=\"{{ url_for('static', path='workspace.js') }}?v={{ static_version }}\" defer></script>\n"
    "  <script src=\"{{ url_for('static', path='workspace-ui.js') }}?v={{ static_version }}\" defer></script>\n",
)
replace_once(
    base,
    "{% set workspace_review_active = request.url.path.startswith('/library-health') or request.url.path.startswith('/duplicates') or request.url.path.startswith('/bulk-match') or request.url.path.startswith('/movies/bulk-match') or request.url.path.startswith('/shows/bulk-match') %}",
    "{% set workspace_review_active = request.url.path.startswith('/review') or request.url.path.startswith('/library-health') or request.url.path.startswith('/duplicates') or request.url.path.startswith('/bulk-match') or request.url.path.startswith('/movies/bulk-match') or request.url.path.startswith('/shows/bulk-match') %}",
)
replace_once(
    base,
    '<a href="/library-health" title="Review"{% if workspace_review_active %} class="domain-current"{% endif %}>',
    '<a href="/review" title="Review"{% if request.url.path == \'/review\' %} class="active" aria-current="page"{% elif workspace_review_active %} class="domain-current"{% endif %}>',
)
replace_once(
    base,
    '            <div class="workspace-nav-secondary">\n              <a href="/library-health" title="Library Health"',
    '            <div class="workspace-nav-secondary">\n              <a href="/review" title="Review Queue"{% if request.url.path == \'/review\' %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6h14M5 12h14M5 18h9"></path><path d="m17 16 2 2 3-4"></path></svg><span>Review Queue</span></a>\n              <a href="/library-health" title="Library Health"',
)

review_routes = ROOT / "app/routes/review.py"
replace_once(
    review_routes,
    "from ..access import require_librarian\nfrom .context import RouteContext\n",
    "from ..access import require_librarian\nfrom ..review_queue import ReviewQueue\nfrom .context import RouteContext\n",
)
replace_once(
    review_routes,
    "    tv_match_lock = ctx.live(\"tv_match_lock\")\n\n    def librarian_get",
    "    tv_match_lock = ctx.live(\"tv_match_lock\")\n    review_queue = ReviewQueue(db, mie, duplicates)\n\n    def librarian_get",
)
route_marker = "    @router.get(\"/library-health\", response_class=HTMLResponse)\n"
route_block = r'''    @router.get("/review", response_class=HTMLResponse)
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
        counts = review_queue.view(include_librarian=True)["counts"]
        counts["buckets"] = review_queue.view(include_librarian=True)["bucket_counts"]
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

'''
text = review_routes.read_text(encoding="utf-8")
if route_marker not in text:
    raise RuntimeError("Review route insertion marker not found")
review_routes.write_text(text.replace(route_marker, route_block + route_marker, 1), encoding="utf-8")

workspace_tests = ROOT / "tests/test_workspace_ui.py"
replace_once(
    workspace_tests,
    "        self.assertIn(\"path='workspace.js'\", base)\n",
    "        self.assertIn(\"path='workspace.js'\", base)\n        self.assertIn(\"path='workspace-ui.js'\", base)\n",
)
replace_once(
    workspace_tests,
    "        for href in (\"/movies\", \"/shows\", \"/collections\", \"/favorites\", \"/duplicates\", \"/bulk-match\"):\n",
    "        for href in (\"/movies\", \"/shows\", \"/collections\", \"/favorites\", \"/review\", \"/duplicates\", \"/bulk-match\"):\n",
)

workspace_doc = ROOT / "docs/WORKSPACE.md"
if workspace_doc.exists():
    text = workspace_doc.read_text(encoding="utf-8")
    text += r'''

## W3 + W4: Unified Review and application interactions

W3 makes `/review` the primary decision surface. It adapts existing MIE findings, live duplicate candidates, and failed metadata work into one filtered queue without introducing a second source of truth. Specialist pages remain available for deep workflows.

W4 adds reusable Workspace primitives: a server-backed right drawer, same-origin AJAX forms, confirmation dialogs, contextual menus, toasts, and a Ctrl/Cmd+K command palette. Review uses these primitives first, but they are intentionally generic so Library, Sources, and later Operation History can reuse them.

Review GETs remain read-only. Librarian-only state changes use dedicated CSRF-protected POST routes and preserve the route-level authorization boundary established during 0.7 hardening.
'''
    workspace_doc.write_text(text, encoding="utf-8")

print("W3/W4 files written and existing Workspace files patched.")

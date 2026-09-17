from __future__ import annotations

import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


class Cycle1MIEReviewTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        templates = Path(__file__).resolve().parents[1] / "app" / "templates"
        cls.environment = Environment(
            loader=FileSystemLoader(templates),
            autoescape=select_autoescape(["html"]),
        )

    def render(self, summary: dict) -> str:
        return self.environment.get_template("_mie_attention.html").render(
            summary=summary,
        )

    def test_attention_panel_explains_score_trend_and_links_to_title(self) -> None:
        html = self.render({
            "last_analyzed_at": "2026-09-17 00:00:00",
            "attention_snapshot_run_id": 9,
            "attention_titles": [{
                "title_id": 42,
                "title_name": "Example Movie",
                "kind": "movie",
                "score": 72,
                "critical_count": 1,
                "warning_count": 1,
                "information_count": 0,
                "trend": "worsening",
                "score_delta": -8,
                "recent_scores": [88, 80, 72],
            }],
        })

        self.assertIn("Titles needing attention", html)
        self.assertIn("Example Movie", html)
        self.assertIn("Movie · 1 critical · 1 warning · 0 information", html)
        self.assertIn("Down 8 points since the previous analysis", html)
        self.assertIn("Recent scores: 88 → 80 → 72", html)
        self.assertIn('href="/titles/42"', html)
        self.assertIn("does not change your media files", html)

    def test_attention_panel_has_clear_empty_state_after_snapshot_analysis(self) -> None:
        html = self.render({
            "last_analyzed_at": "2026-09-17 00:00:00",
            "attention_snapshot_run_id": 9,
            "attention_titles": [],
        })

        self.assertIn("Titles needing attention", html)
        self.assertIn("No title-level findings currently reduce a title health score", html)
        self.assertNotIn("has not been recorded yet", html)

    def test_attention_panel_asks_for_refresh_when_history_is_not_recorded(self) -> None:
        html = self.render({
            "last_analyzed_at": "2026-09-16 23:00:00",
            "attention_snapshot_run_id": None,
            "attention_titles": [],
        })

        self.assertIn("Titles needing attention", html)
        self.assertIn("Title-level health history has not been recorded yet", html)
        self.assertIn("Refresh the analysis", html)
        self.assertNotIn("No title-level findings currently reduce", html)

    def test_attention_panel_is_absent_before_first_analysis(self) -> None:
        html = self.render({
            "last_analyzed_at": None,
            "attention_snapshot_run_id": None,
            "attention_titles": [],
        })

        self.assertNotIn("Titles needing attention", html)


if __name__ == "__main__":
    unittest.main()

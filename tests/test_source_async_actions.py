from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SourceAsyncActionContracts(unittest.TestCase):
    def test_sources_load_async_action_controller_without_removing_form_fallbacks(self):
        template = (ROOT / "app/templates/sources.html").read_text(encoding="utf-8")

        self.assertIn('action="/scan-all"', template)
        self.assertIn('action="/roots/{{ root.id }}/check"', template)
        self.assertIn('action="/roots/{{ root.id }}/scan"', template)
        self.assertIn("source-actions.js", template)
        self.assertIn("?v={{ static_version }}", template)

    def test_source_actions_intercept_scans_and_connection_checks_in_place(self):
        script = (ROOT / "app/static/source-actions.js").read_text(encoding="utf-8")

        self.assertIn('form[action="/scan-all"]', script)
        self.assertIn('form[action$="/scan"]', script)
        self.assertIn('form[action$="/check"]', script)
        self.assertIn('event.preventDefault()', script)
        self.assertIn('await fetch(form.action', script)
        self.assertIn('body: new FormData(form)', script)
        self.assertIn('redirect: "follow"', script)
        self.assertIn('input[name="csrf_token"]', script)
        self.assertIn('headers["X-CSRF-Token"] = csrfToken', script)
        self.assertIn('new DOMParser().parseFromString(html, "text/html")', script)
        self.assertIn('refreshConnectionState(freshDocument, form)', script)
        self.assertIn('optimisticTaskWidget(kind, label)', script)
        self.assertIn('Starting a scan of all sources', script)

    def test_source_async_feedback_has_motion_and_reduced_motion_contract(self):
        css = (ROOT / "app/static/sources.css").read_text(encoding="utf-8")

        self.assertIn(".source-live-status", css)
        self.assertIn('[data-state="working"]', css)
        self.assertIn('[data-state="success"]', css)
        self.assertIn('[data-state="error"]', css)
        self.assertIn(".source-action-busy", css)
        self.assertIn(".root-row.source-row-working", css)
        self.assertIn("@keyframes source-status-in", css)
        self.assertIn("@media(prefers-reduced-motion:reduce)", css)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent

class DetailInteractionPolishTests(unittest.TestCase):
    def test_long_overview_has_accessible_full_text_dialog(self):
        template = (ROOT / "app/templates/detail.html").read_text(encoding="utf-8")
        self.assertIn('id="overview-more"', template)
        self.assertIn('See full overview', template)
        self.assertIn('id="overview-dialog"', template)
        self.assertIn('aria-labelledby="overview-dialog-title"', template)
        self.assertIn('overview.scrollHeight > overview.clientHeight + 1', template)

    def test_movie_action_menu_is_viewport_aware(self):
        template = (ROOT / "app/templates/detail.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/workspace-ui.css").read_text(encoding="utf-8")
        self.assertIn('const fitMovieMenu = (menu) =>', template)
        self.assertIn('naturalHeight > roomBelow && roomAbove > roomBelow', template)
        self.assertIn('menu-open-up', template)
        self.assertIn('.movie-detail-menu.menu-open-up > .series-menu-popover', css)
        self.assertIn('overflow-y: auto', css)

    def test_person_hover_preview_cancels_pending_open_on_leave(self):
        script = (ROOT / "app/static/workspace.js").read_text(encoding="utf-8")
        schedule = script.split('const scheduleClose = () => {', 1)[1].split('};', 1)[0]
        self.assertIn('window.clearTimeout(openTimer);', schedule)
        self.assertIn('window.setTimeout(closeNow, 120)', schedule)
        self.assertIn('event.key === "Escape" && !popover.hidden', script)

if __name__ == "__main__":
    unittest.main()

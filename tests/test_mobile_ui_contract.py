import unittest
from pathlib import Path


class MobileUiContractTests(unittest.TestCase):
    def test_organize_dialog_keeps_progressive_fallback(self):
        base = Path("app/templates/base.html").read_text(encoding="utf-8")
        organize = Path("app/templates/organize.html").read_text(encoding="utf-8")
        libraries = Path("app/templates/title_libraries.html").read_text(encoding="utf-8")
        script = Path("app/static/organize-dialog.js").read_text(encoding="utf-8")

        self.assertIn('id="organize-dialog"', base)
        self.assertIn("data-organize-content", organize)
        self.assertIn("data-organize-content", libraries)
        self.assertIn("(?:organize|libraries)", script)
        self.assertIn("typeof dialog.showModal", script)
        self.assertIn("window.location.assign(url)", script)
        self.assertIn('dialog.addEventListener("cancel"', script)
        self.assertIn("Save Changes", organize)
        self.assertIn("Create and apply tags", organize)
        self.assertIn("Sort title", organize)
        self.assertNotIn("Custom order", organize)
        self.assertNotIn('class="button" href="/tags">Manage tags', organize)
        sort_dialog = Path("app/templates/sort_titles_dialog.html").read_text(encoding="utf-8")
        library = Path("app/templates/library.html").read_text(encoding="utf-8")
        self.assertIn("data-sort-title-order", sort_dialog)
        self.assertIn('title="Drag to reorder"', sort_dialog)
        self.assertIn('class="sort-title-drag" draggable="true"', sort_dialog)
        self.assertIn('draggable="false"', sort_dialog)
        self.assertIn("animateSortRowPositions", script)
        self.assertIn('name="sequence_letter"', sort_dialog)
        self.assertIn('name="number_style"', sort_dialog)
        self.assertIn("Append Sort Titles", library)
        self.assertIn('id="deselect-library-titles"', library)
        self.assertIn('class="library-display-toolbar"', library)
        self.assertIn("library-cover-choice", library)
        stylesheet = Path("app/static/library.css").read_text(encoding="utf-8")
        self.assertNotIn(".cover-card:focus-within .cover-card-actions", stylesheet)
        self.assertIn(".cover-card:has(.cover-row-menu:focus-within) .cover-card-actions", stylesheet)
        self.assertIn(".cover-card { position:relative; z-index:0; isolation:isolate;", stylesheet)
        self.assertIn("infomancer:open-dialog", script)

    def test_mobile_library_display_toolbar_keeps_compact_control_rows(self):
        toolbar = Path("app/static/library-selection-toolbar.css").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 760px)", toolbar)
        self.assertIn("display: contents;", toolbar)
        self.assertIn("grid-column: 1 / -1;", toolbar)
        self.assertIn("grid-row: 2;", toolbar)
        self.assertIn("white-space: nowrap;", toolbar)
        self.assertIn("min-width: 132px;", toolbar)
        self.assertIn("border-top: 1px solid var(--line);", toolbar)
        self.assertIn("grid-row: auto;", toolbar)

    def test_mobile_task_widget_and_footer_have_explicit_states(self):
        base = Path("app/templates/base.html").read_text(encoding="utf-8")
        progress = Path("app/static/progress.css").read_text(encoding="utf-8")
        header = Path("app/static/header.css").read_text(encoding="utf-8")

        self.assertIn('aria-label="No background tasks or notifications"', base)
        self.assertIn('classList.add("has-attention")', base)
        self.assertIn(".task-widget-toggle .task-card-copy", progress)
        self.assertIn("footer a:hover", header)

    def test_header_uses_megaphone_for_announcements_and_bell_for_notifications(self):
        base = Path("app/templates/base.html").read_text(encoding="utf-8")
        task_styles = Path("app/static/task-widget.css").read_text(encoding="utf-8")

        self.assertIn('class="announcement-button"', base)
        self.assertIn('M4 13h3l9 5V6l-9 5H4z', base)
        self.assertIn('.announcement-button>svg{display:block}', task_styles)
        self.assertIn('.announcement-button::before{content:none!important;display:none!important}', task_styles)
        self.assertIn('.topbar .task-widget-toggle::before', task_styles)
        self.assertIn('-webkit-mask:url(', task_styles)
        self.assertIn('.topbar .task-widget .task-dot{position:absolute', task_styles)
        self.assertIn('@media(max-width:760px)', task_styles)

    def test_installation_name_is_hidden_but_compatibility_key_remains(self):
        settings = Path("app/templates/settings.html").read_text(encoding="utf-8")
        setup = Path("app/templates/getting_started.html").read_text(encoding="utf-8")
        app_settings = Path("app/app_settings.py").read_text(encoding="utf-8")

        self.assertNotIn('name="installation_name"', settings)
        self.assertNotIn('name="installation_name"', setup)
        self.assertIn('"installation_name"', app_settings)


if __name__ == "__main__":
    unittest.main()

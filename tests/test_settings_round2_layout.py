from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _round2_css() -> str:
    return (ROOT / "app/static/settings-round2.css").read_text(encoding="utf-8")


def test_settings_shell_loads_round2_polish():
    css = (ROOT / "app/static/settings.css").read_text(encoding="utf-8")

    assert '@import url("settings-round2.css");' in css


def test_system_fingerprinting_uses_focused_workspace_width():
    css = _round2_css()

    assert "body #fingerprints{" in css
    assert "max-width:1040px" in css
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in css
    assert "body #fingerprints .hashing-command-bar" in css


def test_file_protection_modes_use_selected_infomancer_rows():
    css = _round2_css()

    assert "body #safety .safety-mode-choice{" in css
    assert "body #safety .safety-mode-choice:has(input:checked)" in css
    assert "box-shadow:inset 3px 0 0 var(--lime)" in css
    assert "grid-template-columns:minmax(150px,.32fr) minmax(0,1fr)" in css


def test_media_export_pair_has_grouped_actions_and_footer():
    css = _round2_css()

    assert "body #media-information + .settings-card .export-actions" in css
    assert "display:inline-flex" in css
    assert "body #media-information + .settings-card .export-help-note" in css
    assert "border-top:1px solid var(--line)" in css

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_uses_one_supported_home_surface():
    route = (ROOT / "app/routes/dashboard.py").read_text(encoding="utf-8")
    template = (ROOT / "app/templates/dashboard_command.html").read_text(encoding="utf-8")

    assert 'TemplateResponse(request, "dashboard_command.html"' in route
    assert 'query_params.get("layout"' not in route
    assert 'getattr(request.state.user, "home_layout"' not in route
    assert "dashboard_layout" not in route
    assert "home-compare-switch" not in template
    assert "layout=old" not in template
    assert "layout=classic" not in template


def test_retired_dashboard_comparison_templates_are_removed():
    templates = ROOT / "app/templates"
    assert not (templates / "dashboard_classic.html").exists()
    assert not (templates / "dashboard_old_test.html").exists()

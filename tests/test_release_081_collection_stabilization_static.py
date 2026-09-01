from pathlib import Path


def test_release_collection_assets_are_wired_to_live_surfaces():
    root = Path(__file__).resolve().parents[1]
    workspace = (root / "app/static/workspace-ui.js").read_text(encoding="utf-8")
    collections = (root / "app/templates/collections.html").read_text(encoding="utf-8")
    routes = (root / "app/routes/__init__.py").read_text(encoding="utf-8")

    assert "release-081-collections.css" in workspace
    assert "release-081-collection-polish.js" in workspace
    assert "release-081-library-actions.js" in workspace
    assert 'class="panel smart-collection-create"' in collections
    assert routes.index("build_release_081_collection_undo_router,") < routes.index("build_collections_router,")

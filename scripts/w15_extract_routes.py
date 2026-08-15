from __future__ import annotations

import ast
import builtins
import re
import textwrap
from collections import defaultdict
from pathlib import Path

MAIN = Path("app/main.py")
ROUTES_DIR = Path("app/routes")
TEST = Path("tests/test_route_decomposition.py")
DOC = Path("docs/WORKSPACE.md")

MODULE_ORDER = (
    "system",
    "operations",
    "dashboard",
    "review",
    "library",
    "settings",
    "collections",
    "titles",
)


def route_decorator(node: ast.AST) -> bool:
    for dec in getattr(node, "decorator_list", []):
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "app" and func.attr in {"get", "post", "put", "patch", "delete"}:
                return True
        if isinstance(func, ast.Name) and func.id in {"librarian_get", "librarian_post"}:
            return True
    return False


def module_for(line: int) -> str | None:
    if 1760 <= line <= 1812 or 2257 <= line <= 2280 or line in range(2498, 2500):
        return "system"
    if 2503 <= line <= 2957:
        return "operations"
    if 2989 <= line <= 3052:
        return "dashboard"
    if 3056 <= line <= 3891:
        return "review"
    if 4084 <= line <= 4136:
        return "library"
    if 4140 <= line <= 4966:
        return "settings"
    if 4990 <= line <= 5171:
        return "library"
    if 5271 <= line <= 5947:
        return "collections"
    if 5951 <= line <= 6256:
        return "titles"
    if 6260 <= line <= 6795:
        return "library"
    if 6815 <= line <= 8144:
        if 7326 <= line <= 7621:
            return "review"
        return "titles"
    return None


def source_bounds(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int]:
    starts = [node.lineno]
    starts.extend(dec.lineno for dec in node.decorator_list)
    return min(starts), node.end_lineno or node.lineno


def loaded_names(node: ast.AST) -> set[str]:
    names = {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }
    return names - set(dir(builtins))


text = MAIN.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
tree = ast.parse(text)
route_nodes = [
    node for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and route_decorator(node)
]

selected: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = defaultdict(list)
remaining_route_names: set[str] = set()
for node in route_nodes:
    module = module_for(node.lineno)
    if module:
        selected[module].append(node)
    else:
        remaining_route_names.add(node.name)

selected_names = {node.name for nodes in selected.values() for node in nodes}
if len(selected_names) < 120:
    raise SystemExit(f"W1.5 selection unexpectedly small: {len(selected_names)} route functions")

# Guard against moving a route that another still-in-main function directly calls.
for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name in selected_names:
        continue
    refs = loaded_names(node) & selected_names
    if refs:
        raise SystemExit(f"Remaining function {node.name} calls extracted route(s): {sorted(refs)}")

# Guard cross-router route calls. Same-router calls are safe closure references.
owner = {node.name: module for module, nodes in selected.items() for node in nodes}
for module, nodes in selected.items():
    for node in nodes:
        refs = loaded_names(node) & selected_names
        bad = {name for name in refs if owner[name] != module}
        if bad:
            raise SystemExit(f"Cross-router route call from {node.name}: {sorted(bad)}")

ROUTES_DIR.mkdir(parents=True, exist_ok=True)
context_source = '''from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


class RouteContext:
    """Live view of application services/helpers used while routers are assembled.

    W1.5 keeps construction in main.py while route behavior moves into domain modules.
    Each module binds only the names its handlers need. The mapping stays live so
    compatibility handler aliases can be published as routers are registered.
    """

    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        self._namespace = namespace

    def get(self, name: str) -> Any:
        return self._namespace.get(name)
'''
(ROUTES_DIR / "context.py").write_text(context_source, encoding="utf-8")

removed_lines: set[int] = set()
module_stats: dict[str, tuple[int, int]] = {}

for module in MODULE_ORDER:
    nodes = sorted(selected.get(module, []), key=lambda item: item.lineno)
    if not nodes:
        raise SystemExit(f"No routes selected for module {module}")
    module_route_names = {node.name for node in nodes}
    globals_needed: set[str] = set()
    chunks: list[str] = []
    moved_line_count = 0
    for node in nodes:
        start, end = source_bounds(node)
        removed_lines.update(range(start, end + 1))
        moved_line_count += end - start + 1
        raw = "".join(lines[start - 1:end]).rstrip()
        raw = raw.replace("@app.get", "@router.get")
        raw = raw.replace("@app.post", "@router.post")
        raw = raw.replace("@app.put", "@router.put")
        raw = raw.replace("@app.patch", "@router.patch")
        raw = raw.replace("@app.delete", "@router.delete")
        chunks.append(textwrap.indent(raw, "    "))
        globals_needed.update(loaded_names(node))

    globals_needed -= module_route_names
    globals_needed -= {"router", "librarian_get", "librarian_post"}

    aliases = "\n".join(
        f'    {name} = ctx.get("{name}")'
        for name in sorted(globals_needed)
    )
    handlers = "\n".join(
        f'        "{node.name}": {node.name},'
        for node in nodes
    )
    body = "\n\n".join(chunks)
    module_source = f'''from __future__ import annotations

from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
{aliases}

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

{body}

    return router, {{
{handlers}
    }}
'''
    (ROUTES_DIR / f"{module}.py").write_text(module_source, encoding="utf-8")
    module_stats[module] = (len(nodes), moved_line_count)

init_lines = ["from __future__ import annotations", ""]
for module in MODULE_ORDER:
    init_lines.append(f"from .{module} import build_router as build_{module}_router")
init_lines.extend(["", "ROUTER_BUILDERS = ("])
for module in MODULE_ORDER:
    init_lines.append(f"    build_{module}_router,")
init_lines.extend([")", ""])
(ROUTES_DIR / "__init__.py").write_text("\n".join(init_lines), encoding="utf-8")

# Remove moved function definitions from main.py while retaining helpers and auth/admin wiring.
kept = [line for index, line in enumerate(lines, start=1) if index not in removed_lines]
new_main = "".join(kept)
new_main = re.sub(r"\n{4,}", "\n\n\n", new_main)
import_anchor = "from .timezones import timezone_groups\n"
router_import = "from .routes import ROUTER_BUILDERS\nfrom .routes.context import RouteContext\n"
if router_import not in new_main:
    if import_anchor not in new_main:
        raise SystemExit("Could not find route import anchor")
    new_main = new_main.replace(import_anchor, import_anchor + router_import, 1)

registration = '''

# Domain routes are assembled after helpers/services are defined. Authentication,
# bootstrap, middleware, lifecycle, and admin-account wiring intentionally remain
# in this composition root during W1.5.
_route_context = RouteContext(globals())
for _build_route_bundle in ROUTER_BUILDERS:
    _domain_router, _domain_handlers = _build_route_bundle(_route_context)
    app.include_router(_domain_router)
    # Preserve app.main.<handler> compatibility for existing tests/internal callers
    # while the source of truth lives in app.routes.*.
    globals().update(_domain_handlers)
'''
if "_route_context = RouteContext(globals())" in new_main:
    raise SystemExit("Route registration already present")
new_main = new_main.rstrip() + registration + "\n"
MAIN.write_text(new_main, encoding="utf-8")

architecture_test = '''from __future__ import annotations

import unittest
from pathlib import Path

from app import main
from app.access import require_librarian


ROOT = Path(__file__).resolve().parent.parent


class RouteDecompositionTests(unittest.TestCase):
    def test_main_is_composition_root_not_route_monolith(self):
        main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertLess(len(main_source.splitlines()), 5000)
        self.assertIn("ROUTER_BUILDERS", main_source)
        self.assertIn("RouteContext(globals())", main_source)
        self.assertNotIn('@app.get("/library"', main_source)
        self.assertNotIn('@librarian_get("/duplicates"', main_source)
        self.assertIn('@app.get("/login"', main_source)

    def test_domain_router_modules_exist(self):
        for name in ("system", "operations", "dashboard", "review", "library", "settings", "collections", "titles"):
            path = ROOT / "app" / "routes" / f"{name}.py"
            self.assertTrue(path.exists(), name)
            self.assertIn("APIRouter", path.read_text(encoding="utf-8"))

    def test_compatibility_handler_aliases_remain_available(self):
        for name in ("library", "title_detail", "duplicate_review", "collections_page", "sources"):
            self.assertTrue(callable(getattr(main, name, None)), name)

    def test_extracted_protected_routes_keep_librarian_dependency(self):
        targets = {
            ("/duplicates", "GET"),
            ("/sources", "GET"),
            ("/titles/{title_id}/metadata/enrich", "POST"),
        }
        found = set()
        for route in main.app.routes:
            key_candidates = {(getattr(route, "path", ""), method) for method in getattr(route, "methods", set())}
            for key in targets.intersection(key_candidates):
                dependencies = [item.call for item in route.dependant.dependencies]
                self.assertIn(require_librarian, dependencies, key)
                found.add(key)
        self.assertEqual(found, targets)


if __name__ == "__main__":
    unittest.main()
'''
TEST.write_text(architecture_test, encoding="utf-8")

if DOC.exists():
    doc = DOC.read_text(encoding="utf-8")
    marker = "## W1.5 Application decomposition"
    if marker not in doc:
        doc = doc.rstrip() + f'''\n\n{marker}\n\nW1.5 moves the product/domain HTTP surface out of `app/main.py` into `app/routes/` APIRouter modules. The composition root retains application construction, middleware, lifecycle, bootstrap/authentication, and admin-account wiring. Existing handler names are published as compatibility aliases during the transition. Route-level Librarian dependencies remain attached inside each router module.\n'''
        DOC.write_text(doc + "\n", encoding="utf-8")

print(f"Extracted {len(selected_names)} route functions into {len(MODULE_ORDER)} routers")
print(f"main.py: {len(text.splitlines())} -> {len(new_main.splitlines())} lines")
for module in MODULE_ORDER:
    count, moved = module_stats[module]
    print(f"  {module}: {count} routes, {moved} source lines moved")
print(f"Routes intentionally retained in main.py: {len(remaining_route_names)}")

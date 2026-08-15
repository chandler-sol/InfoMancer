from __future__ import annotations

import ast
from collections import Counter, defaultdict
from pathlib import Path

SOURCE = Path("app/main.py")
text = SOURCE.read_text(encoding="utf-8")
tree = ast.parse(text)


def route_decorators(node: ast.AST) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for dec in getattr(node, "decorator_list", []):
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "app"):
            continue
        if func.attr not in {"get", "post", "put", "patch", "delete"}:
            continue
        path = "?"
        if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
            path = dec.args[0].value
        found.append((func.attr.upper(), path))
    return found


def loaded_names(node: ast.AST) -> set[str]:
    locals_: set[str] = set()
    loaded: set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            locals_.add(arg.arg)
        if node.args.vararg:
            locals_.add(node.args.vararg.arg)
        if node.args.kwarg:
            locals_.add(node.args.kwarg.arg)
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            if isinstance(sub.ctx, (ast.Store, ast.Del)):
                locals_.add(sub.id)
            elif isinstance(sub.ctx, ast.Load):
                loaded.add(sub.id)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not node:
            locals_.add(sub.name)
    return loaded - locals_

routes = []
for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    decorators = route_decorators(node)
    if not decorators:
        continue
    globals_used = sorted(loaded_names(node))
    routes.append((node.lineno, getattr(node, "end_lineno", node.lineno), node.name, decorators, globals_used))

print(f"MAIN_LINES={len(text.splitlines())}")
print(f"ROUTE_FUNCTIONS={len(routes)}")
print()

prefix_counts: Counter[str] = Counter()
for _, _, _, decorators, _ in routes:
    for _, path in decorators:
        first = path.strip("/").split("/", 1)[0] if path != "/" else "root"
        prefix_counts[first or "root"] += 1
print("PREFIX_COUNTS")
for prefix, count in prefix_counts.most_common():
    print(f"  {prefix}: {count}")
print()

for start, end, name, decorators, globals_used in routes:
    rendered = ", ".join(f"{method} {path}" for method, path in decorators)
    print(f"{start:5d}-{end:5d}  {name}  [{rendered}]")
    print("    globals: " + ", ".join(globals_used))

from __future__ import annotations

import re
import symtable
from pathlib import Path

ROUTES_DIR = Path("app/routes")
MODULES = (
    "system",
    "operations",
    "dashboard",
    "review",
    "library",
    "settings",
    "collections",
    "titles",
)
ALIAS_RE = re.compile(
    r'^    ([A-Za-z_][A-Za-z0-9_]*) = ctx\.(?:get|live)\("\1"\)$',
    re.MULTILINE,
)


total_before = 0
total_after = 0
for module in MODULES:
    path = ROUTES_DIR / f"{module}.py"
    source = path.read_text(encoding="utf-8")
    table = symtable.symtable(source, str(path), "exec")
    builders = [child for child in table.get_children() if child.get_name() == "build_router"]
    if len(builders) != 1:
        raise SystemExit(f"Expected one build_router symbol table in {path}")
    builder = builders[0]

    # A direct nested route marks every value it closes over from build_router as
    # free, including values needed only by deeper nested helpers. Do not collect
    # free variables from deeper scopes, because those may instead be route-local
    # parameters or locals captured by an inner helper.
    required = {
        symbol.get_name()
        for child in builder.get_children()
        for symbol in child.get_symbols()
        if symbol.is_free()
    }
    # Type annotations, decorators, and default expressions on nested functions
    # execute while build_router runs and therefore appear as builder references.
    required.update(
        symbol.get_name()
        for symbol in builder.get_symbols()
        if symbol.is_referenced()
    )

    aliases = list(ALIAS_RE.finditer(source))
    total_before += len(aliases)
    removable = [match.group(1) for match in aliases if match.group(1) not in required]
    for name in removable:
        source, count = re.subn(
            rf'^    {re.escape(name)} = ctx\.(?:get|live)\("{re.escape(name)}"\)\n?',
            "",
            source,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise SystemExit(f"Failed to remove alias {name} in {path}")

    compile(source, str(path), "exec")
    path.write_text(source, encoding="utf-8")
    after = len(list(ALIAS_RE.finditer(source)))
    total_after += after
    print(f"{module}: aliases {len(aliases)} -> {after} (removed {len(removable)})")

print(f"Total router context aliases: {total_before} -> {total_after}")

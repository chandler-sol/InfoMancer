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
ALIAS_RE = re.compile(r'^    ([A-Za-z_][A-Za-z0-9_]*) = ctx\.(?:get|live)\("\1"\)\n$', re.MULTILINE)


def descendant_free_names(table: symtable.SymbolTable) -> set[str]:
    names: set[str] = set()
    for child in table.get_children():
        for symbol in child.get_symbols():
            if symbol.is_free():
                names.add(symbol.get_name())
        names.update(descendant_free_names(child))
    return names


total_before = 0
total_after = 0
for module in MODULES:
    path = ROUTES_DIR / f"{module}.py"
    source = path.read_text(encoding="utf-8")
    table = symtable.symtable(source, str(path), "exec")
    builders = [child for child in table.get_children() if child.get_name() == "build_router"]
    if len(builders) != 1:
        raise SystemExit(f"Expected one build_router symbol table in {path}")
    required = descendant_free_names(builders[0])
    aliases = list(ALIAS_RE.finditer(source))
    total_before += len(aliases)
    removable = {match.group(1) for match in aliases if match.group(1) not in required}
    for name in sorted(removable):
        source, count = re.subn(
            rf'^    {re.escape(name)} = ctx\.(?:get|live)\("{re.escape(name)}"\)\n$',
            "",
            source,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise SystemExit(f"Failed to remove alias {name} in {path}")
    path.write_text(source, encoding="utf-8")
    after = len(list(ALIAS_RE.finditer(source)))
    total_after += after
    print(f"{module}: aliases {len(aliases)} -> {after}")

print(f"Total router context aliases: {total_before} -> {total_after}")

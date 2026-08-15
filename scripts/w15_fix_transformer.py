from pathlib import Path

path = Path("scripts/w15_extract_routes.py")
text = path.read_text(encoding="utf-8")
old_alias = 'f\'    {name} = ctx.{"get" if name[:1].isupper() else "live"}("{name}")\''
new_alias = 'f\'    {name} = ctx.{"live" if name == "COLLECTION_ART_DIR" or not name[:1].isupper() else "get"}("{name}")\''
if old_alias not in text:
    raise SystemExit("Alias strategy anchor not found")
text = text.replace(old_alias, new_alias, 1)
old_path = '''    def __fspath__(self) -> str:\n        return self._value().__fspath__()\n'''
new_path = old_path + '''\n    def __truediv__(self, other):\n        return self._value() / other\n\n    def __rtruediv__(self, other):\n        return other / self._value()\n'''
if old_path not in text:
    raise SystemExit("LiveRef path anchor not found")
text = text.replace(old_path, new_path, 1)
path.write_text(text, encoding="utf-8")

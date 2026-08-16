from pathlib import Path

path = Path("scripts/_chatgpt_w5_saved_views.py")
text = path.read_text(encoding="utf-8")
old = '''    text = replace_once(
        text,
        '        "default_cover_size",\\n',
        '        "default_cover_size",\\n        "default_season_display",\\n',
        "editable season display",
    )
'''
new = '''    text = replace_once(
        text,
        '        "default_library_view",\\n        "default_cover_size",\\n        "search_provider_name",\\n',
        '        "default_library_view",\\n        "default_cover_size",\\n        "default_season_display",\\n        "search_provider_name",\\n',
        "editable season display",
    )
'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one editable-season patch block, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

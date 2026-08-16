from pathlib import Path

path = Path("tests/test_app_settings.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    '        self.assertEqual(self.settings.validate_safety("standard"), {"lockdown_mode": "0"})\n',
    '        self.assertEqual(self.settings.validate_safety("standard"), {"read_only_mode": "0", "lockdown_mode": "0"})\n',
    1,
)
text = text.replace(
    '        self.assertEqual(self.settings.validate_safety("lockdown"), {"lockdown_mode": "1"})\n',
    '        self.assertEqual(self.settings.validate_safety("lockdown"), {"read_only_mode": "0", "lockdown_mode": "1"})\n',
    1,
)
path.write_text(text, encoding="utf-8")

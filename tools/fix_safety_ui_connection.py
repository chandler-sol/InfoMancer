from pathlib import Path

path = Path("app/mie.py")
text = path.read_text(encoding="utf-8")
old = '''    def _library_quality_defaults_raw(self, conn=None) -> dict[str, Any] | None:
        owns_connection = conn is None
        if owns_connection:
            conn = self.database.connect()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key='mie_quality_defaults'"
            ).fetchone()
            if not row or not row["value"]:
                return None
            value = json.loads(row["value"])
            return value if isinstance(value, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
        finally:
            if owns_connection:
                conn.close()
'''
new = '''    def _library_quality_defaults_raw(self, conn=None) -> dict[str, Any] | None:
        if conn is None:
            with self.database.connect() as connection:
                return self._library_quality_defaults_raw(connection)
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='mie_quality_defaults'"
        ).fetchone()
        if not row or not row["value"]:
            return None
        try:
            value = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return None
        return value if isinstance(value, dict) else None
'''
if old not in text:
    raise SystemExit("quality-default connection helper marker not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Quality-default connection ownership corrected")

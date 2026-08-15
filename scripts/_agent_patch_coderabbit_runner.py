from pathlib import Path

path = Path("scripts/_agent_coderabbit_fixes.py")
text = path.read_text(encoding="utf-8")
start_marker = "# Host-updater tests now always model the mandatory trust allowlist.\n"
end_marker = "# Ruff's reported E702.\n"
start = text.index(start_marker)
end = text.index(end_marker, start)
replacement = r'''# Host-updater tests now always model the mandatory trust allowlist.
replace_count(
    "tests/test_host_updater.py",
    ''' + "'''" + r'''            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary))
''' + "'''" + r''',
    ''' + "'''" + r'''            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary), {"A" * 40})
''' + "'''" + r''',
    2,
)
replace_once(
    "tests/test_host_updater.py",
    ''' + "'''" + r'''    def test_trusted_signature_fingerprint_is_enforced(self):
''' + "'''" + r''',
    ''' + "'''" + r'''    def test_valid_signature_without_trusted_fingerprint_is_rejected(self):
        fingerprint = "A" * 40
        completed = subprocess.CompletedProcess(
            ["git"], 0, "",
            f"[GNUPG:] VALIDSIG {fingerprint} 2026-01-01 0 4 0 1 10 00 {fingerprint}",
        )
        with tempfile.TemporaryDirectory() as temporary, patch(
            "scripts.host_updater.subprocess.run", return_value=completed
        ):
            with self.assertRaises(UpdateError):
                verify_release_tag("v1.2.3", Path(temporary))

    def test_trusted_signature_fingerprint_is_enforced(self):
''' + "'''" + r''',
)

'''
path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
print("Patch runner corrected.")

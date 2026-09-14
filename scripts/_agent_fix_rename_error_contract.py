from pathlib import Path

service_path = Path("app/rename_proposals.py")
service = service_path.read_text(encoding="utf-8")
old = """            except OSError as rollback_exc:\n                raise RenameProposalError(\n                    f\"The catalog update failed and automatic rename rollback also failed: {rollback_exc}\"\n                ) from rollback_exc\n            raise\n        proposal[\"status\"] = \"applied\"\n"""
new = """            except OSError as rollback_exc:\n                raise RenameProposalError(\n                    f\"The catalog update failed and automatic rename rollback also failed: {rollback_exc}\"\n                ) from rollback_exc\n            raise RenameProposalError(\n                \"The catalog update failed, so InfoMancer restored the original filename. Nothing was changed.\"\n            ) from exc\n        proposal[\"status\"] = \"applied\"\n"""
if service.count(old) != 1:
    raise SystemExit(f"Expected one rename rollback error block, found {service.count(old)}")
service_path.write_text(service.replace(old, new, 1), encoding="utf-8")

test_path = Path("tests/test_rename_proposals.py")
tests = test_path.read_text(encoding="utf-8")
anchor = """    def test_apply_rollback_refuses_to_overwrite_new_source_collision(self):\n"""
addition = """    def test_apply_catalog_failure_rolls_back_and_returns_domain_error(self):\n        self.service.refresh_all()\n        proposal = self.service.list_for_review(\"active\")[0]\n        destination = Path(proposal[\"destination_path\"])\n        real_connect = self.database.connect\n        calls = 0\n\n        @contextmanager\n        def controlled_connect():\n            nonlocal calls\n            calls += 1\n            if calls == 3:\n                self.assertTrue(destination.is_file())\n                self.assertFalse(self.source.exists())\n                raise sqlite3.OperationalError(\"synthetic catalog failure\")\n            with real_connect() as conn:\n                yield conn\n\n        self.database.connect = controlled_connect\n        with self.assertRaisesRegex(RenameProposalError, \"restored the original filename\"):\n            self.service.apply(proposal[\"id\"])\n        self.assertTrue(self.source.is_file())\n        self.assertEqual(self.source.read_bytes(), b\"movie\")\n        self.assertFalse(destination.exists())\n\n"""
if tests.count(anchor) != 1 or "test_apply_catalog_failure_rolls_back_and_returns_domain_error" in tests:
    raise SystemExit("Unexpected rename proposal test state")
test_path.write_text(tests.replace(anchor, addition + anchor, 1), encoding="utf-8")

from __future__ import annotations

import unittest

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.maintenance_gate import APPLICATION_MAINTENANCE_GATE, MaintenanceGate
from app.routes.context import RouteContext
from app.routes.security_hardening import _install_maintenance_admission_middleware


class MaintenanceGateTests(unittest.TestCase):
    def test_active_operation_prevents_exclusive_maintenance(self):
        gate = MaintenanceGate()
        self.assertTrue(gate.try_enter_operation())
        self.assertFalse(gate.try_begin_exclusive("restore"))
        self.assertEqual(gate.status()["active_operations"], 1)
        gate.leave_operation()
        self.assertTrue(gate.try_begin_exclusive("restore"))

    def test_exclusive_maintenance_blocks_new_operations_until_released(self):
        gate = MaintenanceGate()
        self.assertTrue(gate.try_begin_exclusive("portable recovery"))
        self.assertFalse(gate.try_enter_operation())
        self.assertEqual(gate.status()["reason"], "portable recovery")
        gate.end_exclusive()
        self.assertTrue(gate.try_enter_operation())
        gate.leave_operation()

    def test_operation_underflow_fails_closed(self):
        gate = MaintenanceGate()
        with self.assertRaises(RuntimeError):
            gate.leave_operation()


class MaintenanceAdmissionMiddlewareTests(unittest.TestCase):
    def setUp(self):
        status = APPLICATION_MAINTENANCE_GATE.status()
        self.assertEqual(status["active_operations"], 0)
        APPLICATION_MAINTENANCE_GATE.end_exclusive()

        self.app = FastAPI()
        _install_maintenance_admission_middleware(RouteContext({"app": self.app}))

        @self.app.get("/ordinary")
        def ordinary():
            active = APPLICATION_MAINTENANCE_GATE.status()["active_operations"]
            return PlainTextResponse(str(active))

        @self.app.get("/health")
        def health():
            return PlainTextResponse("ok")

        @self.app.post("/settings/recovery/apply")
        async def recovery_apply(request: Request):
            return PlainTextResponse("restore admitted")

        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(APPLICATION_MAINTENANCE_GATE.end_exclusive)

    def test_ordinary_request_is_counted_for_full_request(self):
        response = self.client.get("/ordinary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "1")
        self.assertEqual(
            APPLICATION_MAINTENANCE_GATE.status()["active_operations"], 0
        )

    def test_exclusive_mode_blocks_ordinary_request_but_keeps_health_available(self):
        self.assertTrue(
            APPLICATION_MAINTENANCE_GATE.try_begin_exclusive("portable recovery")
        )
        blocked = self.client.get("/ordinary")
        self.assertEqual(blocked.status_code, 503)
        self.assertEqual(blocked.headers.get("retry-after"), "5")
        self.assertEqual(blocked.headers.get("cache-control"), "no-store")

        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.text, "ok")

    def test_recovery_apply_can_enter_to_acquire_exclusive_mode_itself(self):
        response = self.client.post("/settings/recovery/apply")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "restore admitted")


if __name__ == "__main__":
    unittest.main()

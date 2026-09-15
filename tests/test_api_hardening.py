"""Unit tests for Phase 6A API Hardening & Resilience (Wave 2)."""

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from assistant.api.app import create_app
from assistant.api.auth import ApiSecurity, TokenBucket
from assistant.control.models import Device, DeviceStatus, now
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class ApiHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-hardening-test-")
        self.db_path = Path(self.tempdir) / "control.db"
        self.store = ControlStore(self.db_path)

    def tearDown(self):
        if hasattr(self, "store") and self.store:
            self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_persistent_rate_limiting_survives_restart(self):
        # 1. Bucket with capacity 2 requests per minute backed by store
        bucket1 = TokenBucket(per_minute=2, store=self.store)
        self.assertEqual(0, bucket1.take("device_client_A"))
        self.assertEqual(0, bucket1.take("device_client_A"))
        wait_time = bucket1.take("device_client_A")
        self.assertGreater(wait_time, 0)

        # 2. Simulate complete application/process restart with same SQLite database
        self.store.close()
        new_store = ControlStore(self.db_path)
        self.store = new_store

        bucket2 = TokenBucket(per_minute=2, store=new_store)
        # Bucket must still be exhausted immediately after restart
        new_wait = bucket2.take("device_client_A")
        self.assertGreater(new_wait, 0)

    def test_token_rotation_audit_logging_and_endpoint(self):
        plane = ControlPlane(store=self.store)
        guard = ApiSecurity(self.store, require_auth=True, trust_local=False)
        app = create_app(control=plane, security=guard)
        client = TestClient(app)

        # Pair a test device
        pair_res = guard.issue_pairing_code()
        claim_res = client.post("/api/pair", json={"code": pair_res["code"], "name": "AuditPhone"})
        self.assertEqual(201, claim_res.status_code)
        token = claim_res.json()["token"]
        device_id = claim_res.json()["device"]["id"]

        # Rotate token
        rotate_res = client.post("/api/auth/rotate", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(200, rotate_res.status_code)
        new_token = rotate_res.json()["token"]
        self.assertNotEqual(token, new_token)

        # Query audit log endpoint
        audit_res = client.get("/api/auth/audit/rotations", headers={"Authorization": f"Bearer {new_token}"})
        self.assertEqual(200, audit_res.status_code)
        rotations = audit_res.json()["rotations"]
        self.assertEqual(1, len(rotations))
        self.assertEqual(device_id, rotations[0]["device_id"])
        self.assertIn("rotated_at", rotations[0])

    def test_clock_step_backwards_does_not_freeze_bucket(self):
        # A stored bucket whose last_updated is in the future (NTP stepped the
        # clock backwards) must not drain the caller's tokens.
        self.store.update_rate_limit("jumped", tokens=1.0, last_updated=time.time() + 600.0)
        bucket = TokenBucket(per_minute=60, store=self.store)
        self.assertEqual(0, bucket.take("jumped"))

        # Same guard for the in-memory path.
        memory_bucket = TokenBucket(per_minute=60)
        memory_bucket._tokens["jumped"] = (1.0, time.time() + 600.0)
        self.assertEqual(0, memory_bucket.take("jumped"))

    def test_stale_rate_limit_buckets_are_pruned(self):
        now_ts = time.time()
        # One live bucket, one stale bucket untouched for over an hour.
        self.store.update_rate_limit("live", tokens=5.0, last_updated=now_ts)
        self.store.update_rate_limit("stale", tokens=5.0, last_updated=now_ts - 7200.0)

        # A fresh write past the prune interval triggers cleanup.
        self.store._last_rate_limit_prune = now_ts - 600.0
        self.store.update_rate_limit("fresh", tokens=5.0, last_updated=now_ts + 1.0)

        self.assertIsNone(self.store.get_rate_limit("stale"))
        self.assertIsNotNone(self.store.get_rate_limit("live"))
        self.assertIsNotNone(self.store.get_rate_limit("fresh"))


if __name__ == "__main__":
    unittest.main()

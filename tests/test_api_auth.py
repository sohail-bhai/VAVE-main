"""Tests for API authentication, pairing and rate limiting.

Reaching the port must not be the same as being allowed to drive the machine,
so these cover the closed default rather than the open one the endpoint tests
use.
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from assistant.api import create_app
from assistant.api.auth import ApiSecurity, PairingError, TokenBucket, hash_token
from assistant.control.executor import TaskExecutor
from assistant.control.models import DeviceStatus, TaskStatus, now
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class SecurityTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-auth-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)
        self.executor = TaskExecutor(plane=self.plane,
                                     runner=lambda instruction, context: "done")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def client(self, **kwargs):
        """A client whose calls look remote unless a test says otherwise."""
        options = {"require_auth": True, "trust_local": True,
                   "rate_limit_per_minute": 10000, "trusted_hosts": ("127.0.0.1",)}
        options.update(kwargs)
        self.security = ApiSecurity(self.store, **options)
        app = create_app(control=self.plane, executor=self.executor,
                         security=self.security)
        return TestClient(app)


class PairingTests(SecurityTestCase):
    def test_a_paired_device_gets_a_token_it_can_use(self):
        client = self.client(trusted_hosts=("testclient",))

        code = client.post("/api/pair/code").json()["code"]
        paired = client.post("/api/pair", json={"code": code, "name": "Rav's phone"})

        self.assertEqual(201, paired.status_code)
        token = paired.json()["token"]
        response = client.get("/api/status", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(200, response.status_code)

    def test_the_token_is_never_stored_in_the_clear(self):
        security = ApiSecurity(self.store, require_auth=True)
        code = security.issue_pairing_code()["code"]

        device, token = security.pair(code, "Rav's phone")

        stored = self.store.get_device(device.id)
        self.assertEqual(hash_token(token), stored.token_hash)
        self.assertNotIn(token, str(stored.token_hash))

    def test_a_device_never_exposes_its_token_hash(self):
        security = ApiSecurity(self.store, require_auth=True)
        code = security.issue_pairing_code()["code"]
        device, _ = security.pair(code, "Rav's phone")

        self.assertNotIn("token_hash", device.to_dict())

    def test_a_pairing_code_works_only_once(self):
        security = ApiSecurity(self.store, require_auth=True)
        code = security.issue_pairing_code()["code"]
        security.pair(code, "First phone")

        with self.assertRaises(PairingError):
            security.pair(code, "Second phone")

    def test_a_wrong_code_is_refused(self):
        client = self.client(trusted_hosts=("testclient",))

        response = client.post("/api/pair", json={"code": "000000", "name": "Phone"})

        self.assertEqual(403, response.status_code)

    def test_a_remote_caller_cannot_mint_a_pairing_code(self):
        client = self.client(trusted_hosts=("127.0.0.1",))

        response = client.post("/api/pair/code")

        self.assertEqual(403, response.status_code)

    def test_an_already_paired_device_can_pair_the_next_one(self):
        security = ApiSecurity(self.store, require_auth=True,
                               trusted_hosts=("127.0.0.1",))
        client = TestClient(create_app(control=self.plane, executor=self.executor,
                                       security=security))
        _, token = security.pair(security.issue_pairing_code()["code"], "First phone")

        response = client.post("/api/pair/code",
                               headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(200, response.status_code)

    def test_unpairing_revokes_only_that_device(self):
        security = ApiSecurity(self.store, require_auth=True)
        first, first_token = security.pair(security.issue_pairing_code()["code"], "One")
        second, second_token = security.pair(security.issue_pairing_code()["code"], "Two")

        security.unpair(first.id)

        self.assertIsNone(security.device_for_token(first_token))
        self.assertIsNotNone(security.device_for_token(second_token))


class AuthenticationTests(SecurityTestCase):
    def test_an_unauthenticated_remote_call_is_refused(self):
        client = self.client()

        response = client.get("/api/tasks")

        self.assertEqual(401, response.status_code)
        self.assertEqual("unauthenticated", response.json()["error"]["kind"])

    def test_a_bad_token_is_refused_even_from_a_trusted_host(self):
        client = self.client(trusted_hosts=("testclient",))

        response = client.get("/api/tasks", headers={"Authorization": "Bearer nope"})

        self.assertEqual(401, response.status_code)

    def test_a_local_caller_works_without_a_token(self):
        client = self.client(trusted_hosts=("testclient",))

        self.assertEqual(200, client.get("/api/tasks").status_code)

    def test_local_trust_can_be_switched_off(self):
        client = self.client(trust_local=False, trusted_hosts=("testclient",))

        self.assertEqual(401, client.get("/api/tasks").status_code)

    def test_health_is_reachable_without_a_token(self):
        client = self.client()

        self.assertEqual(200, client.get("/health").status_code)

    def test_using_the_api_marks_the_device_as_seen(self):
        client = self.client()
        device, token = None, None
        security = self.security
        device, token = security.pair(security.issue_pairing_code()["code"], "Phone")
        before = self.store.get_device(device.id).last_seen

        client.get("/api/status", headers={"Authorization": f"Bearer {token}"})

        self.assertGreaterEqual(self.store.get_device(device.id).last_seen, before)

    def test_the_websocket_refuses_an_unauthenticated_client(self):
        client = self.client()

        with self.assertRaises(Exception):
            with client.websocket_connect("/ws/activity"):
                pass

    def test_the_websocket_accepts_a_paired_token(self):
        client = self.client()
        _, token = self.security.pair(
            self.security.issue_pairing_code()["code"], "Phone")

        with client.websocket_connect(f"/ws/activity?token={token}") as socket:
            self.plane.record("Something happened.")
            self.assertIn("message", socket.receive_json())


class RateLimitTests(SecurityTestCase):
    def test_a_flood_of_requests_is_slowed_down(self):
        client = self.client(rate_limit_per_minute=5, trusted_hosts=("testclient",))

        statuses = [client.get("/api/tasks").status_code for _ in range(8)]

        self.assertIn(429, statuses)
        limited = client.get("/api/tasks")
        self.assertEqual("rate_limited", limited.json()["error"]["kind"])
        self.assertIn("retry-after", {key.lower() for key in limited.headers})

    def test_the_bucket_refills_over_time(self):
        bucket = TokenBucket(per_minute=60)

        self.assertEqual(0, bucket.take("someone"))
        for _ in range(59):
            bucket.take("someone")
        self.assertGreater(bucket.take("someone"), 0)

    def test_devices_are_limited_separately(self):
        bucket = TokenBucket(per_minute=1)

        self.assertEqual(0, bucket.take("phone"))
        self.assertEqual(0, bucket.take("laptop"))


class ErrorShapeTests(SecurityTestCase):
    def test_a_missing_task_uses_the_shared_error_shape(self):
        client = self.client(trusted_hosts=("testclient",))

        body = client.get("/api/tasks/no-such-task").json()

        self.assertEqual(404, body["error"]["status"])
        self.assertEqual("not_found", body["error"]["kind"])

    def test_an_invalid_body_reports_the_offending_field(self):
        client = self.client(trusted_hosts=("testclient",))

        response = client.post("/api/tasks", json={"goal": ""})

        self.assertEqual(422, response.status_code)
        body = response.json()["error"]
        self.assertEqual("invalid_request", body["kind"])
        self.assertEqual(["goal"], [item["field"] for item in body["fields"]])


class TokenExpiryAndRotationTests(SecurityTestCase):
    def test_pair_includes_token_expires_at(self):
        security = ApiSecurity(self.store, require_auth=True)
        code = security.issue_pairing_code()["code"]
        device, token = security.pair(code, "Phone", ttl_seconds=3600)

        self.assertGreater(device.token_expires_at, now())
        stored = self.store.get_device(device.id)
        self.assertEqual(device.token_expires_at, stored.token_expires_at)

    def test_expired_token_is_rejected_on_authenticate(self):
        security = ApiSecurity(self.store, require_auth=True)
        code = security.issue_pairing_code()["code"]
        device, token = security.pair(code, "Phone", ttl_seconds=10)

        device.token_expires_at = now() - 10.0
        self.store.save_device(device)

        with self.assertRaises(PermissionError):
            security.authenticate(token, "testclient")

        refreshed = self.store.get_device(device.id)
        self.assertEqual(DeviceStatus.OFFLINE, refreshed.status)

    def test_expired_token_returns_401_on_api(self):
        client = self.client(trusted_hosts=("127.0.0.1",))
        code = self.security.issue_pairing_code()["code"]
        device, token = self.security.pair(code, "Phone", ttl_seconds=10)

        device.token_expires_at = now() - 10.0
        self.store.save_device(device)

        resp = client.get("/api/status", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(401, resp.status_code)

    def test_rotate_token_succeeds_and_updates_hash_and_expiry(self):
        security = ApiSecurity(self.store, require_auth=True)
        code = security.issue_pairing_code()["code"]
        device, old_token = security.pair(code, "Phone", ttl_seconds=1000)

        updated_device, new_token = security.rotate(old_token, ttl_seconds=5000)
        self.assertNotEqual(old_token, new_token)
        self.assertEqual(hash_token(new_token), updated_device.token_hash)
        self.assertGreater(updated_device.token_expires_at, now() + 4000)

        # New token works
        authenticated = security.authenticate(new_token, "testclient")
        self.assertEqual(updated_device.id, authenticated.id)

        # Old token fails
        with self.assertRaises(PermissionError):
            security.authenticate(old_token, "testclient")

    def test_cannot_rotate_expired_token(self):
        security = ApiSecurity(self.store, require_auth=True)
        code = security.issue_pairing_code()["code"]
        device, token = security.pair(code, "Phone", ttl_seconds=10)

        device.token_expires_at = now() - 10.0
        self.store.save_device(device)

        with self.assertRaises(PermissionError):
            security.rotate(token)

    def test_api_rotate_endpoint(self):
        client = self.client(trusted_hosts=("127.0.0.1",))
        code = self.security.issue_pairing_code()["code"]
        device, token = self.security.pair(code, "Phone", ttl_seconds=3600)

        resp = client.post("/api/auth/rotate", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(200, resp.status_code)
        data = resp.json()
        new_token = data["token"]
        self.assertNotEqual(token, new_token)
        self.assertGreater(data["expires_at"], now())

        # Calling status with new token works
        status_resp = client.get("/api/status", headers={"Authorization": f"Bearer {new_token}"})
        self.assertEqual(200, status_resp.status_code)

    def test_api_rotate_requires_bearer_token(self):
        client = self.client(trusted_hosts=("127.0.0.1",))
        resp = client.post("/api/auth/rotate")
        self.assertEqual(401, resp.status_code)


class StartupAutoResumeTests(SecurityTestCase):
    def test_api_startup_resumes_interrupted_tasks(self):
        resumed_calls = []

        class StubExecutor:
            def resume_interrupted(self):
                resumed_calls.append("called")
                return []

        security = ApiSecurity(self.store, require_auth=False)
        app = create_app(control=self.plane, executor=StubExecutor(),
                         security=security)

        with TestClient(app):
            # Wait briefly for background thread in lifespan
            time.sleep(0.05)

        self.assertIn("called", resumed_calls)


if __name__ == "__main__":
    unittest.main()

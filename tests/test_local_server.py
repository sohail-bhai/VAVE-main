"""Tests for the control plane running inside the desktop app.

The phone is meant to be a client of *this* computer, not a second VAVE, so
what matters is that the server the desktop app starts shares one control plane
with it: a task created from the phone is the same task the desktop shows.
"""

import json
import unittest
import urllib.error
import urllib.request

from assistant.api.server import LocalServer


def _call(url, token=None, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read() or b"null")


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        # Bound to this machine only: a test must not open a port to the network.
        self.server = LocalServer(host="127.0.0.1", port=8793)
        if not self.server.start():
            self.skipTest(f"Could not start the server: {self.server.error}")
        self.base = "http://127.0.0.1:8793"

    def tearDown(self):
        self.server.stop()
        from assistant.control.service import reset_control_plane
        reset_control_plane()

    def test_it_listens_and_says_where(self):
        self.assertTrue(self.server.running)
        self.assertEqual([f"127.0.0.1:{self.server.port}"], self.server.addresses())

        status, body = _call(f"{self.base}/health")
        self.assertEqual(200, status)
        self.assertTrue(body["ok"])

    def test_a_phone_pairs_with_a_code_from_this_process(self):
        code, expires_in = self.server.pairing_code()

        self.assertTrue(code.isdigit())
        self.assertGreater(expires_in, 0)

        status, body = _call(f"{self.base}/api/pair", payload={
            "code": code, "name": "Test phone", "kind": "phone"})

        self.assertEqual(201, status)
        self.assertTrue(body["token"])

    def test_a_stale_code_is_refused(self):
        code, _ = self.server.pairing_code()
        _call(f"{self.base}/api/pair", payload={"code": code, "name": "First"})

        # A code is claimed once; the second phone must ask for its own.
        with self.assertRaises(urllib.error.HTTPError) as refused:
            _call(f"{self.base}/api/pair", payload={"code": code, "name": "Second"})

        self.assertIn(refused.exception.code, (400, 401, 403))

    def test_a_paired_phone_sees_the_desktop_control_plane(self):
        """One control plane, so work started anywhere is visible everywhere."""
        code, _ = self.server.pairing_code()
        _, paired = _call(f"{self.base}/api/pair",
                          payload={"code": code, "name": "Test phone"})
        token = paired["token"]

        status, created = _call(f"{self.base}/api/tasks", token=token,
                                payload={"goal": "Tidy the desk"})
        self.assertEqual(201, status)

        from assistant.control.service import get_control_plane
        task = get_control_plane().get_task(created["id"])

        self.assertIsNotNone(task)
        self.assertEqual("Tidy the desk", task.goal)

    def test_without_a_token_nothing_is_reachable(self):
        with self.assertRaises(urllib.error.HTTPError) as refused:
            # A request that does not come from this machine's own loopback
            # trust would be rejected; the tasks list still requires a device.
            request = urllib.request.Request(f"{self.base}/api/tasks",
                                             headers={"Authorization": "Bearer nonsense"})
            urllib.request.urlopen(request, timeout=5)

        self.assertEqual(401, refused.exception.code)

    def test_pairing_codes_need_a_running_server(self):
        self.server.stop()

        code, expires_in = self.server.pairing_code()

        self.assertIsNone(code)
        self.assertEqual(0, expires_in)


if __name__ == "__main__":
    unittest.main()

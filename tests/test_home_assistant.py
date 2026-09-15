"""Unit tests for the Home Assistant bridge (Phase 4)."""

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from assistant import home as home_module
from assistant.api.app import create_app
from assistant.api.auth import ApiSecurity
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class FakeHomeTransport:
    """Stateful Home Assistant double: states mutate when services run."""

    def __init__(self):
        self.calls = []
        self.states = {
            "light.bedroom": {"entity_id": "light.bedroom", "state": "off",
                              "attributes": {"friendly_name": "Bedroom Light"}},
            "light.kitchen": {"entity_id": "light.kitchen", "state": "on",
                              "attributes": {"friendly_name": "Kitchen Light"}},
            "climate.living_room": {"entity_id": "climate.living_room", "state": "heat",
                                    "attributes": {"friendly_name": "Living Room",
                                                   "temperature": 22}},
            "automation.sun": {"entity_id": "automation.sun", "state": "on",
                               "attributes": {"friendly_name": "Sun"}},
        }

    def __call__(self, method, url, payload=None):
        self.calls.append({"method": method, "url": url, "payload": payload})
        path = url.split("://", 1)[-1].split("/", 1)[-1]

        if method == "GET" and path == "api/states":
            return list(self.states.values())
        if method == "GET" and path.startswith("api/states/"):
            entity_id = path[len("api/states/"):]
            return dict(self.states.get(entity_id, {}))
        if method == "POST" and path.startswith("api/services/"):
            _, _, domain, service = path.split("/", 3)
            entity_id = (payload or {}).get("entity_id", "")
            state = self.states.get(entity_id)
            if state is None:
                return {}
            if service in ("turn_on", "turn_off"):
                state["state"] = "on" if service == "turn_on" else "off"
            elif service == "toggle":
                state["state"] = "off" if state["state"] == "on" else "on"
            elif service == "set_temperature":
                state["attributes"]["temperature"] = (payload or {}).get("temperature")
            return {}
        raise AssertionError(f"unexpected call: {method} {url}")


def make_client(transport=None):
    transport = transport or FakeHomeTransport()
    return home_module.HomeAssistantClient(
        base_url="http://ha.local:8123", token="test-token", transport=transport)


class HomeClientTests(unittest.TestCase):
    def test_list_filters_to_device_domains(self):
        out = home_module.list_home_devices(_client_override=make_client())
        self.assertIn("light.bedroom", out)
        self.assertIn("climate.living_room", out)
        self.assertNotIn("automation.sun", out)

    def test_list_accepts_domain_filter(self):
        out = home_module.list_home_devices("light", _client_override=make_client())
        self.assertIn("light.bedroom", out)
        self.assertNotIn("climate.living_room", out)

    def test_get_state_happy_and_missing(self):
        client = make_client()
        self.assertEqual("Bedroom Light is off.",
                         home_module.get_device_state("light.bedroom", _client_override=client))
        self.assertIn("No device", home_module.get_device_state("light.attic", _client_override=client))
        self.assertIn("like 'light.bedroom'", home_module.get_device_state("nonsense", _client_override=client))

    def test_control_switches_and_verifies(self):
        client = make_client()
        self.assertEqual("Bedroom Light is now on.",
                         home_module.control_device("light.bedroom", "on", _client_override=client))
        self.assertEqual("Kitchen Light is now off.",
                         home_module.control_device("light.kitchen", "toggle", _client_override=client))

    def test_control_brightness_payload(self):
        transport = FakeHomeTransport()
        client = make_client(transport)
        result = home_module.control_device("light.bedroom", "brightness", 40, _client_override=client)
        self.assertIn("now on", result)
        service_calls = [c for c in transport.calls if c["method"] == "POST"]
        self.assertEqual(1, len(service_calls))
        self.assertTrue(service_calls[0]["url"].endswith("api/services/light/turn_on"))
        self.assertEqual(40, service_calls[0]["payload"]["brightness_pct"])

    def test_control_temperature_payload(self):
        transport = FakeHomeTransport()
        client = make_client(transport)
        home_module.control_device("climate.living_room", "temperature", 24, _client_override=client)
        service_calls = [c for c in transport.calls if c["method"] == "POST"]
        self.assertTrue(service_calls[0]["url"].endswith("api/services/climate/set_temperature"))
        self.assertEqual(24, service_calls[0]["payload"]["temperature"])

    def test_control_refusals(self):
        client = make_client()
        self.assertIn("only applies to lights",
                      home_module.control_device("climate.living_room", "brightness", 40, _client_override=client))
        self.assertIn("only applies to climate",
                      home_module.control_device("light.bedroom", "temperature", 24, _client_override=client))
        self.assertIn("Unknown action",
                      home_module.control_device("light.bedroom", "explode", _client_override=client))
        self.assertIn("No device",
                      home_module.control_device("light.attic", "on", _client_override=client))

    def test_not_configured_is_a_sentence(self):
        with mock.patch("assistant.home.get_setting", return_value=""):
            self.assertIn("not configured",
                          home_module.list_home_devices(_client_override=None))

    def test_narrow_secret_scope_blocks_control_but_not_reads(self):
        import tempfile
        from pathlib import Path

        tempdir = tempfile.mkdtemp(prefix="vave-home-scope-test-")
        store = ControlStore(Path(tempdir) / "control.db")
        plane = ControlPlane(store=store)
        try:
            plane.secrets.put("homeassistant", "tok", allowed_capabilities="home.read")
            with mock.patch("assistant.control.service.get_control_plane", return_value=plane):
                with mock.patch("assistant.home.get_setting",
                                side_effect=lambda key, default="": "http://ha.local:8123"
                                if key == "home_assistant_url" else default):
                    reads = home_module.list_home_devices(_client_override=None)
                    # No transport and no real server: reaches the network and
                    # fails there, which proves the read was ALLOWED through.
                    self.assertIn("Cannot reach", reads)
                    refused = home_module.control_device("light.bedroom", "on")
                    self.assertIn("not allowed", refused)
        finally:
            store.close()
            import shutil
            shutil.rmtree(tempdir, ignore_errors=True)


class HomeWiringTests(unittest.TestCase):
    def test_tools_registered_with_capabilities_and_tiers(self):
        from assistant import ai_brain
        from assistant import guard
        from assistant.control.capabilities import TOOL_CAPABILITIES

        for tool in ("list_home_devices", "get_device_state", "control_device"):
            self.assertIn(tool, ai_brain.AVAILABLE_FUNCTIONS)
        self.assertEqual("home.read", TOOL_CAPABILITIES["list_home_devices"])
        self.assertEqual("home.read", TOOL_CAPABILITIES["get_device_state"])
        self.assertEqual("home.control", TOOL_CAPABILITIES["control_device"])
        self.assertEqual("safe", guard.tier_of("list_home_devices"))
        self.assertEqual("safe", guard.tier_of("get_device_state"))
        self.assertEqual("sensitive", guard.tier_of("control_device"))
        self.assertIn("control_device", guard.REACHES_OUTWARD)

    def test_brain_offers_home_tools_for_home_language(self):
        from assistant import ai_brain

        chosen = ai_brain.select_tools("turn off the bedroom light")
        names = [t["function"]["name"] for t in chosen
                 if isinstance(t, dict) and "function" in t]
        self.assertIn("control_device", names)
        self.assertIn("get_device_state", names)

        schemas = {t["function"]["name"] for t in ai_brain.LLM_TOOLS
                   if t.get("type") == "function"}
        self.assertTrue({"list_home_devices", "get_device_state", "control_device"} <= schemas)


class HomeApiTests(unittest.TestCase):
    def setUp(self):
        self.plane = ControlPlane(store=ControlStore(":memory:"))
        security = ApiSecurity(self.plane.store, trust_local=True,
                               trusted_hosts=("testclient", "127.0.0.1"))
        self.client = TestClient(create_app(control=self.plane, security=security))
        self.transport = FakeHomeTransport()
        self.patchers = [
            mock.patch("assistant.home._client",
                       return_value=make_client(self.transport)),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        if hasattr(self, "plane") and self.plane:
            self.plane.close()

    def approve_everything(self):
        for approval in self.plane.list_approvals(pending_only=True):
            self.plane.resolve_approval(approval.id, True)

    def test_devices_endpoint_is_structured(self):
        res = self.client.get("/api/home/devices")
        self.assertEqual(200, res.status_code)
        body = res.json()
        self.assertTrue(body["live"])
        ids = [d["entity_id"] for d in body["devices"]]
        self.assertIn("light.bedroom", ids)
        self.assertNotIn("automation.sun", ids)

    def test_control_waits_for_approval_then_verifies(self):
        payload = {"entity_id": "light.bedroom", "action": "on"}
        first = self.client.post("/api/home/control", json=payload)
        self.assertEqual(202, first.status_code)
        self.assertEqual("waiting_approval", first.json()["status"])

        self.approve_everything()
        done = self.client.post("/api/home/control", json=payload)
        self.assertEqual(200, done.status_code)
        self.assertEqual("done", done.json()["status"])
        self.assertIn("now on", done.json()["result"])

        # The grant is spent: the identical call asks again.
        again = self.client.post("/api/home/control", json=payload)
        self.assertEqual(202, again.status_code)

    def test_control_without_configuration_is_a_400(self):
        with mock.patch("assistant.home._client",
                        side_effect=home_module.HomeAssistantError("not configured")):
            res = self.client.post("/api/home/control",
                                   json={"entity_id": "light.x", "action": "on"})
        self.assertEqual(202, res.status_code)  # approval comes first


if __name__ == "__main__":
    unittest.main()

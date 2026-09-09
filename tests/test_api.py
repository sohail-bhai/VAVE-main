"""Tests for the HTTP and WebSocket boundary.

These use a control plane backed by a temporary database, so the API is
exercised end to end without touching the real one.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from assistant.api import create_app
from assistant.api.auth import ApiSecurity
from assistant.control.executor import TaskExecutor
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-api-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)
        # A runner that needs no local model, so the API is tested on its own.
        self.executed = []
        self.executor = TaskExecutor(plane=self.plane, runner=self._runner)
        # These tests are about the endpoints; authentication has its own
        # module, so this app is deliberately open.
        self.security = ApiSecurity(self.store, require_auth=False,
                                    rate_limit_per_minute=10000)
        self.client = TestClient(create_app(control=self.plane,
                                            executor=self.executor,
                                            security=self.security))

    def _runner(self, instruction, context):
        self.executed.append(instruction)
        return f"Handled: {instruction}"

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tempdir, ignore_errors=True)


class SystemTests(ApiTestCase):
    def test_root_points_at_the_documentation(self):
        response = self.client.get("/")
        self.assertEqual(200, response.status_code)
        self.assertEqual("/docs", response.json()["documentation"])

    def test_health_reports_ok(self):
        response = self.client.get("/health")
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["ok"])

    def test_status_summarises_the_control_plane(self):
        body = self.client.get("/api/status").json()
        for key in ("devices", "helpers", "active_tasks",
                    "pending_approvals", "temporary_access"):
            self.assertIn(key, body)

    def test_local_device_is_registered_automatically(self):
        devices = self.client.get("/api/devices").json()
        self.assertEqual(1, len(devices))
        self.assertEqual("computer", devices[0]["kind"])


class TaskApiTests(ApiTestCase):
    def test_creating_a_task_returns_its_steps(self):
        response = self.client.post("/api/tasks", json={
            "goal": "Continue my Hackwave project",
            "steps": ["Finding relevant files", "Researching on the web"],
        })

        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual("Continue my Hackwave project", body["goal"])
        self.assertEqual(2, len(body["steps"]))
        self.assertEqual(0.0, body["progress"])

    def test_a_goal_is_required(self):
        self.assertEqual(422, self.client.post("/api/tasks", json={"goal": ""}).status_code)

    def test_unknown_task_is_a_404(self):
        self.assertEqual(404, self.client.get("/api/tasks/nope").status_code)

    def test_walking_the_steps_updates_progress(self):
        task_id = self.client.post("/api/tasks", json={
            "goal": "Do the thing", "steps": ["One", "Two"],
        }).json()["id"]

        self.client.post(f"/api/tasks/{task_id}/steps/start", json={"position": 0})
        self.client.post(f"/api/tasks/{task_id}/steps/finish",
                         json={"position": 0, "detail": "Found 3 documents"})

        body = self.client.get(f"/api/tasks/{task_id}").json()
        self.assertEqual(0.5, body["progress"])
        self.assertEqual("running", body["status"])

    def test_cancelling_a_task(self):
        task_id = self.client.post("/api/tasks", json={
            "goal": "Do the thing", "steps": ["One"],
        }).json()["id"]

        body = self.client.post(f"/api/tasks/{task_id}/cancel").json()
        self.assertEqual("cancelled", body["status"])

    def test_active_only_filters_finished_tasks(self):
        task_id = self.client.post("/api/tasks", json={"goal": "Finished one"}).json()["id"]
        self.client.post("/api/tasks", json={"goal": "Still going"})
        self.client.post(f"/api/tasks/{task_id}/complete")

        active = self.client.get("/api/tasks", params={"active_only": True}).json()
        self.assertEqual(["Still going"], [task["goal"] for task in active])


class HelperApiTests(ApiTestCase):
    def test_registering_a_helper_advertises_its_capabilities(self):
        response = self.client.post("/api/helpers", json={
            "name": "Research helper", "capabilities": ["research", "web"],
        })

        self.assertEqual(201, response.status_code)
        self.assertEqual(["research", "web"], response.json()["capabilities"])

    def test_a_task_is_matched_to_a_capable_helper(self):
        self.client.post("/api/helpers", json={
            "name": "Research helper", "capabilities": ["research"]})

        body = self.client.post("/api/tasks", json={
            "goal": "Look this up", "capability": "research"}).json()

        self.assertTrue(body["helper_id"])


class ApprovalApiTests(ApiTestCase):
    def _pending_approval(self):
        task = self.plane.create_task("Merge my changes", steps=["One"])
        return self.plane.request_approval(
            "Merge changes", "I'm ready to merge your project changes.",
            reason="You asked me to finish the project",
            impact="14 files will change", task_id=task.id), task

    def test_pending_approvals_are_listed(self):
        approval, _task = self._pending_approval()

        body = self.client.get("/api/approvals").json()
        self.assertEqual([approval.id], [item["id"] for item in body])

    def test_approving_resumes_the_task(self):
        approval, task = self._pending_approval()

        response = self.client.post(f"/api/approvals/{approval.id}",
                                    json={"approved": True})

        self.assertEqual(200, response.status_code)
        self.assertEqual("approved", response.json()["status"])
        self.assertEqual("running", self.client.get(f"/api/tasks/{task.id}").json()["status"])

    def test_declining_cancels_the_task(self):
        approval, task = self._pending_approval()

        self.client.post(f"/api/approvals/{approval.id}", json={"approved": False})

        self.assertEqual("cancelled",
                         self.client.get(f"/api/tasks/{task.id}").json()["status"])

    def test_deciding_twice_is_a_404(self):
        approval, _task = self._pending_approval()
        self.client.post(f"/api/approvals/{approval.id}", json={"approved": True})

        response = self.client.post(f"/api/approvals/{approval.id}",
                                    json={"approved": True})
        self.assertEqual(404, response.status_code)


class SecurityApiTests(ApiTestCase):
    def test_granting_access_reports_time_remaining(self):
        response = self.client.post("/api/permissions", json={
            "resource": "Hackwave project", "actions": ["read", "write"],
            "seconds": 1800,
        })

        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual("active", body["status"])
        self.assertGreater(body["seconds_remaining"], 1700)

    def test_permission_length_is_capped(self):
        response = self.client.post("/api/permissions", json={
            "resource": "Everything", "actions": ["read"], "seconds": 999999,
        })
        self.assertEqual(422, response.status_code)

    def test_revoking_access(self):
        permission_id = self.client.post("/api/permissions", json={
            "resource": "Hackwave project", "actions": ["read"],
        }).json()["id"]

        self.client.delete(f"/api/permissions/{permission_id}")

        self.assertFalse(self.plane.check("Hackwave project", "read"))

    def test_emergency_stop_halts_work_and_blocks_new_tasks(self):
        self.client.post("/api/tasks", json={"goal": "Something", "steps": ["One"]})

        result = self.client.post("/api/emergency-stop").json()
        self.assertTrue(result["stopped"])
        self.assertEqual(1, result["tasks_cancelled"])

        blocked = self.client.post("/api/tasks", json={"goal": "New work"})
        self.assertEqual(409, blocked.status_code)

    def test_resume_allows_work_again(self):
        self.client.post("/api/emergency-stop")
        self.client.post("/api/resume")

        self.assertEqual(201, self.client.post("/api/tasks",
                                               json={"goal": "New work"}).status_code)


class ActivityApiTests(ApiTestCase):
    def test_activity_is_returned_newest_first(self):
        self.plane.record("First thing")
        self.plane.record("Second thing")

        messages = [event["message"] for event in self.client.get("/api/activity").json()]
        self.assertEqual("Second thing", messages[0])

    def test_websocket_sends_history_then_live_events(self):
        self.plane.record("Earlier thing")

        with self.client.websocket_connect("/ws/activity") as websocket:
            self.assertEqual("Earlier thing", websocket.receive_json()["message"])

            self.plane.record("Live thing")
            self.assertEqual("Live thing", websocket.receive_json()["message"])

    def test_websocket_unsubscribes_on_disconnect(self):
        before = len(self.plane._subscribers)

        with self.client.websocket_connect("/ws/activity") as websocket:
            websocket.receive_json  # connection established

        # The subscriber list must not grow once the client has gone away.
        # The notifier's own subscription is permanent and stays put.
        self.assertEqual(before, len(self.plane._subscribers))


class TaskExecutionTests(ApiTestCase):
    def test_running_a_task_works_its_steps(self):
        created = self.client.post("/api/tasks", json={
            "goal": "Tidy my notes", "steps": ["Reading notes"]}).json()

        response = self.client.post(f"/api/tasks/{created['id']}/run")
        self.executor.wait(created["id"], timeout=5)

        self.assertEqual(202, response.status_code)
        self.assertEqual(["Reading notes"], self.executed)
        detail = self.client.get(f"/api/tasks/{created['id']}").json()
        self.assertEqual("completed", detail["status"])

    def test_a_task_can_be_created_and_run_in_one_call(self):
        created = self.client.post("/api/tasks", json={
            "goal": "Tidy my notes", "steps": ["Reading notes"], "run": True}).json()
        self.executor.wait(created["id"], timeout=5)

        self.assertEqual(["Reading notes"], self.executed)

    def test_creating_without_run_does_not_start_work(self):
        self.client.post("/api/tasks", json={
            "goal": "Tidy my notes", "steps": ["Reading notes"]})

        self.assertEqual([], self.executed)

    def test_running_an_unknown_task_is_a_404(self):
        response = self.client.post("/api/tasks/no-such-task/run")
        self.assertEqual(404, response.status_code)

    def test_running_a_finished_task_is_refused(self):
        created = self.client.post("/api/tasks", json={"goal": "Tidy my notes"}).json()
        self.client.post(f"/api/tasks/{created['id']}/complete")

        response = self.client.post(f"/api/tasks/{created['id']}/run")
        self.assertEqual(409, response.status_code)

    def test_running_is_refused_while_stopped(self):
        created = self.client.post("/api/tasks", json={
            "goal": "Tidy my notes", "steps": ["Reading notes"]}).json()
        self.client.post("/api/emergency-stop")

        response = self.client.post(f"/api/tasks/{created['id']}/run")

        self.assertEqual(409, response.status_code)
        self.assertEqual([], self.executed)


class CapabilityTests(ApiTestCase):
    def test_the_catalog_lists_capabilities_with_their_risk(self):
        body = self.client.get("/api/capabilities").json()

        entry = next(item for item in body
                     if item["capability"] == "gcp.cloud_run.deploy")
        self.assertEqual("critical", entry["risk"])

    def test_the_catalog_can_be_filtered(self):
        body = self.client.get("/api/capabilities", params={"prefix": "browser.*"}).json()

        self.assertTrue(all(item["capability"].startswith("browser.")
                            for item in body))

    def test_a_low_risk_request_is_granted(self):
        body = self.client.post("/api/capabilities/request",
                                json={"capability": "browser.navigate"}).json()

        self.assertEqual("granted", body["status"])
        self.assertIsNotNone(body["permission"])

    def test_a_high_risk_request_comes_back_waiting(self):
        body = self.client.post("/api/capabilities/request",
                                json={"capability": "google.gmail.send"}).json()

        self.assertEqual("waiting", body["status"])
        self.assertEqual("google.gmail.send", body["approval"]["capability"])

    def test_approving_over_the_api_releases_the_access(self):
        request = self.client.post("/api/capabilities/request",
                                   json={"capability": "google.gmail.send"}).json()

        self.client.post(f"/api/approvals/{request['approval']['id']}",
                         json={"approved": True})

        granted = [permission["actions"]
                   for permission in self.client.get("/api/permissions").json()]
        self.assertIn(["google.gmail.send"], granted)

    def test_requesting_is_refused_while_stopped(self):
        self.client.post("/api/emergency-stop")

        response = self.client.post("/api/capabilities/request",
                                    json={"capability": "browser.navigate"})

        self.assertEqual(409, response.status_code)

    def test_a_policy_rule_changes_the_answer(self):
        self.client.post("/api/policies", json={"capability": "browser.navigate",
                                                 "decision": "deny",
                                                 "reason": "Not today."})

        body = self.client.post("/api/capabilities/request",
                                json={"capability": "browser.navigate"}).json()

        self.assertEqual("denied", body["status"])
        self.assertEqual("Not today.", body["judgement"]["reason"])

    def test_policy_rules_are_listed_and_removable(self):
        rule = self.client.post("/api/policies",
                                json={"capability": "google.*",
                                      "decision": "require_approval"}).json()

        self.assertEqual(1, len(self.client.get("/api/policies").json()))
        self.assertEqual(200, self.client.delete(f"/api/policies/{rule['id']}").status_code)
        self.assertEqual([], self.client.get("/api/policies").json())

    def test_an_unknown_decision_is_rejected(self):
        response = self.client.post("/api/policies",
                                    json={"capability": "google.*",
                                          "decision": "maybe"})

        self.assertEqual(422, response.status_code)

    def test_removing_an_unknown_rule_is_a_404(self):
        self.assertEqual(404, self.client.delete("/api/policies/nope").status_code)


class AgentTests(ApiTestCase):
    def register(self, name="Research agent", **extra):
        body = {"name": name, "capabilities": ["research"]}
        body.update(extra)
        return self.client.post("/api/agents", json=body).json()

    def test_an_agent_registers_with_its_version_and_endpoint(self):
        agent = self.register(version="1.4.0", framework="http",
                              endpoint="http://localhost:9000/run")

        self.assertEqual("1.4.0", agent["version"])
        self.assertEqual("http://localhost:9000/run", agent["endpoint"])

    def test_the_older_helpers_path_still_works(self):
        self.register()

        self.assertEqual(1, len(self.client.get("/api/helpers").json()))

    def test_a_heartbeat_records_how_the_last_work_went(self):
        agent = self.register()

        self.client.post(f"/api/agents/{agent['id']}/heartbeat",
                         json={"latency_ms": 250, "ok": True})

        health = self.client.get("/api/agents/health").json()[0]
        self.assertEqual(1, health["success_count"])
        self.assertEqual(250, health["p95_latency_ms"])

    def test_an_unknown_status_is_rejected(self):
        agent = self.register()

        response = self.client.post(f"/api/agents/{agent['id']}/heartbeat",
                                    json={"status": "sleepy"})

        self.assertEqual(422, response.status_code)

    def test_an_agent_can_be_disabled_and_enabled(self):
        agent = self.register()

        disabled = self.client.post(f"/api/agents/{agent['id']}/disable").json()
        enabled = self.client.post(f"/api/agents/{agent['id']}/enable").json()

        self.assertFalse(disabled["enabled"])
        self.assertTrue(enabled["enabled"])

    def test_killing_an_agent_quarantines_it(self):
        agent = self.register()

        killed = self.client.post(f"/api/agents/{agent['id']}/kill",
                                  json={"reason": "It kept clicking Buy."}).json()

        self.assertEqual("quarantined", killed["status"])
        self.assertFalse(killed["enabled"])

    def test_an_unknown_agent_is_a_404(self):
        for path in ("/api/agents/nope", "/api/agents/nope/enable"):
            self.assertEqual(404, self.client.get(path).status_code
                             if path.endswith("nope")
                             else self.client.post(path).status_code)

    def test_a_device_registers_with_its_capabilities(self):
        device = self.client.post("/api/devices",
                                  json={"name": "Rav's phone", "kind": "phone",
                                        "capabilities": ["notify"]}).json()

        self.assertEqual(["notify"], device["capabilities"])

    def test_a_device_heartbeat_keeps_it_online(self):
        device = self.client.post("/api/devices",
                                  json={"name": "Rav's phone", "kind": "phone"}).json()

        body = self.client.post(f"/api/devices/{device['id']}/heartbeat",
                                json={"capabilities": ["notify", "approve"]}).json()

        self.assertEqual("online", body["status"])
        self.assertEqual(["notify", "approve"], body["capabilities"])

    def test_a_heartbeat_for_an_unknown_device_is_a_404(self):
        response = self.client.post("/api/devices/nope/heartbeat", json={})

        self.assertEqual(404, response.status_code)


class PlanningTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        # A planner that needs no local model.
        self.executor.planner = _FixedPlanner([
            {"label": "Reading the notes", "depends_on": []},
            {"label": "Writing the summary", "depends_on": [0]},
        ])

    def test_a_goal_can_be_planned_without_running_it(self):
        body = self.client.post("/api/tasks/plan",
                                json={"goal": "Summarise my notes"}).json()

        self.assertEqual(["Reading the notes", "Writing the summary"],
                         [step["label"] for step in body["steps"]])
        self.assertEqual([], self.client.get("/api/tasks").json())

    def test_autoplan_creates_the_steps(self):
        detail = self.client.post("/api/tasks",
                                  json={"goal": "Summarise my notes",
                                        "autoplan": True}).json()

        self.assertEqual(["Reading the notes", "Writing the summary"],
                         [step["label"] for step in detail["steps"]])

    def test_a_graph_can_be_posted_directly(self):
        detail = self.client.post("/api/tasks", json={
            "goal": "Two parts",
            "plan": [{"label": "Fetch the data", "depends_on": []},
                     {"label": "Write it up", "depends_on": [0]}],
        }).json()

        self.assertEqual([0], detail["steps"][1]["depends_on"])

    def test_cancelling_stops_the_task(self):
        created = self.client.post("/api/tasks",
                                   json={"goal": "Tidy my notes",
                                         "steps": ["Reading notes"]}).json()

        body = self.client.post(f"/api/tasks/{created['id']}/cancel").json()

        self.assertEqual("cancelled", body["status"])

    def test_cancelling_an_unknown_task_is_a_404(self):
        self.assertEqual(404, self.client.post("/api/tasks/nope/cancel").status_code)


class _FixedPlanner:
    """A planner that returns a known plan, so tests need no model."""

    def __init__(self, steps):
        self.steps = steps

    def plan(self, goal):
        return list(self.steps)


class RecoveryTests(ApiTestCase):
    def test_a_step_can_be_delegated_to_another_agent(self):
        self.client.post("/api/agents", json={"name": "Document agent",
                                              "capabilities": ["documents"]})
        created = self.client.post("/api/tasks", json={"goal": "Read the report",
                                                       "steps": ["Finding it"]}).json()

        step = self.client.post(f"/api/tasks/{created['id']}/delegate",
                                json={"label": "Extracting the date",
                                      "capability": "documents"}).json()

        self.assertEqual("Extracting the date", step["label"])
        self.assertEqual([0], step["depends_on"])

    def test_delegating_with_nobody_available_is_refused(self):
        created = self.client.post("/api/tasks", json={"goal": "Read the report",
                                                       "steps": ["Finding it"]}).json()

        response = self.client.post(f"/api/tasks/{created['id']}/delegate",
                                    json={"label": "Extracting the date",
                                          "capability": "documents"})

        self.assertEqual(409, response.status_code)

    def test_delegating_on_an_unknown_task_is_a_404(self):
        response = self.client.post("/api/tasks/nope/delegate",
                                    json={"label": "Something"})

        self.assertEqual(404, response.status_code)

    def test_interrupted_tasks_can_be_picked_back_up(self):
        created = self.client.post("/api/tasks", json={"goal": "Two parts",
                                                       "steps": ["First", "Second"]}).json()
        self.client.post(f"/api/tasks/{created['id']}/steps/start", json={"position": 0})
        self.client.post(f"/api/tasks/{created['id']}/steps/finish",
                         json={"position": 0, "detail": "Already done."})

        resumed = self.client.post("/api/tasks/resume").json()
        self.executor.wait(created["id"], timeout=5)

        self.assertEqual([created["id"]], [task["id"] for task in resumed])
        self.assertEqual(["Second"], self.executed)


class EventStreamTests(ApiTestCase):
    def test_activity_can_be_filtered_by_type(self):
        self.client.post("/api/tasks", json={"goal": "Tidy my notes"})

        body = self.client.get("/api/activity",
                               params={"types": "task_created"}).json()

        self.assertEqual(["task_created"], [event["type"] for event in body])

    def test_an_unknown_event_type_is_rejected(self):
        response = self.client.get("/api/activity", params={"types": "made_up"})

        self.assertEqual(422, response.status_code)

    def test_the_event_types_are_listed_for_clients(self):
        body = self.client.get("/api/event-types").json()

        self.assertIn("approval_requested", body)
        self.assertIn("task_started", body)

    def test_the_event_socket_carries_the_audit_fields(self):
        with self.client.websocket_connect("/ws/events") as socket:
            self.plane.request_capability("google.gmail.send", agent_id="mail-agent")

            event = socket.receive_json()
            while event["type"] != "capability_requested":
                event = socket.receive_json()

        self.assertEqual("google.gmail.send", event["capability"])
        self.assertEqual("high", event["risk"])

    def test_the_event_socket_can_filter(self):
        with self.client.websocket_connect("/ws/events?types=task_completed") as socket:
            created = self.client.post("/api/tasks", json={"goal": "Tidy my notes"}).json()
            self.client.post(f"/api/tasks/{created['id']}/complete")

            event = socket.receive_json()

        self.assertEqual("task_completed", event["type"])

    def test_notifications_reach_a_connected_phone(self):
        with self.client.websocket_connect("/ws/notifications") as socket:
            created = self.client.post("/api/tasks", json={"goal": "Tidy my notes"}).json()
            self.client.post(f"/api/tasks/{created['id']}/complete",
                             params={"summary": "Tidied 12 notes."})

            notification = socket.receive_json()

        self.assertEqual("Tidied 12 notes.", notification["message"])
        self.assertEqual("done", notification["urgency"])

    def test_recent_notifications_can_be_read_back(self):
        created = self.client.post("/api/tasks", json={"goal": "Tidy my notes"}).json()
        self.client.post(f"/api/tasks/{created['id']}/complete",
                         params={"summary": "Tidied 12 notes."})

        body = self.client.get("/api/notifications").json()

        self.assertEqual("Tidied 12 notes.", body[0]["message"])

    def test_an_approval_notification_says_it_needs_an_answer(self):
        self.client.post("/api/capabilities/request",
                         json={"capability": "google.gmail.send"})

        body = self.client.get("/api/notifications").json()

        self.assertTrue(body[0]["needs_answer"])
        self.assertEqual("action", body[0]["urgency"])


class SecretApiTests(ApiTestCase):
    def test_a_secret_can_be_stored_and_listed_without_its_value(self):
        stored = self.client.put("/api/secrets/email_app_password",
                                 json={"value": "hunter2",
                                       "description": "Gmail"}).json()

        listed = self.client.get("/api/secrets").json()

        self.assertEqual("secret://email_app_password", stored["reference"])
        self.assertNotIn("hunter2", str(listed))
        self.assertEqual(["email_app_password"], [item["name"] for item in listed])

    def test_there_is_no_way_to_read_a_value_back(self):
        self.client.put("/api/secrets/token", json={"value": "hunter2"})

        # Nothing in the API returns it, so a read attempt is simply not routed.
        self.assertEqual(405, self.client.get("/api/secrets/token").status_code)

    def test_a_secret_can_be_deleted(self):
        self.client.put("/api/secrets/token", json={"value": "hunter2"})

        self.assertEqual(200, self.client.delete("/api/secrets/token").status_code)
        self.assertEqual([], self.client.get("/api/secrets").json())

    def test_deleting_an_unknown_secret_is_a_404(self):
        self.assertEqual(404, self.client.delete("/api/secrets/nope").status_code)

    def test_an_empty_value_is_rejected(self):
        response = self.client.put("/api/secrets/token", json={"value": ""})

        self.assertEqual(422, response.status_code)


class FileApiTests(ApiTestCase):
    """The phone's view: browse, download, upload, over the same token."""

    def setUp(self):
        super().setUp()
        import assistant.files as files
        import assistant.api.app as api_app

        self.files = files
        self.share = (Path(self.tempdir) / "Shared").resolve()
        (self.share / "reports").mkdir(parents=True)
        (self.share / "invoice.pdf").write_bytes(b"a bill")
        (self.share / "reports" / "q3.txt").write_text("numbers")

        self._real_setting = files.get_setting
        self._real_api_setting = api_app.get_setting
        files.get_setting = self._setting
        api_app.get_setting = self._setting
        self.addCleanup(self._restore)

    def _restore(self):
        import assistant.api.app as api_app
        self.files.get_setting = self._real_setting
        api_app.get_setting = self._real_api_setting

    def _setting(self, key, default=None):
        if key == "file_shares":
            return [str(self.share)]
        if key == "files_allow_write":
            return True
        if key == "files_allow_delete":
            return getattr(self, "allow_delete", False)
        return self._real_setting(key, default)

    def test_the_phone_is_told_what_is_shared(self):
        body = self.client.get("/api/files/shares").json()

        self.assertEqual([str(self.share)], [item["path"] for item in body])

    def test_a_folder_can_be_browsed(self):
        body = self.client.get("/api/files",
                               params={"path": "Shared"}).json()

        self.assertEqual(["reports", "invoice.pdf"],
                         [item["name"] for item in body["entries"]])

    def test_browsing_starts_at_the_shared_folders(self):
        """With no path, a phone that knows nothing still gets somewhere."""
        body = self.client.get("/api/files").json()

        self.assertEqual(["Shared"], [item["name"] for item in body["entries"]])

    def test_a_file_can_be_downloaded(self):
        response = self.client.get("/api/files/download",
                                   params={"path": "invoice.pdf"})

        self.assertEqual(200, response.status_code)
        self.assertEqual(b"a bill", response.content)

    def test_a_download_is_recorded_on_the_timeline(self):
        self.client.get("/api/files/download", params={"path": "invoice.pdf"})

        messages = [event.message for event in self.plane.list_events()]
        self.assertTrue(any("Sent invoice.pdf" in message for message in messages))

    def test_a_file_outside_the_shares_is_a_404(self):
        response = self.client.get("/api/files/download",
                                   params={"path": "/etc/passwd"})

        self.assertEqual(404, response.status_code)

    def test_climbing_out_of_a_share_is_a_404(self):
        response = self.client.get("/api/files",
                                   params={"path": str(self.share / ".." / "..")})

        self.assertEqual(404, response.status_code)

    def test_a_file_can_be_searched_for_by_name(self):
        body = self.client.get("/api/files/search", params={"query": "q3"}).json()

        self.assertEqual(["q3.txt"], [item["name"] for item in body])

    def test_the_phone_can_send_a_file_to_the_computer(self):
        response = self.client.post(
            "/api/files/upload",
            files={"file": ("photo.jpg", b"image bytes", "image/jpeg")},
            data={"folder": ""})

        self.assertEqual(201, response.status_code)
        self.assertEqual(b"image bytes", (self.share / "photo.jpg").read_bytes())

    def test_an_upload_is_recorded_on_the_timeline(self):
        self.client.post("/api/files/upload",
                         files={"file": ("photo.jpg", b"bytes", "image/jpeg")},
                         data={"folder": ""})

        messages = [event.message for event in self.plane.list_events()]
        self.assertTrue(any("Received photo.jpg" in message for message in messages))

    def test_a_folder_can_be_made_and_a_file_moved_into_it(self):
        self.client.post("/api/files/folder", json={"path": "bills"})

        moved = self.client.post("/api/files/move",
                                 json={"source": "invoice.pdf",
                                       "destination": str(self.share / "bills" / "invoice.pdf")})

        self.assertEqual(200, moved.status_code)
        self.assertTrue((self.share / "bills" / "invoice.pdf").exists())

    def test_deleting_is_off_unless_it_is_switched_on(self):
        response = self.client.delete("/api/files", params={"path": "invoice.pdf"})

        self.assertEqual(403, response.status_code)
        self.assertTrue((self.share / "invoice.pdf").exists())

    def test_deleting_works_once_switched_on(self):
        self.allow_delete = True

        response = self.client.delete("/api/files", params={"path": "invoice.pdf"})

        self.assertEqual(200, response.status_code)
        self.assertFalse((self.share / "invoice.pdf").exists())


if __name__ == "__main__":
    unittest.main()

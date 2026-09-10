"""Tests for the capability catalog, the policy engine and the broker.

An agent must never receive access as a side effect of asking for it, so these
cover what is granted, what is held for the user, and what is refused outright.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from assistant.control import capabilities
from assistant.control.models import (
    ApprovalStatus,
    Decision,
    PermissionStatus,
    PolicyRule,
    RiskLevel,
    TaskStatus,
)
from assistant.control.policy import PolicyEngine
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class CatalogTests(unittest.TestCase):
    def test_every_entry_has_a_risk_level_and_a_description(self):
        for name, (risk, description) in capabilities.CATALOG.items():
            self.assertIsInstance(risk, RiskLevel, name)
            self.assertTrue(description, name)

    def test_an_unknown_capability_is_treated_as_critical(self):
        self.assertEqual(RiskLevel.CRITICAL, capabilities.risk_for("made.up.thing"))
        self.assertFalse(capabilities.is_known("made.up.thing"))

    def test_reading_is_lower_risk_than_writing(self):
        self.assertLess(capabilities.risk_for("google.drive.read").rank,
                        capabilities.risk_for("google.drive.write").rank)
        self.assertLess(capabilities.risk_for("google.drive.write").rank,
                        capabilities.risk_for("google.drive.delete").rank)

    def test_deploying_to_production_is_critical(self):
        self.assertEqual(RiskLevel.CRITICAL,
                         capabilities.risk_for("gcp.cloud_run.deploy"))

    def test_the_catalog_can_be_filtered_by_namespace(self):
        names = [entry["capability"] for entry in capabilities.catalog("google.gmail.*")]
        self.assertEqual(["google.gmail.draft", "google.gmail.read", "google.gmail.send"], names)

    def test_vave_tools_map_onto_catalog_names(self):
        for tool, capability in capabilities.TOOL_CAPABILITIES.items():
            self.assertTrue(capabilities.is_known(capability),
                            f"{tool} maps to unknown capability {capability}")

    def test_a_tool_without_a_capability_reports_none(self):
        self.assertEqual("system.shell.run",
                         capabilities.capability_for_tool("run_terminal_command"))
        self.assertEqual("", capabilities.capability_for_tool("tell_time"))


class PolicyTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-policy-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.policy = PolicyEngine(self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tempdir, ignore_errors=True)


class PolicyDefaultTests(PolicyTestCase):
    def test_low_risk_runs_without_asking(self):
        self.assertTrue(self.policy.evaluate("browser.navigate").allowed)

    def test_high_risk_asks_the_user(self):
        self.assertTrue(self.policy.evaluate("google.gmail.send").needs_approval)

    def test_critical_risk_asks_the_user(self):
        self.assertTrue(self.policy.evaluate("gcp.cloud_run.deploy").needs_approval)

    def test_an_unknown_capability_asks_rather_than_assuming(self):
        judgement = self.policy.evaluate("something.invented")
        self.assertTrue(judgement.needs_approval)
        self.assertIn("does not recognise", judgement.reason)

    def test_the_reason_is_written_for_a_person(self):
        self.assertIn("Send email as you",
                      self.policy.evaluate("google.gmail.send").reason)


class PolicyRuleTests(PolicyTestCase):
    def test_a_rule_can_deny_what_risk_would_allow(self):
        self.policy.add_rule("browser.navigate", Decision.DENY)

        self.assertTrue(self.policy.evaluate("browser.navigate").denied)

    def test_a_rule_can_allow_what_risk_would_hold(self):
        self.policy.add_rule("google.gmail.send", Decision.ALLOW)

        self.assertTrue(self.policy.evaluate("google.gmail.send").allowed)

    def test_a_namespace_rule_covers_the_whole_namespace(self):
        self.policy.add_rule("google.drive.*", Decision.DENY)

        self.assertTrue(self.policy.evaluate("google.drive.read").denied)
        self.assertTrue(self.policy.evaluate("google.drive.write").denied)
        self.assertFalse(self.policy.evaluate("google.gmail.read").denied)

    def test_the_narrowest_rule_wins(self):
        self.policy.add_rule("*", Decision.ALLOW)
        self.policy.add_rule("google.gmail.send", Decision.DENY)

        self.assertTrue(self.policy.evaluate("google.gmail.send").denied)
        self.assertTrue(self.policy.evaluate("google.drive.read").allowed)

    def test_a_deny_beats_an_allow_of_equal_narrowness(self):
        self.policy.add_rule("google.gmail.send", Decision.ALLOW)
        self.policy.add_rule("google.gmail.send", Decision.DENY)

        self.assertTrue(self.policy.evaluate("google.gmail.send").denied)

    def test_a_rule_can_target_one_agent_only(self):
        self.policy.add_rule("filesystem.write", Decision.DENY, agent_id="browser-agent")

        self.assertTrue(self.policy.evaluate("filesystem.write",
                                             agent_id="browser-agent").denied)
        self.assertFalse(self.policy.evaluate("filesystem.write",
                                              agent_id="local-agent").denied)

    def test_a_rule_can_target_one_task_only(self):
        self.policy.add_rule("google.gmail.send", Decision.ALLOW, task_id="t1")

        self.assertTrue(self.policy.evaluate("google.gmail.send", task_id="t1").allowed)
        self.assertTrue(self.policy.evaluate("google.gmail.send",
                                             task_id="t2").needs_approval)

    def test_a_removed_rule_stops_applying(self):
        rule = self.policy.add_rule("browser.navigate", Decision.DENY)

        self.policy.remove_rule(rule.id)

        self.assertTrue(self.policy.evaluate("browser.navigate").allowed)

    def test_rules_survive_a_restart(self):
        self.policy.add_rule("browser.navigate", Decision.DENY, reason="Offline week")
        self.store.close()

        store = ControlStore(Path(self.tempdir) / "control.db")
        self.addCleanup(store.close)
        judgement = PolicyEngine(store).evaluate("browser.navigate")

        self.assertTrue(judgement.denied)
        self.assertEqual("Offline week", judgement.reason)

    def test_specificity_ranks_narrow_above_broad(self):
        broad = PolicyRule(capability="*")
        namespaced = PolicyRule(capability="google.*")
        exact = PolicyRule(capability="google.gmail.send")
        targeted = PolicyRule(capability="google.gmail.send", agent_id="a1")

        self.assertLess(broad.specificity, namespaced.specificity)
        self.assertLess(namespaced.specificity, exact.specificity)
        self.assertLess(exact.specificity, targeted.specificity)


class BrokerTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-broker-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tempdir, ignore_errors=True)


class BrokerTests(BrokerTestCase):
    def test_a_low_risk_request_is_granted_immediately(self):
        result = self.plane.request_capability("browser.navigate")

        self.assertEqual("granted", result["status"])
        self.assertIsNotNone(result["permission"])
        self.assertTrue(self.plane.has_capability("browser.navigate"))

    def test_a_high_risk_request_waits_for_the_user(self):
        result = self.plane.request_capability("google.gmail.send")

        self.assertEqual("waiting", result["status"])
        self.assertIsNone(result["permission"])
        self.assertFalse(self.plane.has_capability("google.gmail.send"))

    def test_a_denied_request_grants_nothing(self):
        self.plane.add_policy_rule("filesystem.write", Decision.DENY)

        result = self.plane.request_capability("filesystem.write")

        self.assertEqual("denied", result["status"])
        self.assertFalse(self.plane.has_capability("filesystem.write"))

    def test_a_grant_is_scoped_to_its_task(self):
        task = self.plane.create_task("Tidy up")
        self.plane.request_capability("browser.navigate", task_id=task.id)

        self.assertTrue(self.plane.has_capability("browser.navigate", task_id=task.id))
        self.assertFalse(self.plane.has_capability("browser.navigate",
                                                   task_id="another-task"))

    def test_a_grant_is_released_when_its_task_finishes(self):
        task = self.plane.create_task("Tidy up")
        self.plane.request_capability("browser.navigate", task_id=task.id)

        self.plane.complete_task(task.id, "Done.")

        self.assertFalse(self.plane.has_capability("browser.navigate", task_id=task.id))

    def test_the_timeline_says_what_was_asked_for(self):
        self.plane.request_capability("google.drive.read")

        messages = [event.message for event in self.plane.list_events()]
        self.assertIn("Asked to read your drive files.", messages)

    def test_a_refusal_is_recorded(self):
        self.plane.add_policy_rule("browser.purchase", Decision.DENY,
                                   reason="Never buy anything.")

        self.plane.request_capability("browser.purchase")

        messages = [event.message for event in self.plane.list_events()]
        self.assertTrue(any("Refused" in message for message in messages))

    def test_requesting_is_refused_while_stopped(self):
        self.plane.emergency_stop()

        with self.assertRaises(RuntimeError):
            self.plane.request_capability("browser.navigate")


class ApprovalReleasesAccessTests(BrokerTestCase):
    def test_approving_releases_exactly_that_capability(self):
        task = self.plane.create_task("Send the invoice")
        result = self.plane.request_capability("google.gmail.send", task_id=task.id)
        approval_id = result["approval"]["id"]

        self.plane.resolve_approval(approval_id, approved=True)

        self.assertTrue(self.plane.has_capability("google.gmail.send", task_id=task.id))
        self.assertFalse(self.plane.has_capability("google.gmail.read", task_id=task.id))

    def test_declining_releases_nothing(self):
        task = self.plane.create_task("Send the invoice")
        result = self.plane.request_capability("google.gmail.send", task_id=task.id)

        self.plane.resolve_approval(result["approval"]["id"], approved=False)

        self.assertFalse(self.plane.has_capability("google.gmail.send", task_id=task.id))
        self.assertEqual(TaskStatus.CANCELLED, self.plane.get_task(task.id).status)

    def test_the_approval_carries_what_it_would_release(self):
        result = self.plane.request_capability("gcp.cloud_run.deploy",
                                               agent_id="deploy-agent")
        approval = result["approval"]

        self.assertEqual("gcp.cloud_run.deploy", approval["capability"])
        self.assertEqual("critical", approval["risk"])
        self.assertEqual("deploy-agent", approval["agent_id"])

    def test_the_question_reads_as_plain_language(self):
        result = self.plane.request_capability("google.gmail.send")

        self.assertEqual("Can I send email as you?", result["approval"]["question"])

    def test_an_approval_without_a_capability_grants_nothing(self):
        approval = self.plane.request_approval(action="Merge changes",
                                               question="Shall I merge?")

        self.plane.resolve_approval(approval.id, approved=True)

        self.assertEqual([], self.plane.list_permissions(active_only=True))

    def test_the_grant_lasts_as_long_as_the_request_asked_for(self):
        result = self.plane.request_capability("google.gmail.send", seconds=60)

        self.plane.resolve_approval(result["approval"]["id"], approved=True)

        permission = self.plane.list_permissions(active_only=True)[0]
        self.assertEqual(PermissionStatus.ACTIVE, permission.status)
        self.assertLessEqual(permission.expires_at - permission.granted_at, 61)


if __name__ == "__main__":
    unittest.main()

"""Tests for the typed event stream, the audit fields and notifications.

The timeline records everything; a notification interrupts a person. These
cover both, and that a broken channel never stops the work.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from assistant.control.executor import TaskExecutor
from assistant.control.models import Decision, EventType
from assistant.control.notifier import Notification, Notifier, TelegramChannel
from assistant.control.service import AGENT_STALE_SECONDS, ControlPlane
from assistant.control.store import ControlStore
from assistant.control.models import now


class EventTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-event-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def types(self, task_id=None):
        return [event.type for event in self.plane.list_events(task_id=task_id)]


class LifecycleEventTests(EventTestCase):
    def test_a_task_reports_every_transition(self):
        task = self.plane.create_task("Tidy my notes", steps=["Reading notes"])
        TaskExecutor(plane=self.plane, runner=lambda i, c: "Read them.",
                     max_attempts=1).run(task.id)

        recorded = self.types(task.id)
        for expected in (EventType.TASK_CREATED, EventType.TASK_STARTED,
                         EventType.STEP_STARTED, EventType.STEP_FINISHED,
                         EventType.TASK_COMPLETED):
            self.assertIn(expected, recorded)

    def test_a_failure_is_typed_as_a_failure(self):
        task = self.plane.create_task("Tidy my notes", steps=["Reading notes"])

        def explode(instruction, context):
            raise RuntimeError("no model")

        TaskExecutor(plane=self.plane, runner=explode, max_attempts=1).run(task.id)

        self.assertIn(EventType.TASK_FAILED, self.types(task.id))

    def test_an_agent_going_quiet_is_typed(self):
        self.plane.register_helper("Research agent", ["research"])

        self.plane.sweep(at=now() + AGENT_STALE_SECONDS + 1)

        self.assertIn(EventType.AGENT_OFFLINE, self.types())

    def test_every_event_type_is_offered_to_clients(self):
        # A client filters on these strings, so they must all be listable.
        self.assertIn("task_started", [event.value for event in EventType])
        self.assertIn("device_disconnected", [event.value for event in EventType])


class AuditFieldTests(EventTestCase):
    def test_a_capability_request_records_what_was_asked_for(self):
        task = self.plane.create_task("Send the invoice")

        self.plane.request_capability("google.gmail.send", agent_id="mail-agent",
                                      task_id=task.id)

        event = next(item for item in self.plane.list_events()
                     if item.type == EventType.CAPABILITY_REQUESTED)
        self.assertEqual("google.gmail.send", event.capability)
        self.assertEqual("high", event.risk)
        self.assertEqual("mail-agent", event.agent_id)

    def test_a_refusal_records_that_it_was_denied(self):
        self.plane.add_policy_rule("browser.purchase", Decision.DENY)

        self.plane.request_capability("browser.purchase")

        event = next(item for item in self.plane.list_events()
                     if item.type == EventType.CAPABILITY_DENIED)
        self.assertEqual("denied", event.result)
        self.assertEqual("critical", event.risk)

    def test_an_approval_links_its_events_together(self):
        result = self.plane.request_capability("google.gmail.send")
        approval_id = result["approval"]["id"]

        self.plane.resolve_approval(approval_id, approved=True)

        linked = [event for event in self.plane.list_events()
                  if event.approval_id == approval_id]
        self.assertEqual({EventType.APPROVAL_REQUESTED, EventType.APPROVAL_RESOLVED},
                         {event.type for event in linked})
        self.assertEqual("approved",
                         next(event.result for event in linked
                              if event.type == EventType.APPROVAL_RESOLVED))

    def test_a_finished_step_records_how_it_went(self):
        task = self.plane.create_task("One part", steps=["Reading notes"])
        self.plane.start_step(task.id, 0)
        self.plane.finish_step(task.id, 0, "Read them.")

        event = next(item for item in self.plane.list_events()
                     if item.type == EventType.STEP_FINISHED)
        self.assertEqual("ok", event.result)
        self.assertEqual(0, event.metadata["position"])

    def test_events_can_be_filtered_by_type(self):
        task = self.plane.create_task("One part", steps=["Reading notes"])
        self.plane.start_step(task.id, 0)

        events = self.plane.list_events(types=["step_started"])

        self.assertEqual([EventType.STEP_STARTED], [event.type for event in events])

    def test_audit_fields_survive_a_reopen(self):
        self.plane.request_capability("google.gmail.send", agent_id="mail-agent")
        self.store.close()

        store = ControlStore(Path(self.tempdir) / "control.db")
        self.addCleanup(store.close)

        event = next(item for item in store.list_events()
                     if item.type == EventType.CAPABILITY_REQUESTED)
        self.assertEqual("mail-agent", event.agent_id)
        self.assertEqual("high", event.risk)


class _Recorder:
    """A channel that remembers what it was asked to deliver."""

    name = "recorder"

    def __init__(self):
        self.delivered = []

    def deliver(self, notification):
        self.delivered.append(notification)


class NotifierTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.channel = _Recorder()
        self.notifier = Notifier(self.plane, channels=[self.channel])
        self.addCleanup(self.notifier.close)

    def messages(self):
        return [item.event.message for item in self.channel.delivered]

    def test_an_approval_reaches_the_phone(self):
        self.plane.request_capability("google.gmail.send")

        self.assertTrue(self.notifier.flush())
        self.assertEqual(1, len(self.channel.delivered))
        self.assertTrue(self.channel.delivered[0].needs_answer)
        self.assertEqual("action", self.channel.delivered[0].urgency)

    def test_a_finished_task_is_reported(self):
        task = self.plane.create_task("Tidy my notes")

        self.plane.complete_task(task.id, "Tidied 12 notes.")

        self.assertTrue(self.notifier.flush())
        self.assertIn("Tidied 12 notes.", self.messages())

    def test_routine_progress_is_not_a_notification(self):
        task = self.plane.create_task("Tidy my notes", steps=["Reading notes"])
        self.plane.start_step(task.id, 0)
        self.plane.finish_step(task.id, 0, "Read them.")

        self.assertEqual([], self.channel.delivered)

    def test_a_security_event_is_marked_as_one(self):
        agent = self.plane.register_helper("Rogue agent", ["research"])

        self.plane.kill_helper(agent.id, reason="It kept clicking Buy.")

        self.assertTrue(self.notifier.flush())
        urgencies = [item.urgency for item in self.channel.delivered]
        self.assertIn("security", urgencies)

    def test_an_emergency_stop_is_reported(self):
        self.plane.emergency_stop()

        self.assertTrue(self.notifier.flush())
        self.assertIn("security", [item.urgency for item in self.channel.delivered])

    def test_a_broken_channel_never_stops_the_work(self):
        class Broken:
            name = "broken"

            def deliver(self, notification):
                raise RuntimeError("the phone is off")

        self.notifier.add_channel(Broken())
        task = self.plane.create_task("Tidy my notes")

        self.plane.complete_task(task.id, "Tidied 12 notes.")

        self.assertTrue(self.notifier.flush())
        self.assertIn("Tidied 12 notes.", self.messages())

    def test_recent_notifications_are_kept_for_catching_up(self):
        task = self.plane.create_task("Tidy my notes")
        self.plane.complete_task(task.id, "Tidied 12 notes.")

        recent = self.notifier.recent()

        self.assertEqual("Tidied 12 notes.", recent[0]["message"])
        self.assertEqual("task_completed", recent[0]["type"])

    def test_history_does_not_grow_without_limit(self):
        notifier = Notifier(self.plane, channels=[], history=3)
        self.addCleanup(notifier.close)

        for index in range(5):
            task = self.plane.create_task(f"Task {index}")
            self.plane.complete_task(task.id, f"Done {index}.")

        self.assertEqual(3, len(notifier.recent(limit=50)))

    def test_a_removed_channel_stops_receiving(self):
        self.notifier.remove_channel(self.channel)
        task = self.plane.create_task("Tidy my notes")

        self.plane.complete_task(task.id, "Tidied 12 notes.")

        self.assertEqual([], self.channel.delivered)

    def test_closing_the_notifier_detaches_it(self):
        before = len(self.plane._subscribers)

        self.notifier.close()

        self.assertEqual(before - 1, len(self.plane._subscribers))


class TelegramChannelTests(EventTestCase):
    def test_the_text_says_what_kind_of_thing_it_is(self):
        sent = []
        channel = TelegramChannel(send=sent.append)
        notifier = Notifier(self.plane, channels=[channel])
        self.addCleanup(notifier.close)

        self.plane.request_capability("google.gmail.send")

        self.assertTrue(notifier.flush())
        self.assertEqual(["Needs you: Waiting for your approval: "
                          "Can I send email as you?"], sent)

    def test_a_problem_is_labelled_as_a_problem(self):
        event = next(iter(self.plane.list_events()), None)
        self.plane.record("Something broke.", EventType.TASK_FAILED)
        event = self.plane.list_events(limit=1)[0]

        self.assertEqual("Problem: Something broke.",
                         Notification(event, "problem").as_text())


if __name__ == "__main__":
    unittest.main()

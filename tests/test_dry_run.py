"""Consequence-free rehearsal mode regressions."""

import json
import os
import sys
import threading
import unittest
from unittest import mock

from tests.support import VaveTestCase


class GuardDryRunTests(VaveTestCase):
    def setUp(self):
        super().setUp()
        from assistant import call_context

        self._token = call_context.set_dry_run(True)

    def tearDown(self):
        from assistant import call_context

        call_context.reset_dry_run(self._token)
        super().tearDown()

    def test_destructive_tool_is_simulated_without_calling_function(self):
        from assistant import guard

        action = mock.Mock(return_value="should not happen")
        guard.register_name("destructive", "dry_delete")
        with mock.patch("assistant.guard.audit.append", return_value="audit-1"):
            result = guard.call(
                action,
                _tool_name="dry_delete",
                path="notes.txt",
            )
        action.assert_not_called()
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["simulated"])
        self.assertIn("No action was taken", result["summary"])

    def test_unknown_sensitive_tool_fails_closed_to_simulation(self):
        from assistant import guard

        action = mock.Mock()
        with mock.patch("assistant.guard.audit.append", return_value="audit-2"):
            result = guard.call(action, _tool_name="unclassified_dry_action")
        action.assert_not_called()
        self.assertEqual("sensitive", result["tier"])
        self.assertTrue(result["simulated"])

    def test_capability_risk_overrides_stale_safe_registration(self):
        from assistant import guard

        action = mock.Mock()
        guard.register_name("safe", "propose_new_feature")
        with mock.patch("assistant.guard.audit.append", return_value="audit-risk"):
            result = guard.call(action, _tool_name="propose_new_feature")
        action.assert_not_called()
        self.assertEqual("destructive", result["tier"])

    def test_dry_run_aware_orchestrator_may_build_plan(self):
        from assistant import guard

        planner = mock.Mock(return_value="DRY RUN: plan only")
        guard.register_name("destructive", "run_coding_workflow")
        with mock.patch("assistant.guard.audit.append", return_value="audit-plan"):
            result = guard.call(
                planner,
                _tool_name="run_coding_workflow",
                goal="Build feature",
            )
        planner.assert_called_once_with(goal="Build feature")
        self.assertEqual("DRY RUN: plan only", result)

    def test_safe_read_still_executes(self):
        from assistant import guard

        read = mock.Mock(return_value="visible state")
        guard.register_name("safe", "dry_safe_read")
        with mock.patch("assistant.guard.audit.append", return_value="audit-3"):
            result = guard.call(read, _tool_name="dry_safe_read")
        read.assert_called_once_with()
        self.assertEqual("visible state", result)

    def test_hard_denial_remains_blocked_not_simulated_as_allowed(self):
        from assistant import guard

        action = mock.Mock()
        with mock.patch("assistant.guard.audit.append", return_value="audit-4"):
            result = guard.call(
                action,
                _tool_name="run_terminal_command",
                command="rm -rf /",
            )
        action.assert_not_called()
        self.assertFalse(result["would_execute"])
        self.assertTrue(result["blocked"])

    def test_simulation_redacts_secret_arguments(self):
        from assistant import guard

        action = mock.Mock()
        guard.register_name("sensitive", "dry_secret_action")
        with mock.patch("assistant.guard.audit.append", return_value="audit-5"):
            result = guard.call(
                action,
                _tool_name="dry_secret_action",
                api_key="sk-live-super-secret-value",
            )
        self.assertNotIn("sk-live-super-secret-value", result["summary"])
        self.assertIn("***", str(result["args"]))


class DryRunContextTests(VaveTestCase):
    def test_process_override_reaches_plain_worker_threads(self):
        from assistant import call_context

        seen = []
        previous = call_context.set_process_dry_run(True)
        try:
            worker = threading.Thread(
                target=lambda: seen.append(call_context.is_dry_run())
            )
            worker.start()
            worker.join(2)
        finally:
            call_context.set_process_dry_run(previous)
        self.assertEqual([True], seen)

    def test_spawn_thread_copies_request_rehearsal_context(self):
        from assistant import call_context

        seen = []
        token = call_context.set_dry_run(True)
        try:
            worker = call_context.spawn_thread(
                target=lambda: seen.append(call_context.is_dry_run())
            )
            worker.join(2)
        finally:
            call_context.reset_dry_run(token)
        self.assertEqual([True], seen)


class DryRunCommandTests(VaveTestCase):
    def test_rehearse_prefix_routes_request_under_temporary_context(self):
        from assistant import call_context, commands

        seen = {}

        def fake_ask(command, auto_confirm=False):
            seen["command"] = command
            seen["dry_run"] = call_context.is_dry_run()

        with mock.patch("assistant.commands.ask_ai", side_effect=fake_ask), \
             mock.patch("assistant.commands.speak"):
            result = commands.execute_command("rehearse shut down the laptop")
        self.assertTrue(result)
        self.assertEqual("shut down the laptop", seen["command"])
        self.assertTrue(seen["dry_run"])
        self.assertFalse(call_context.is_dry_run())

    def test_rehearse_that_reuses_last_command(self):
        from assistant import commands

        commands._last_command = "send the status email"
        with mock.patch("assistant.commands.ask_ai") as ask, \
             mock.patch("assistant.commands.speak"):
            commands.execute_command("rehearse that")
        ask.assert_called_once_with("send the status email", auto_confirm=True)

    def test_enable_and_disable_persistent_mode(self):
        from assistant import commands

        with mock.patch("assistant.commands.update_setting") as update, \
             mock.patch("assistant.commands.speak"):
            commands.execute_command("enable dry run mode")
            commands.execute_command("disable dry run mode")
        self.assertEqual(
            [
                mock.call("dry_run_enabled", True),
                mock.call("dry_run_enabled", False),
            ],
            update.call_args_list,
        )


class DryRunCliTests(VaveTestCase):
    def test_text_mode_sets_and_resets_process_dry_run(self):
        import main
        from assistant import call_context

        seen = []
        controller = mock.Mock()
        controller.handle_text_command.side_effect = lambda _text: seen.append(
            call_context.is_dry_run()
        )
        with mock.patch("main.AssistantController", return_value=controller), \
             mock.patch("assistant.bootstrap.bootstrap_safety", return_value=mock.Mock()):
            result = main.main([
                "--text", "send an email", "--dry-run", "--no-speech"
            ])
        self.assertEqual(0, result)
        self.assertEqual([True], seen)
        self.assertFalse(call_context.is_dry_run())


class DryRunWorkflowTests(VaveTestCase):
    def setUp(self):
        super().setUp()
        from assistant import call_context

        self._token = call_context.set_dry_run(True)

    def tearDown(self):
        from assistant import call_context

        call_context.reset_dry_run(self._token)
        super().tearDown()

    def test_project_manager_plans_without_delegating_or_testing(self):
        from assistant.project_manager import ManagerTask, ProjectManager, VisionSpec

        spec = VisionSpec(
            goal="Build feature",
            summary="Plan",
            test_command=f"{sys.executable} -m unittest",
            tasks=[ManagerTask(
                id="build",
                engine="opencode",
                prompt="write it",
                target_files=["feature.py"],
            )],
        )
        test_executor = mock.Mock()
        manager = ProjectManager(
            llm_caller=mock.Mock(),
            test_executor=test_executor,
        )
        with mock.patch.object(manager, "synthesize_spec", return_value=spec), \
             mock.patch("assistant.integrations.opencode.run_opencode") as run_engine:
            result = manager.run_managed_project(
                "Build feature", project_path=self.temp_dir
            )
        self.assertTrue(result["dry_run"])
        self.assertEqual("simulated", spec.tasks[0].status)
        test_executor.assert_not_called()
        run_engine.assert_not_called()

    def test_agent_skips_policy_and_secret_resolution_for_simulation(self):
        from assistant import ai_brain, guard

        action = mock.Mock(return_value="must not execute")
        guard.register_name("sensitive", "dry_outward_action")
        responses = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "dry_outward_action",
                        "arguments": {"token": "secret://private_token"},
                    },
                }],
            },
            {"role": "assistant", "content": "DRY RUN: would act."},
        ]
        authorize = mock.Mock(return_value=(False, "approval required"))
        resolve = mock.Mock(return_value={"token": "plaintext"})
        tools = [{
            "type": "function",
            "function": {
                "name": "dry_outward_action",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        with mock.patch.dict(
            ai_brain.AVAILABLE_FUNCTIONS,
            {"dry_outward_action": action},
        ), mock.patch(
            "assistant.ai_brain.chat_with_fallback",
            side_effect=responses,
        ), mock.patch(
            "assistant.ai_brain._stopped_short",
            return_value=False,
        ), mock.patch(
            "assistant.ai_brain._remaining_work",
            return_value="",
        ), mock.patch(
            "assistant.guard.audit.append",
            return_value="audit-agent",
        ):
            result = ai_brain._agent_loop(
                [{"role": "user", "content": "do the thing"}],
                tools=tools,
                authorize=authorize,
                resolve_secrets=resolve,
                return_details=True,
            )
        self.assertEqual("completed", result["outcome"])
        action.assert_not_called()
        authorize.assert_not_called()
        resolve.assert_not_called()

    def test_rehearsal_never_sends_planning_context_to_hosted_model(self):
        from assistant import ai_brain

        settings = {
            "featherless_enabled": True,
            "featherless_api_key": "secret-value",
            "ollama_url": "http://localhost:11434",
            "ollama_num_ctx": 1024,
            "ollama_num_gpu": None,
            "llm_chat_temperature": 0.1,
            "llm_timeout_seconds": 5,
        }
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "message": {"role": "assistant", "content": "local"}
        }).encode("utf-8")
        response.__enter__.return_value = response
        with mock.patch(
            "assistant.ai_brain.get_setting",
            side_effect=lambda key, default=None: settings.get(key, default),
        ), mock.patch(
            "assistant.ai_brain.urllib.request.urlopen",
            return_value=response,
        ) as open_url:
            result = ai_brain.query_local_llm_chat(
                [{"role": "user", "content": "plan privately"}],
                tools=False,
            )
        requested = open_url.call_args.args[0].full_url
        self.assertTrue(requested.startswith("http://localhost:11434/"))
        self.assertNotIn("featherless", requested)
        self.assertEqual("local", result["content"])

    def test_coding_workflow_does_not_run_agents_or_tests(self):
        from assistant import coding_workflow

        plan = [{
            "id": "build",
            "file": "app.py",
            "action": "create",
            "description": "build",
            "needs": [],
            "test_command": f"{sys.executable} -m unittest",
        }]
        with mock.patch(
            "assistant.coding_workflow.plan_work",
            return_value=(plan, [], json.dumps(plan)),
        ), mock.patch(
            "assistant.coding_workflow.run_task_waves"
        ) as waves, mock.patch(
            "assistant.coding_workflow.run_test_command"
        ) as run_test:
            result = coding_workflow.run_workflow(
                "Build app", project_path=self.temp_dir
            )
        self.assertTrue(result["dry_run"])
        self.assertIn("DRY RUN", result["report"])
        waves.assert_not_called()
        run_test.assert_not_called()

    def test_coding_workflow_dry_run_never_commits(self):
        from assistant import coding_workflow

        result = {
            "ok": True,
            "dry_run": True,
            "report": "DRY RUN",
        }
        with mock.patch(
            "assistant.coding_workflow.run_workflow",
            return_value=result,
        ), mock.patch(
            "assistant.dev_tools.git_auto_commit_and_push"
        ) as commit:
            report = coding_workflow.run_coding_workflow(
                "Build app", self.temp_dir, commit=True
            )
        self.assertEqual("DRY RUN", report)
        commit.assert_not_called()

    def test_approved_gap_stays_approved_during_rehearsal(self):
        import assistant.self_evolution.gaps as gaps_module
        from assistant.self_evolution import (
            approve_gap,
            get_capability_gap,
            implement_capability,
            record_capability_gap,
        )

        original = gaps_module.GAPS_FILE
        gaps_module.GAPS_FILE = os.path.join(self.temp_dir, "gaps.json")
        try:
            gap = record_capability_gap("Task", "Feature", "Missing", "Build")
            approve_gap(gap.id)
            with mock.patch(
                "assistant.self_evolution.auto_implementer.ProjectManager"
            ) as manager:
                result = implement_capability(gap.id)
            self.assertTrue(result["dry_run"])
            self.assertEqual("approved", get_capability_gap(gap.id).status)
            manager.assert_not_called()
        finally:
            gaps_module.GAPS_FILE = original

    def test_workspace_write_is_simulated_before_handler(self):
        from assistant.workspace.gateway import WorkspaceGateway

        gateway = WorkspaceGateway()
        handler = mock.Mock()
        gateway._capabilities["google.gmail.send"] = handler
        result = gateway.execute_capability(
            "google.gmail.send",
            to="person@example.com",
            subject="Status",
            body="Done",
        )
        handler.assert_not_called()
        self.assertTrue(result["dry_run"])
        self.assertNotIn("Done", result["summary"])

    def test_home_control_is_simulated_before_client_or_secret_lookup(self):
        from assistant.home import control_device

        client = mock.Mock()
        result = control_device(
            "light.office",
            "off",
            _client_override=client,
        )
        self.assertIn("DRY RUN", result)
        client.call_service.assert_not_called()


if __name__ == "__main__":
    unittest.main()

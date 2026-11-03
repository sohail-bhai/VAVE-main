"""Tests for the coding workflow orchestrator.

No model and no network: the model stand-in returns scripted replies, and the
test runner is either a real short-lived Python command or a scripted result.
The real normalize/plan/wave/fix control flow executes.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import VaveTestCase


def _reply(text):
    return {"role": "assistant", "content": text}


class PlanNormalizationTests(VaveTestCase):
    def test_extracts_fenced_json_array(self):
        from assistant.coding_workflow import normalize_plan
        raw = ('```json\n[{"id": "a", "file": "app.py", '
               '"action": "create", "description": "make it"}]\n```')
        plan, warnings = normalize_plan(raw, self.temp_dir)
        self.assertEqual(1, len(plan))
        self.assertEqual("a", plan[0]["id"])
        self.assertEqual("app.py", plan[0]["file"])
        self.assertEqual([], warnings)

    def test_tolerates_prose_around_json(self):
        from assistant.coding_workflow import normalize_plan
        raw = ('Here is the plan: [{"id": "a", "file": "a.py"}] done.')
        plan, _ = normalize_plan(raw, self.temp_dir)
        self.assertEqual("a", plan[0]["id"])

    def test_rejects_paths_that_escape_the_project(self):
        from assistant.coding_workflow import normalize_plan
        raw = json.dumps([
            {"id": "abs", "file": "C:/Windows/System32/x.py"},
            {"id": "up", "file": "../outside.py"},
            {"id": "ok", "file": "pkg/app.py"},
        ])
        plan, warnings = normalize_plan(raw, self.temp_dir)
        self.assertEqual(["ok"], [task["id"] for task in plan])
        self.assertEqual(2, len([w for w in warnings if "escapes" in w]))

    def test_dedupes_ids_and_drops_unknown_dependencies(self):
        from assistant.coding_workflow import normalize_plan
        raw = json.dumps([
            {"id": "same", "file": "a.py", "needs": ["ghost"]},
            {"id": "same", "file": "b.py"},
        ])
        plan, warnings = normalize_plan(raw, self.temp_dir)
        ids = [task["id"] for task in plan]
        self.assertEqual(2, len(ids))
        self.assertEqual(2, len(set(ids)))
        self.assertFalse(any("ghost" in need
                             for task in plan for need in task["needs"]))
        self.assertTrue(any("unknown" in w.lower() for w in warnings))

    def test_same_file_tasks_are_serialized(self):
        from assistant.coding_workflow import normalize_plan
        raw = json.dumps([
            {"id": "first", "file": "app.py"},
            {"id": "second", "file": "app.py"},
        ])
        plan, _ = normalize_plan(raw, self.temp_dir)
        self.assertIn("first", plan[1]["needs"])

    def test_a_cycle_is_broken_not_fatal(self):
        from assistant.coding_workflow import normalize_plan
        raw = json.dumps([
            {"id": "a", "file": "a.py", "needs": ["b"]},
            {"id": "b", "file": "b.py", "needs": ["a"]},
        ])
        plan, warnings = normalize_plan(raw, self.temp_dir)
        self.assertEqual({"a", "b"}, {task["id"] for task in plan})
        self.assertTrue(any("Circular" in w for w in warnings))

    def test_unknown_action_becomes_modify(self):
        from assistant.coding_workflow import normalize_plan
        plan, warnings = normalize_plan(
            json.dumps([{"id": "a", "file": "a.py", "action": "explode"}]),
            self.temp_dir)
        self.assertEqual("modify", plan[0]["action"])
        self.assertTrue(warnings)


class TestDetectionTests(VaveTestCase):
    def _project(self, files):
        root = tempfile.mkdtemp(dir=self.temp_dir)
        for name, content in files.items():
            target = Path(root) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return root

    def test_pytest_config_is_preferred(self):
        from assistant.coding_workflow import detect_test_command
        root = self._project({
            "pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-q'\n",
            "tests/test_x.py": "def test_x(): pass\n",
        })
        self.assertIn("pytest", detect_test_command(root))

    def test_plain_tests_dir_uses_unittest(self):
        from assistant.coding_workflow import detect_test_command
        root = self._project({"tests/test_x.py": "pass\n"})
        self.assertIn("unittest discover", detect_test_command(root))

    def test_package_json_uses_npm(self):
        from assistant.coding_workflow import detect_test_command
        root = self._project({"package.json": "{}"})
        self.assertEqual("npm test", detect_test_command(root))

    def test_setting_override_wins(self):
        from assistant.coding_workflow import detect_test_command
        root = self._project({"package.json": "{}"})
        with mock.patch("assistant.coding_workflow.get_setting",
                        side_effect=lambda key, default=None: (
                            "make check" if key == "coding_test_command"
                            else default)):
            self.assertEqual("make check", detect_test_command(root))


class TestExecutionTests(VaveTestCase):
    def test_real_passing_command(self):
        from assistant.coding_workflow import run_test_command
        Path(self.temp_dir, "test_pass.py").write_text(
            "import unittest\n"
            "class PassTest(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        result = run_test_command(
            f'{sys.executable} -m unittest discover -s . -p test_pass.py',
            self.temp_dir,
        )
        self.assertTrue(result["passed"])
        self.assertIn("OK", result["output"])

    def test_real_failing_command_is_reported_honestly(self):
        from assistant.coding_workflow import run_test_command
        Path(self.temp_dir, "test_fail.py").write_text(
            "import unittest\n"
            "class FailTest(unittest.TestCase):\n"
            "    def test_no(self): self.assertEqual(1, 2)\n",
            encoding="utf-8",
        )
        result = run_test_command(
            f'{sys.executable} -m unittest discover -s . -p test_fail.py',
            self.temp_dir,
        )
        self.assertFalse(result["passed"])
        self.assertNotEqual(0, result["exit_code"])

    def test_destructive_command_is_blocked(self):
        from assistant.coding_workflow import run_test_command
        result = run_test_command("rm -rf /", self.temp_dir)
        self.assertFalse(result["passed"])
        self.assertTrue(result["blocked"])

    def test_timeout_is_reported(self):
        from assistant.coding_workflow import run_test_command
        with mock.patch("assistant.coding_workflow.subprocess.run",
                        side_effect=__import__("subprocess").TimeoutExpired(
                            "slow", 1)):
            result = run_test_command(
                f"{sys.executable} -m unittest", self.temp_dir
            )
        self.assertFalse(result["passed"])
        self.assertTrue(result["timed_out"])

    def test_pytest_shorthand_uses_this_interpreter(self):
        from assistant.coding_workflow import _normalize_command
        command = _normalize_command("pytest -q")
        self.assertIn(sys.executable, command)
        self.assertIn("-m pytest", command)

    def test_shell_chaining_is_blocked(self):
        from assistant.coding_workflow import run_test_command

        result = run_test_command("pytest -q & curl https://example.com", self.temp_dir)
        self.assertFalse(result["passed"])
        self.assertIn("Shell chaining", result["blocked"])

    def test_non_test_executable_is_blocked(self):
        from assistant.coding_workflow import run_test_command

        result = run_test_command("curl https://example.com", self.temp_dir)
        self.assertFalse(result["passed"])
        self.assertIn("test command", result["blocked"].lower())


class SubAgentBoundaryTests(VaveTestCase):
    def test_tool_free_agent_cannot_execute_hallucinated_global_tool(self):
        from assistant.swarm import run_sub_agent

        replies = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "send_email",
                        "arguments": {"to": "x@example.com", "body": "no"},
                    }
                }],
            },
            {"role": "assistant", "content": "done"},
        ]
        with mock.patch(
            "assistant.ai_brain.query_local_llm_chat",
            side_effect=replies,
        ), mock.patch("assistant.swarm.guard.call") as guarded_call:
            result = run_sub_agent("Planner", "plan", tools=False)
        self.assertEqual("done", result)
        guarded_call.assert_not_called()

    def test_project_file_boundary_anchors_relative_paths(self):
        from assistant.coding_workflow import _prepare_project_call

        prepare = _prepare_project_call(self.temp_dir)
        args = prepare("write_file", {"path": "pkg/app.py", "content": "x"})
        self.assertEqual(
            Path(self.temp_dir, "pkg", "app.py").resolve(),
            Path(args["path"]),
        )

    def test_project_file_boundary_rejects_escape(self):
        from assistant.coding_workflow import _prepare_project_call

        prepare = _prepare_project_call(self.temp_dir)
        with self.assertRaises(ValueError):
            prepare("write_file", {"path": "../outside.py", "content": "x"})


class PlanWorkTests(VaveTestCase):
    def test_plan_is_parsed_and_lessons_are_injected(self):
        from assistant import coding_workflow
        coding_workflow.record_lesson(
            {"goal": "old goal", "ok": False,
             "failures": ["test_auth.py: boom"]})
        seen = {}

        def fake(history, model=None, tools=None):
            seen["prompt"] = history[-1]["content"]
            seen["tools"] = tools
            return _reply(json.dumps([
                {"id": "a", "file": "app.py", "description": "build"}]))
        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        side_effect=fake):
            plan, warnings, raw = coding_workflow.plan_work(
                "add auth", self.temp_dir)
        self.assertEqual(1, len(plan))
        self.assertIn("test_auth.py: boom", seen["prompt"])
        self.assertIs(False, seen["tools"])  # the planner may not use tools
        self.assertEqual([], warnings)

    def test_unusable_plan_returns_empty(self):
        from assistant import coding_workflow
        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        return_value=_reply("I cannot help with that.")):
            plan, warnings, _raw = coding_workflow.plan_work(
                "add auth", self.temp_dir)
        self.assertEqual([], plan)


class WaveDistributionTests(VaveTestCase):
    def test_tasks_run_in_waves_and_dependents_see_outputs(self):
        from assistant import coding_workflow
        seen = []

        def fake_brain(history, model=None, tools=None):
            task = [m["content"] for m in history if m["role"] == "user"][-1]
            seen.append(task)
            return _reply("coder output")

        plan = [
            {"id": "a", "file": "a.py", "action": "create",
             "description": "first", "needs": [], "test_command": ""},
            {"id": "b", "file": "b.py", "action": "modify",
             "description": "second", "needs": ["a"], "test_command": ""},
        ]
        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        side_effect=fake_brain):
            result = coding_workflow.run_task_waves(
                plan, "goal", self.temp_dir)
        self.assertTrue(result["ok"])
        self.assertEqual("coder", result["results"]["a"]["agent"])
        self.assertIn("first", seen[0])
        self.assertIn("coder output", seen[1])

    def test_test_tasks_go_to_the_test_writer(self):
        from assistant import coding_workflow

        def fake_brain(history, model=None, tools=None):
            return _reply("wrote tests")

        plan = [{"id": "t", "file": "tests/test_a.py", "action": "test",
                 "description": "test it", "needs": [], "test_command": ""}]
        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        side_effect=fake_brain):
            result = coding_workflow.run_task_waves(
                plan, "goal", self.temp_dir)
        self.assertEqual("test_writer", result["results"]["t"]["agent"])

    def test_failed_dependency_skips_the_dependent(self):
        from assistant import coding_workflow

        def fake_brain(history, model=None, tools=None):
            task = [m["content"] for m in history if m["role"] == "user"][-1]
            if "FAILME" in task:
                return _reply("Agent failed.")
            return _reply("done")

        plan = [
            {"id": "a", "file": "a.py", "action": "create",
             "description": "FAILME", "needs": [], "test_command": ""},
            {"id": "b", "file": "b.py", "action": "create",
             "description": "after", "needs": ["a"], "test_command": ""},
        ]
        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        side_effect=fake_brain):
            result = coding_workflow.run_task_waves(
                plan, "goal", self.temp_dir)
        self.assertFalse(result["ok"])
        self.assertIn("Skipped", result["results"]["b"]["output"])

    def test_retry_set_is_failures_plus_dependents(self):
        from assistant.coding_workflow import retry_ids_for
        plan = [
            {"id": "a", "file": "a.py", "needs": []},
            {"id": "b", "file": "b.py", "needs": ["a"]},
            {"id": "c", "file": "c.py", "needs": ["b"]},
            {"id": "d", "file": "d.py", "needs": []},
        ]
        prior = {
            "a": {"ok": False, "output": "broken"},
            "b": {"ok": False, "output": "Skipped"},
            "c": {"ok": False, "output": "Skipped"},
            "d": {"ok": True, "output": "fine"},
        }
        self.assertEqual({"a", "b", "c"}, retry_ids_for(plan, prior))

    def test_a_later_wave_leaves_successful_tasks_alone(self):
        from assistant import coding_workflow
        calls = []

        def fake_brain(history, model=None, tools=None):
            task = [m["content"] for m in history if m["role"] == "user"][-1]
            calls.append(task)
            return _reply("done")

        plan = [
            {"id": "a", "file": "a.py", "action": "create",
             "description": "keep me", "needs": [], "test_command": ""},
            {"id": "b", "file": "b.py", "action": "create",
             "description": "retry me", "needs": [], "test_command": ""},
            {"id": "c", "file": "c.py", "action": "create",
             "description": "downstream", "needs": ["b"], "test_command": ""},
        ]
        prior = {
            "a": {"ok": True, "agent": "coder", "output": "done"},
            "b": {"ok": False, "agent": "coder", "output": "Agent failed."},
            "c": {"ok": False, "agent": "coder", "output": "Skipped"},
        }
        retry = coding_workflow.retry_ids_for(plan, prior)
        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        side_effect=fake_brain):
            result = coding_workflow.run_task_waves(
                plan, "goal", self.temp_dir, prior_results=prior,
                retry_ids=retry)
        self.assertTrue(result["ok"])
        self.assertEqual(2, len(calls))
        self.assertFalse(any("keep me" in call for call in calls))
        self.assertTrue(any("retry me" in call for call in calls))
        self.assertTrue(any("downstream" in call for call in calls))


class WorkflowLoopTests(VaveTestCase):
    def _scripted_agents(self, plan, verdicts=None):
        verdicts = list(verdicts or ["APPROVED. Good."])
        state = {"reviews": 0}

        def fake(role_prompt, task, **kwargs):
            agent_id = kwargs.get("agent_id", "")
            if agent_id.startswith("planner"):
                return json.dumps(plan)
            if agent_id.startswith("reviewer"):
                index = min(state["reviews"], len(verdicts) - 1)
                state["reviews"] += 1
                return verdicts[index]
            if agent_id.startswith("fixer"):
                return "fixed the bug"
            return f"done {agent_id}"

        return fake, state

    def test_successful_run_needs_one_cycle(self):
        from assistant import coding_workflow
        plan = [{"id": "a", "file": "app.py", "action": "create",
                 "description": "build", "needs": [], "test_command": ""}]
        fake, _state = self._scripted_agents(plan)
        passing = {"command": "tests", "passed": True, "exit_code": 0,
                   "output": "ok", "timed_out": False, "blocked": ""}
        with mock.patch("assistant.coding_workflow.run_sub_agent",
                        side_effect=fake), \
             mock.patch("assistant.coding_workflow.detect_test_command",
                        return_value=""), \
             mock.patch("assistant.coding_workflow.run_test_command",
                        return_value=passing):
            result = coding_workflow.run_workflow(
                "build the app", self.temp_dir, test_command="tests")
        self.assertTrue(result["ok"])
        self.assertEqual(1, result["cycles"])
        self.assertIn("SUCCESS", result["report"])
        self.assertIn("coder", result["report"])

    def test_failure_is_fixed_then_verified(self):
        from assistant import coding_workflow
        plan = [{"id": "a", "file": "app.py", "action": "create",
                 "description": "build", "needs": [], "test_command": ""}]
        fake, _state = self._scripted_agents(plan)
        results = [
            {"command": "tests", "passed": False, "exit_code": 1,
             "output": "AssertionError: 1 != 2", "timed_out": False,
             "blocked": ""},
            {"command": "tests", "passed": True, "exit_code": 0,
             "output": "ok", "timed_out": False, "blocked": ""},
        ]
        calls = {"n": 0}

        def fake_test(command, cwd=".", timeout=None):
            index = min(calls["n"], len(results) - 1)
            calls["n"] += 1
            return results[index]

        with mock.patch("assistant.coding_workflow.run_sub_agent",
                        side_effect=fake), \
             mock.patch("assistant.coding_workflow.detect_test_command",
                        return_value=""), \
             mock.patch("assistant.coding_workflow.run_test_command",
                        side_effect=fake_test):
            result = coding_workflow.run_workflow(
                "build the app", self.temp_dir, test_command="tests")
        self.assertTrue(result["ok"])
        self.assertEqual(2, calls["n"])
        fixes = result["artifacts"]["cycles"][0]["fixes"]
        self.assertEqual("fixed the bug", fixes[0]["output"])

    def test_reviewer_rejection_drives_the_fixer(self):
        from assistant import coding_workflow
        plan = [{"id": "a", "file": "app.py", "action": "create",
                 "description": "build", "needs": [], "test_command": ""}]
        fake, state = self._scripted_agents(
            plan, verdicts=["REJECTED: no error handling", "APPROVED."])
        passing = {"command": "tests", "passed": True, "exit_code": 0,
                   "output": "ok", "timed_out": False, "blocked": ""}
        with mock.patch("assistant.coding_workflow.run_sub_agent",
                        side_effect=fake), \
             mock.patch("assistant.coding_workflow.detect_test_command",
                        return_value=""), \
             mock.patch("assistant.coding_workflow.run_test_command",
                        return_value=passing):
            result = coding_workflow.run_workflow(
                "build the app", self.temp_dir, test_command="tests")
        self.assertTrue(result["ok"])
        self.assertEqual(2, state["reviews"])
        self.assertEqual(1, len(result["artifacts"]["cycles"][0]["fixes"]))

    def test_no_plan_fails_and_records_a_lesson(self):
        from assistant import coding_workflow
        with mock.patch("assistant.coding_workflow.run_sub_agent",
                        return_value="not a plan"):
            result = coding_workflow.run_workflow(
                "build the app", self.temp_dir)
        self.assertFalse(result["ok"])
        self.assertIn("Planner", result["report"])
        lessons = coding_workflow.load_lessons()
        self.assertTrue(any("Planner" in str(item) for item in lessons))

    def test_missing_project_path_fails_fast(self):
        from assistant import coding_workflow
        missing = os.path.join(self.temp_dir, "does_not_exist")
        result = coding_workflow.run_workflow("build", missing)
        self.assertFalse(result["ok"])
        self.assertIn("does not exist", result["report"])

    def test_tool_entry_point_returns_text(self):
        from assistant import coding_workflow
        with mock.patch("assistant.coding_workflow.run_workflow",
                        return_value={"ok": True, "report": "all good"}):
            text = coding_workflow.run_coding_workflow("build it", ".")
        self.assertEqual("all good", text)


class LessonsTests(VaveTestCase):
    def test_round_trip_and_redaction(self):
        from assistant import coding_workflow
        coding_workflow.record_lesson({
            "ok": False, "goal": "x", "api_key": "sk-super-secret",
            "failures": ["boom"]})
        text = coding_workflow.lessons_path().read_text(encoding="utf-8")
        self.assertNotIn("sk-super-secret", text)
        lessons = coding_workflow.load_lessons()
        self.assertEqual(1, len(lessons))
        self.assertEqual("boom", lessons[0]["failures"][0])

    def test_journal_is_capped(self):
        from assistant import coding_workflow
        for index in range(205):
            coding_workflow.record_lesson({"ok": True, "goal": str(index)})
        self.assertEqual(200, len(coding_workflow.load_lessons(limit=500)))

    def test_stats_count_runs_and_failures(self):
        from assistant import coding_workflow
        coding_workflow.record_lesson({"ok": True, "goal": "a"})
        coding_workflow.record_lesson(
            {"ok": False, "goal": "b", "failures": ["boom"]})
        stats = coding_workflow.workflow_stats()
        self.assertEqual(2, stats["runs"])
        self.assertEqual(1, stats["ok"])
        self.assertEqual(1, stats["failed"])
        self.assertEqual("boom", stats["top_failures"][0]["reason"])

    def test_registry_lists_agents_and_apps(self):
        from assistant.coding_workflow import (list_coding_agents,
                                                registered_agents,
                                                registered_apps)
        self.assertIn("planner", registered_agents())
        self.assertIn("coder", registered_agents())
        self.assertIn("test_writer", registered_agents())
        self.assertIn("fixer", registered_agents())
        self.assertIn("reviewer", registered_agents())
        self.assertIn("git_auto_commit_and_push", registered_apps())
        text = list_coding_agents()
        self.assertIn("planner", text)
        self.assertIn("scaffold_code", text)


class RegistrationTests(VaveTestCase):
    def test_tools_registered_with_capabilities_and_schemas(self):
        from assistant.ai_brain import (AVAILABLE_FUNCTIONS, LLM_TOOLS,
                                        select_tools)
        from assistant.control.capabilities import TOOL_CAPABILITIES
        names = ("run_coding_workflow", "list_coding_agents",
                 "coding_workflow_stats")
        for name in names:
            with self.subTest(tool=name):
                self.assertIn(name, AVAILABLE_FUNCTIONS)
                self.assertIn(name, TOOL_CAPABILITIES)
                self.assertTrue(any(item["function"]["name"] == name
                                    for item in LLM_TOOLS))
        offered = [item["function"]["name"] for item in
                   select_tools("implement a feature in this project")]
        self.assertIn("run_coding_workflow", offered)

    def test_workflow_capability_is_high_risk(self):
        from assistant import guard
        from assistant.control.capabilities import capability_for_tool, risk_for
        capability = capability_for_tool("run_coding_workflow")
        self.assertEqual("coding.write", capability)
        self.assertEqual("high", risk_for(capability).value)
        self.assertEqual("destructive", guard.tier_of("run_coding_workflow"))
        self.assertEqual("destructive", guard.tier_of("run_opencode_task"))

    def test_voice_patterns_route_coding_requests(self):
        from assistant.commands import (_CODING_AGENTS_PATTERN,
                                        _CODING_STATS_PATTERN,
                                        _CODING_WORKFLOW_PATTERN)
        self.assertTrue(_CODING_WORKFLOW_PATTERN.match("implement login"))
        self.assertTrue(_CODING_WORKFLOW_PATTERN.match("fix tests"))
        self.assertTrue(_CODING_AGENTS_PATTERN.match(
            "list coding agents"))
        self.assertTrue(_CODING_STATS_PATTERN.match(
            "coding workflow stats"))


if __name__ == "__main__":
    unittest.main()

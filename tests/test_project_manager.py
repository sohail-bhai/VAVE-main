"""Unit tests for VAVE Project Manager (Phase 2)."""

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from assistant.project_manager.manager import (
    ProjectManager,
    manage_coding_project,
)
from assistant.project_manager.spec import (
    EvaluationResult,
    ManagerTask,
    VisionSpec,
)


class TestProjectManagerSpec(unittest.TestCase):
    def test_vision_spec_serialization(self):
        spec = VisionSpec(
            goal="Build a calculator",
            summary="CLI calculator in Python",
            acceptance_criteria=["Must add", "Must subtract"],
            test_command="pytest tests/test_calc.py",
            tasks=[
                ManagerTask(
                    id="task_calc",
                    engine="vave_swarm",
                    prompt="Write calc.py",
                    target_files=["calc.py"],
                )
            ],
        )
        data = spec.to_dict()
        self.assertEqual(data["goal"], "Build a calculator")
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["engine"], "vave_swarm")

        rebuilt = VisionSpec.from_dict(data)
        self.assertEqual(rebuilt.goal, spec.goal)
        self.assertEqual(len(rebuilt.tasks), 1)
        self.assertEqual(rebuilt.tasks[0].id, "task_calc")


class TestProjectManager(unittest.TestCase):
    @patch("assistant.ai_brain.query_local_llm_chat")
    def test_real_model_message_is_unwrapped_and_tool_free(self, mock_query):
        mock_query.return_value = {"role": "assistant", "content": '{"ok": true}'}
        pm = ProjectManager()
        result = pm._query_model("prompt", system_prompt="system")
        self.assertEqual('{"ok": true}', result)
        self.assertIs(False, mock_query.call_args.kwargs["tools"])

    def test_synthesize_spec_with_clean_json(self):
        sample_json = json.dumps({
            "summary": "Build REST API",
            "acceptance_criteria": ["Return 200 OK", "Handle 404"],
            "test_command": "pytest",
            "tasks": [
                {
                    "id": "t1",
                    "engine": "opencode",
                    "prompt": "Scaffold FastAPI app",
                    "target_files": ["main.py"],
                }
            ],
        })

        mock_llm = MagicMock(return_value=sample_json)
        pm = ProjectManager(llm_caller=mock_llm)
        spec = pm.synthesize_spec("Create REST API")

        self.assertEqual(spec.goal, "Create REST API")
        self.assertEqual(spec.summary, "Build REST API")
        self.assertEqual(len(spec.tasks), 1)
        self.assertEqual(spec.tasks[0].engine, "opencode")

    def test_synthesize_spec_with_markdown_fences(self):
        sample_output = """Here is the spec:
```json
{
  "summary": "Build CLI tool",
  "acceptance_criteria": ["Has --help"],
  "test_command": "python -m unittest",
  "tasks": [
    {
      "id": "t1",
      "engine": "vave_swarm",
      "prompt": "Create cli.py",
      "target_files": ["cli.py"]
    }
  ]
}
```
Good luck!
"""
        mock_llm = MagicMock(return_value=sample_output)
        pm = ProjectManager(llm_caller=mock_llm)
        spec = pm.synthesize_spec("Build CLI")
        self.assertEqual(spec.summary, "Build CLI tool")
        self.assertEqual(len(spec.tasks), 1)

    def test_synthesize_spec_fallback_on_malformed_llm(self):
        mock_llm = MagicMock(return_value="Not valid json at all")
        pm = ProjectManager(llm_caller=mock_llm)
        spec = pm.synthesize_spec("Fix database bug")

        self.assertEqual(spec.goal, "Fix database bug")
        self.assertEqual(len(spec.tasks), 1)
        self.assertEqual(spec.tasks[0].engine, "vave_swarm")
        self.assertEqual(spec.tasks[0].prompt, "Fix database bug")

    @patch("assistant.integrations.antigravity.is_antigravity_available", return_value=True)
    @patch("assistant.integrations.antigravity.run_antigravity_task", return_value={"ok": True, "output": "Antigravity task completed."})
    def test_delegate_task_antigravity(self, mock_run, mock_avail):
        pm = ProjectManager(llm_caller=MagicMock())
        task = ManagerTask(id="t_agy", engine="antigravity", prompt="Write calc.py with tests")
        res = pm.delegate_task(task, project_path=".")
        self.assertIn("Antigravity task completed", res)
        mock_run.assert_called_once()

    @patch("assistant.integrations.opencode.is_opencode_available", return_value=True)
    @patch("assistant.integrations.opencode.run_opencode", return_value={"ok": True, "output": "OpenCode generated app."})
    def test_delegate_task_opencode(self, mock_run, mock_avail):
        pm = ProjectManager(llm_caller=MagicMock())
        task = ManagerTask(id="t1", engine="opencode", prompt="Create boilerplate")
        res = pm.delegate_task(task, project_path=".")
        self.assertIn("OpenCode generated app", res)
        mock_run.assert_called_once()

    @patch(
        "assistant.coding_workflow.run_workflow",
        return_value={"ok": True, "report": "Swarm completed simple task."},
    )
    @patch("assistant.integrations.opencode.run_opencode")
    def test_delegate_task_opencode_diverts_to_swarm_for_simple_task(self, mock_opencode, mock_cw):
        pm = ProjectManager(llm_caller=MagicMock())
        task = ManagerTask(id="t_simple", engine="opencode", prompt="Fix typo in readme header")
        res = pm.delegate_task(task, project_path=".")
        self.assertEqual(res, "Swarm completed simple task.")
        mock_opencode.assert_not_called()
        mock_cw.assert_called_once()

    @patch("assistant.integrations.openclaw.is_openclaw_available", return_value=True)
    @patch("assistant.integrations.openclaw.run_openclaw", return_value={"ok": True, "output": "OpenClaw agent executed."})
    def test_delegate_task_openclaw(self, mock_run, mock_avail):
        pm = ProjectManager(llm_caller=MagicMock())
        task = ManagerTask(id="t1", engine="openclaw", prompt="Scrape documentation")
        res = pm.delegate_task(task, project_path=".")
        self.assertIn("OpenClaw agent executed", res)
        mock_run.assert_called_once()

    @patch(
        "assistant.coding_workflow.run_workflow",
        return_value={"ok": True, "report": "Coding workflow completed."},
    )
    def test_delegate_task_vave_swarm_with_critique(self, mock_cw):
        pm = ProjectManager(llm_caller=MagicMock())
        task = ManagerTask(id="t1", engine="vave_swarm", prompt="Implement feature")
        res = pm.delegate_task(task, project_path=".", extra_context="Tests failed: missing import")
        self.assertEqual(res, "Coding workflow completed.")
        called_prompt = mock_cw.call_args[1]["goal"]
        self.assertIn("[MANAGER CRITIQUE FROM PREVIOUS CYCLE]", called_prompt)
        self.assertIn("missing import", called_prompt)

    @patch("assistant.integrations.opencode.is_opencode_available", return_value=True)
    @patch(
        "assistant.integrations.opencode.run_opencode",
        return_value={"ok": False, "output": "", "error": "engine crashed"},
    )
    def test_delegate_failure_is_recorded(self, mock_run, mock_avail):
        pm = ProjectManager(llm_caller=MagicMock())
        task = ManagerTask(
            id="t_failed",
            engine="opencode",
            prompt="Complex full stack architecture task",
        )
        output = pm.delegate_task(task)
        self.assertEqual("failed", task.status)
        self.assertIn("engine crashed", output)

    def test_invalid_reviewer_output_withholds_approval(self):
        pm = ProjectManager(
            llm_caller=MagicMock(return_value="not valid reviewer json"),
            test_executor=MagicMock(
                return_value={"ok": True, "output": "tests passed", "exit_code": 0}
            ),
        )
        result = pm.evaluate(VisionSpec(goal="Test", tasks=[]), project_path=".")
        self.assertFalse(result.approved)
        self.assertIn("approval withheld", result.remarks)

    def test_string_false_does_not_become_approval(self):
        pm = ProjectManager(
            llm_caller=MagicMock(
                return_value=json.dumps(
                    {"approved": "false", "remarks": "not done", "missing_criteria": []}
                )
            ),
            test_executor=MagicMock(
                return_value={"ok": True, "output": "tests passed", "exit_code": 0}
            ),
        )
        result = pm.evaluate(VisionSpec(goal="Test", tasks=[]), project_path=".")
        self.assertFalse(result.approved)

    def test_default_test_executor_blocks_destructive_shell(self):
        pm = ProjectManager()
        result = pm._default_test_executor("rm -rf /", ".")
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])

    def test_target_path_cannot_escape_project(self):
        pm = ProjectManager(llm_caller=MagicMock())
        with patch.object(pm, "synthesize_spec") as synthesize:
            synthesize.return_value = VisionSpec(
                goal="bad target",
                tasks=[
                    ManagerTask(
                        id="escape",
                        engine="vave_swarm",
                        prompt="write outside",
                        target_files=["../outside.py"],
                    )
                ],
            )
            with patch.object(pm, "delegate_task") as delegate:
                result = pm.run_managed_project("bad target", max_loops=1)
        delegate.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("escapes project root", result["history"][0]["tasks"][0]["output"])

    def test_emergency_stop_prevents_delegation(self):
        pm = ProjectManager(llm_caller=MagicMock())
        spec = VisionSpec(
            goal="stopped",
            tasks=[ManagerTask(id="never_runs", engine="vave_swarm", prompt="work")],
        )
        with patch.object(pm, "synthesize_spec", return_value=spec), patch.object(
            pm, "delegate_task"
        ) as delegate, patch(
            "assistant.project_manager.manager._stop_requested",
            return_value=True,
        ):
            result = pm.run_managed_project("stopped")
        delegate.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("emergency stop", result["summary"])

    def test_managed_projects_are_process_exclusive(self):
        from assistant.project_manager import manager as manager_module

        self.assertTrue(manager_module._MANAGER_LOCK.acquire(blocking=False))
        try:
            result = ProjectManager(llm_caller=MagicMock()).run_managed_project(
                "second project"
            )
        finally:
            manager_module._MANAGER_LOCK.release()
        self.assertFalse(result["ok"])
        self.assertTrue(result["busy"])
        self.assertIn("already running", result["summary"])

    def test_evaluate_test_failure(self):
        pm = ProjectManager(
            test_executor=MagicMock(return_value={"ok": False, "output": "AssertionError in test_foo", "exit_code": 1})
        )
        spec = VisionSpec(goal="Test", test_command="pytest", tasks=[])
        res = pm.evaluate(spec, project_path=".")
        self.assertFalse(res.approved)
        self.assertFalse(res.test_passed)
        self.assertIn("AssertionError", res.remarks)

    def test_run_managed_project_loopback(self):
        # Cycle 1: test fails. Cycle 2: test passes and LLM approves.
        call_count = {"test": 0}

        def mock_test_exec(cmd, cwd):
            call_count["test"] += 1
            if call_count["test"] == 1:
                return {"ok": False, "output": "Test failed on cycle 1", "exit_code": 1}
            return {"ok": True, "output": "All 5 tests passed", "exit_code": 0}

        def mock_llm(messages, model=""):
            content = messages[-1]["content"]
            if "Project Goal:" in content:
                # Synthesis
                return json.dumps({
                    "summary": "Implement feature X",
                    "acceptance_criteria": ["Pass tests"],
                    "test_command": "pytest",
                    "tasks": [{"id": "t1", "engine": "vave_swarm", "prompt": "Code feature X"}],
                })
            # Critique
            return json.dumps({
                "approved": True,
                "remarks": "Clean implementation.",
                "missing_criteria": [],
            })

        pm = ProjectManager(llm_caller=mock_llm, test_executor=mock_test_exec)
        with patch.object(pm, "delegate_task", return_value="Task output"):
            res = pm.run_managed_project("Goal X", max_loops=3)

        self.assertTrue(res["ok"])
        self.assertEqual(res["cycles_run"], 2)
        self.assertEqual(len(res["history"]), 2)
        self.assertFalse(res["history"][0]["evaluation"]["approved"])
        self.assertTrue(res["history"][1]["evaluation"]["approved"])

    def test_manage_coding_project_wrapper(self):
        with patch.object(ProjectManager, "run_managed_project", return_value={"ok": True, "summary": "Done"}):
            raw = manage_coding_project("Test Goal")
            parsed = json.loads(raw)
            self.assertTrue(parsed["ok"])
            self.assertEqual(parsed["summary"], "Done")


if __name__ == "__main__":
    unittest.main()

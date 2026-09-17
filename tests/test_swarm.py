"""Tests for Track 3: swarm DAG, roles, and the generalized actor-critic.

No model, no network: a fake query_local_llm_chat answers from the task text
itself, so thread order cannot change the outcome. The real run_sub_agent,
guard calls and event flow all execute.
"""

import threading
import unittest
from unittest import mock


def _says(text):
    return {"role": "assistant", "content": text}


def _fake_brain_factory():
    """A model stand-in: each reply is derived from the task it was given."""
    def fake(history, model=None):
        user_texts = [str(m.get("content", "")) for m in history
                      if m.get("role") == "user"]
        task = user_texts[-1] if user_texts else ""
        if "FAILME" in task:
            return _says("Agent failed.")
        if "YOUR TASK:" in task:
            marker = task.split("YOUR TASK:", 1)[1].strip().split("\n")[0]
            if "Completed dependencies:" in task:
                return _says(f"saw deps, did {marker}; deps were: {task}")
            return _says(f"did {marker}")
        return _says("done")
    return fake


class RoleTests(unittest.TestCase):
    def test_known_roles_expand(self):
        from assistant.swarm import role_prompt_for
        self.assertIn("research", role_prompt_for("Researcher").lower())
        self.assertIn("plan", role_prompt_for("Planner").lower())
        self.assertIn("test", role_prompt_for("Coder").lower())
        self.assertIn("APPROVED", role_prompt_for("Verifier"))

    def test_unknown_role_passes_through(self):
        from assistant.swarm import role_prompt_for
        self.assertEqual("Be a pirate.", role_prompt_for("Be a pirate."))

    def test_run_sub_agent_resolves_role_names(self):
        from assistant import swarm
        seen = {}

        def fake(history, model=None):
            seen["system"] = history[0]["content"]
            return _says("done")

        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        side_effect=fake):
            swarm.run_sub_agent("Researcher", "find things")
        self.assertIn("research", seen["system"].lower())


class DagValidationTests(unittest.TestCase):
    def test_levels_order_dependencies_first(self):
        from assistant.swarm import topological_levels
        levels, error = topological_levels([
            {"id": "c", "needs": ["a", "b"]},
            {"id": "a", "needs": []},
            {"id": "b", "needs": ["a"]},
        ])
        self.assertEqual("", error)
        self.assertEqual([["a"], ["b"], ["c"]], levels)

    def test_independent_nodes_share_a_wave(self):
        from assistant.swarm import topological_levels
        levels, error = topological_levels([
            {"id": "a", "needs": []},
            {"id": "b", "needs": []},
        ])
        self.assertEqual("", error)
        self.assertEqual([["a", "b"]], levels)

    def test_unknown_dependency_is_an_error(self):
        from assistant.swarm import topological_levels
        _levels, error = topological_levels([{"id": "a", "needs": ["ghost"]}])
        self.assertIn("ghost", error)

    def test_cycle_is_an_error(self):
        from assistant.swarm import topological_levels
        _levels, error = topological_levels([
            {"id": "a", "needs": ["b"]},
            {"id": "b", "needs": ["a"]},
        ])
        self.assertIn("Circular", error)

    def test_duplicate_and_empty_ids_are_errors(self):
        from assistant.swarm import topological_levels
        _levels, error = topological_levels([{"id": "a"}, {"id": "a"}])
        self.assertIn("Duplicate", error)
        _levels, error = topological_levels([{"task": "no id"}])
        self.assertIn("'id'", error)

    def test_empty_graph_is_an_error(self):
        from assistant.swarm import topological_levels
        _levels, error = topological_levels([])
        self.assertTrue(error)


class DagRunTests(unittest.TestCase):
    def test_dependents_see_dependency_outputs(self):
        from assistant import swarm
        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        side_effect=_fake_brain_factory()):
            result = swarm.run_swarm_dag([
                {"id": "fetch", "role": "Researcher", "task": "fetch stuff"},
                {"id": "write", "role": "Coder", "task": "write stuff",
                 "needs": ["fetch"]},
            ])
        self.assertTrue(result["ok"])
        self.assertTrue(result["results"]["fetch"]["ok"])
        write_output = result["results"]["write"]["output"]
        self.assertIn("did fetch stuff", write_output)
        self.assertIn("write stuff", write_output)

    def test_failed_dependency_skips_dependents(self):
        from assistant import swarm
        calls = []
        real_fake = _fake_brain_factory()

        def counting_fake(history, model=None):
            user_texts = [str(m.get("content", "")) for m in history
                          if m.get("role") == "user"]
            calls.append(user_texts[-1] if user_texts else "")
            return real_fake(history, model)

        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        side_effect=counting_fake):
            result = swarm.run_swarm_dag([
                {"id": "boom", "role": "Coder", "task": "FAILME now"},
                {"id": "after", "role": "Coder", "task": "after boom",
                 "needs": ["boom"]},
            ])
        self.assertFalse(result["ok"])
        self.assertFalse(result["results"]["boom"]["ok"])
        self.assertFalse(result["results"]["after"]["ok"])
        self.assertIn("Skipped", result["results"]["after"]["output"])
        # The skipped node never reached the model.
        self.assertFalse(any("after boom" in call for call in calls))

    def test_bad_graph_reports_without_running(self):
        from assistant import swarm
        with mock.patch("assistant.ai_brain.query_local_llm_chat") as mock_q:
            result = swarm.run_swarm_dag([{"id": "a", "needs": ["ghost"]}])
        self.assertFalse(result["ok"])
        self.assertIn("ghost", result["report"])
        mock_q.assert_not_called()

    def test_events_flow_when_bus_given(self):
        from assistant import events, swarm
        bus = events.EventBus()
        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        side_effect=_fake_brain_factory()):
            swarm.run_swarm_dag(
                [{"id": "solo", "role": "Coder", "task": "solo work"}],
                event_bus=bus)
        messages = [event.message for event in bus.drain()]
        self.assertTrue(any("started" in message for message in messages))
        self.assertTrue(any("finished" in message for message in messages))


class ActorCriticTests(unittest.TestCase):
    def _judge_brain(self, verdicts):
        """Actor echoes the goal; critic returns scripted verdicts."""
        state = {"critic_calls": 0}

        def fake(history, model=None):
            user_texts = [str(m.get("content", "")) for m in history
                          if m.get("role") == "user"]
            task = user_texts[-1] if user_texts else ""
            if "Attempt to judge:" in task:
                index = state["critic_calls"]
                state["critic_calls"] += 1
                return _says(verdicts[min(index, len(verdicts) - 1)])
            return _says(f"draft for {task[:40]}")

        return fake

    def test_approval_on_second_verdict(self):
        from assistant import swarm
        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        side_effect=self._judge_brain(
                            ["REJECTED: thin", "APPROVED. Good now."])):
            result = swarm.run_actor_critic("write it", max_iterations=3)
        self.assertTrue(result["approved"])
        self.assertEqual(2, result["iterations"])
        self.assertEqual(2, len(result["verdicts"]))

    def test_cap_is_honored(self):
        from assistant import swarm
        with mock.patch("assistant.ai_brain.query_local_llm_chat",
                        side_effect=self._judge_brain(["REJECTED: thin"])):
            result = swarm.run_actor_critic("write it", max_iterations=2)
        self.assertFalse(result["approved"])
        self.assertEqual(2, result["iterations"])

    def test_verdict_rule_needs_clean_approval(self):
        from assistant.swarm import _verdict_approved
        self.assertTrue(_verdict_approved("APPROVED. Nice."))
        self.assertFalse(_verdict_approved("REJECTED: bad"))
        self.assertFalse(_verdict_approved("APPROVED but also REJECTED"))
        self.assertFalse(_verdict_approved(""))


class RegistrationTests(unittest.TestCase):
    def test_new_tools_registered_schema_capability_and_triggers(self):
        from assistant.ai_brain import (AVAILABLE_FUNCTIONS, LLM_TOOLS,
                                        select_tools)
        from assistant.control.capabilities import TOOL_CAPABILITIES
        for name in ("run_swarm_dag", "run_actor_critic"):
            with self.subTest(tool=name):
                self.assertIn(name, AVAILABLE_FUNCTIONS)
                self.assertIn(name, TOOL_CAPABILITIES)
                self.assertTrue(any(item["function"]["name"] == name
                                    for item in LLM_TOOLS))
        offered = [item["function"]["name"] for item in
                   select_tools("delegate this multi-stage goal to a swarm")]
        self.assertIn("run_swarm_dag", offered)


if __name__ == "__main__":
    unittest.main()

"""Tests verifying that the agent is never starved of core desktop primitives,
and that tool registries (AVAILABLE_FUNCTIONS, LLM_TOOLS, guard, capabilities)
remain strictly consistent.
"""

import unittest
from unittest import mock

from assistant.ai_brain import select_tools, AVAILABLE_FUNCTIONS, LLM_TOOLS
from assistant import guard
from assistant.control.capabilities import TOOL_CAPABILITIES
from tests.support import VaveTestCase


class PrimitivesConsistencyTests(VaveTestCase):
    """Tests for primitive availability and tool registry consistency."""

    def test_core_desktop_primitives_always_offered_for_desktop_requests(self):
        core_primitives = {
            "list_windows", "get_clickable_elements", "read_screen",
            "focus_window", "click_element", "click_at", "type_text",
            "press_key", "scroll", "wait"
        }

        test_prompts = [
            "scroll down in the file explorer window",
            "open notepad and type hello",
            "click the save button in notepad",
            "look at the active window and scroll down",
            "switch to chrome and click download",
        ]

        for prompt in test_prompts:
            with self.subTest(prompt=prompt):
                tools = select_tools(prompt)
                offered_names = {t["function"]["name"] for t in tools}
                for prim in core_primitives:
                    self.assertIn(prim, offered_names,
                                  f"Primitive '{prim}' was starved/evicted for prompt: '{prompt}'")

    def test_scroll_offered_for_scroll_request(self):
        tools = select_tools("scroll down in the file explorer window")
        offered_names = [t["function"]["name"] for t in tools]
        self.assertIn("scroll", offered_names)

    def test_available_functions_and_llm_tools_are_synchronized(self):
        tool_schema_names = {t["function"]["name"] for t in LLM_TOOLS}
        available_names = set(AVAILABLE_FUNCTIONS.keys())

        # Every available function exposed to the model must have a schema
        missing_schemas = [name for name in available_names if name not in tool_schema_names]
        self.assertEqual(missing_schemas, [])

    def test_all_available_functions_are_classified_in_guard(self):
        classified = guard._REGISTRY["safe"] | guard._REGISTRY["sensitive"] | guard._REGISTRY["destructive"]
        unclassified = [name for name in AVAILABLE_FUNCTIONS if name not in classified]
        self.assertEqual(unclassified, [])

    def test_desktop_input_primitives_have_tool_capabilities(self):
        critical_input_tools = [
            "click_element", "click_at", "double_click_at", "right_click_at",
            "move_mouse", "type_text", "press_key", "scroll", "drag_and_drop",
            "find_and_click_text"
        ]
        for tool in critical_input_tools:
            with self.subTest(tool=tool):
                self.assertIn(tool, TOOL_CAPABILITIES,
                              f"Tool '{tool}' is missing from TOOL_CAPABILITIES")
                self.assertEqual(TOOL_CAPABILITIES[tool], "system.input.control")

    @mock.patch("assistant.vision.find_text_on_screen", return_value=(400, 300))
    @mock.patch("assistant.system_tasks.click_at", return_value="Clicked at (400, 300).")
    @mock.patch("assistant.system_tasks._find_window")
    def test_click_element_falls_back_to_ocr_when_uia_misses(self, mock_find, mock_click_at, mock_ocr):
        from assistant.system_tasks import click_element

        # Mock a window with no matching controls
        mock_win = mock.MagicMock()
        mock_win.Name = "Test Window"
        mock_win.NativeWindowHandle = None
        mock_find.return_value = mock_win

        with mock.patch("assistant.system_tasks._ensure_com"), \
             mock.patch("uiautomation.WalkControl", return_value=[]):
            result = click_element("Submit", window_title="Test Window")

        mock_ocr.assert_called_once_with("Submit")
        mock_click_at.assert_called_once_with(400, 300)
        self.assertEqual(result, "Clicked at (400, 300).")

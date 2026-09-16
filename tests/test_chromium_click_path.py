"""Regression tests for D8: the Chromium/PWA click path.

What broke live: a Netflix (Edge/Chromium) window exposed only browser chrome
to UI Automation, the model clicked Edge's own toolbar button named
"Profile 1 Profile" at (1150,35), InvokePattern reported success while doing
nothing, and the stall redirect fired once then went silent for five more
repeats. These tests pin each fix. Everything is mocked: no windows, no
mouse, no network.
"""

import ctypes
import sys
import unittest
from unittest import mock

# Eager import: mock.patch.dict(sys.modules, ...) snapshots sys.modules on
# entry and restores it on exit, deleting everything imported in between. If
# assistant.vision (pytesseract -> numpy, whose C extension cannot initialize
# twice) were first imported inside a patched region, every later
# `from assistant.vision import ...` would explode. Importing here pins the
# whole chain before any snapshot is taken.
import assistant.vision  # noqa: F401


def _auto_with(windows):
    auto = mock.MagicMock()
    root = mock.MagicMock()
    root.GetChildren.return_value = windows
    auto.GetRootControl.return_value = root
    return auto


def _window(auto, name, cls, pid=4242, handle=7777):
    w = mock.MagicMock()
    w.ControlType = auto.ControlType.WindowControl
    w.ProcessId = pid
    w.Name = name
    w.ClassName = cls
    w.NativeWindowHandle = handle
    return w


class WindowClassificationTests(unittest.TestCase):
    def test_chromium_window_detected(self):
        from assistant.system_tasks import _is_chromium_window
        w = mock.MagicMock(ClassName="Chrome_WidgetWin_1")
        self.assertTrue(_is_chromium_window(w))
        w2 = mock.MagicMock(ClassName="Notepad")
        self.assertFalse(_is_chromium_window(w2))

    def test_console_window_detected(self):
        from assistant.system_tasks import _is_console_window
        w = mock.MagicMock(ClassName="CASCADIA_HOSTING_WINDOW_CLASS")
        self.assertTrue(_is_console_window(w))
        w2 = mock.MagicMock(ClassName="Chrome_WidgetWin_1")
        self.assertFalse(_is_console_window(w2))

    def test_find_window_ignores_console_for_app_queries(self):
        from assistant import system_tasks
        auto = mock.MagicMock()
        console = _window(auto, "C:\\Windows\\system32\\cmd.exe - probe_click.py Netflix",
                          "CASCADIA_HOSTING_WINDOW_CLASS")
        real = _window(auto, "Netflix", "Chrome_WidgetWin_1")
        auto.GetRootControl.return_value.GetChildren.return_value = [console, real]
        with mock.patch.dict(sys.modules, {"uiautomation": auto}):
            with mock.patch("assistant.system_tasks._ensure_com"):
                found = system_tasks._find_window("Netflix")
        self.assertIs(found, real)

    def test_find_window_still_finds_consoles_by_alias(self):
        from assistant import system_tasks
        auto = mock.MagicMock()
        console = _window(auto, "Command Prompt", "CASCADIA_HOSTING_WINDOW_CLASS")
        auto.GetRootControl.return_value.GetChildren.return_value = [console]
        with mock.patch.dict(sys.modules, {"uiautomation": auto}):
            with mock.patch("assistant.system_tasks._ensure_com"):
                found = system_tasks._find_window("cmd")
        self.assertIs(found, console)


class AccessibilityNudgeTests(unittest.TestCase):
    def test_prepare_scan_foregrounds_and_nudges_chromium(self):
        from assistant import system_tasks
        win = mock.MagicMock(ClassName="Chrome_WidgetWin_1",
                             NativeWindowHandle=7777)
        with mock.patch("assistant.system_tasks.activate_window") as mock_act, \
             mock.patch("assistant.system_tasks._nudge_accessibility") as mock_nudge, \
             mock.patch("time.sleep"):
            system_tasks._prepare_window_for_scan(win)
        mock_act.assert_called_once_with(7777)
        mock_nudge.assert_called_once_with(win)

    def test_prepare_scan_leaves_native_windows_alone(self):
        from assistant import system_tasks
        win = mock.MagicMock(ClassName="Notepad", NativeWindowHandle=1111)
        with mock.patch("assistant.system_tasks.activate_window") as mock_act, \
             mock.patch("assistant.system_tasks._nudge_accessibility") as mock_nudge, \
             mock.patch("time.sleep"):
            system_tasks._prepare_window_for_scan(win)
        mock_act.assert_not_called()
        mock_nudge.assert_not_called()

    def test_nudge_sends_wm_getobject(self):
        from assistant import system_tasks
        win = mock.MagicMock(NativeWindowHandle=8888, ProcessId=4242)
        with mock.patch.object(ctypes, "windll") as mock_windll:
            mock_windll.user32.EnumWindows.return_value = 1
            mock_windll.user32.EnumChildWindows.return_value = 1
            system_tasks._nudge_accessibility(win)
        args = mock_windll.user32.SendMessageTimeoutW.call_args[0]
        self.assertEqual(args[1], 0x003D)  # WM_GETOBJECT


class PhysicalClickTests(unittest.TestCase):
    @mock.patch("assistant.system_tasks._ensure_com")
    @mock.patch("assistant.system_tasks.activate_window")
    def test_click_element_uses_real_click_for_web_controls(self, _act, _com):
        import sys
        from assistant import system_tasks
        mock_auto = mock.MagicMock()
        mock_auto.ControlType.ButtonControl = 1
        mock_control = mock.MagicMock()
        mock_control.ControlType = 1
        mock_control.Name = "Sohail"
        mock_control.IsEnabled = True
        mock_control.BoundingRectangle = mock.MagicMock(
            left=100, top=200, width=lambda: 300, height=lambda: 60)
        mock_control.IsOffscreen = False
        mock_control.GetFrameworkId.return_value = "Chrome"

        mock_window = mock.MagicMock(Name="Netflix", NativeWindowHandle=12345,
                                     ClassName="Chrome_WidgetWin_1")
        mock_auto.WalkControl.return_value = [(mock_control, 1)]
        mock_auto.GetForegroundControl.return_value = mock_window
        mock_pyautogui = mock.MagicMock()

        with mock.patch.dict(sys.modules, {"uiautomation": mock_auto}):
            with mock.patch("assistant.system_tasks._find_window",
                            return_value=mock_window):
                with mock.patch("assistant.system_tasks._clickable_control_types",
                                return_value={1}):
                    with mock.patch("assistant.system_tasks._get_pyautogui",
                                    return_value=mock_pyautogui):
                        with mock.patch("assistant.system_tasks._prepare_window_for_scan"):
                            with mock.patch("time.sleep"):
                                res = system_tasks.click_element(
                                    "Sohail", window_title="Netflix")

        mock_control.GetInvokePattern.assert_not_called()
        mock_pyautogui.click.assert_called_once_with(250, 230)
        self.assertIn("Clicked 'Sohail'", res)
        self.assertIn("at (250, 230)", res)


# --- Minimal scripted-model harness (mirrors tests/test_chained_tasks.py;
# tests/ is not a package, so the helpers live here too). ---

def _call(func_name, **arguments):
    return {"function": {"name": func_name, "arguments": arguments}}


def _tools(*calls):
    return {"role": "assistant", "content": "", "tool_calls": list(calls)}


def _says(text):
    return {"role": "assistant", "content": text}


class ScriptedModel:
    def __init__(self, replies, verdicts=()):
        self.replies = list(replies)
        self.verdicts = list(verdicts)
        self.prompts = []

    def __call__(self, messages, model=None, tools=None):
        self.prompts.append(list(messages))
        if tools is False:
            if self.verdicts:
                return {"role": "assistant", "content": self.verdicts.pop(0)}
            return {"role": "assistant", "content": "DONE"}
        if self.replies:
            return self.replies.pop(0)
        return _says("Finished.")


class StallRedirectTests(unittest.TestCase):
    """The stall redirect must fire on EVERY repeat past the limit, not once.

    Interleaved inspections reset the consecutive-repeat counter, so only the
    per-signature counter catches this shape — which is exactly the Netflix
    loop (click, look, click, look, …).
    """

    def setUp(self):
        from assistant import ai_brain
        self.ai_brain = ai_brain
        self.executed = []

        def record(name):
            def run(**kwargs):
                self.executed.append((name, kwargs))
                return f"{name} ok"
            return run

        self.fake_tools = {
            "click_element": record("click_element"),
            "list_windows": record("list_windows"),
        }

    def test_repeated_click_keeps_getting_redirected(self):
        model = ScriptedModel([
            _tools(_call("click_element", name="Profile 1 Profile",
                         window_title="Netflix")),
            _tools(_call("list_windows")),
            _tools(_call("click_element", name="Profile 1 Profile",
                         window_title="Netflix")),
            _tools(_call("list_windows")),
            _tools(_call("click_element", name="Profile 1 Profile",
                         window_title="Netflix")),
            _tools(_call("list_windows")),
            _tools(_call("click_element", name="Profile 1 Profile",
                         window_title="Netflix")),
            _says("done."),
        ])
        conversation = [{"role": "system", "content": "system"},
                        {"role": "user", "content": "open netflix and open sohail profile"}]
        passthrough = lambda func, _tool_name=None, **kw: func(**kw)
        with mock.patch.object(self.ai_brain, "query_local_llm_chat", model), \
             mock.patch.dict(self.ai_brain.AVAILABLE_FUNCTIONS,
                             self.fake_tools, clear=False), \
             mock.patch.object(self.ai_brain.guard, "call",
                               side_effect=passthrough):
            self.ai_brain._agent_loop(conversation, max_steps=12, tools=[])

        redirects = [m["content"] for prompt in model.prompts for m in prompt
                     if m.get("role") == "system"
                     and ("Do not repeat it" in m.get("content", "")
                          or "Stop calling it" in m.get("content", ""))]
        self.assertGreaterEqual(
            len(redirects), 2,
            f"expected repeated stall redirects, got {len(redirects)}")
        # The redirect must point at the OCR route for browser text.
        self.assertTrue(
            any("find_and_click_text" in r for r in redirects),
            "stall redirect never mentioned the OCR route")


class ChromeGuardTests(unittest.TestCase):
    """click_element must not silently click browser chrome as page content.

    Live case: Edge's toolbar button 'Profile 1 Profile' matched while the
    Netflix page was invisible. The guard tries the page through OCR first
    and only falls through to the chrome control when the page has no match.
    """

    def _chrome_window(self):
        win = mock.MagicMock(Name="Netflix", NativeWindowHandle=12345,
                             ClassName="Chrome_WidgetWin_1")
        rect = mock.MagicMock(left=0, top=0, right=1920, bottom=1080)
        win.BoundingRectangle = rect
        return win

    def _toolbar_button(self, top_window):
        # A direct child of the top window: NOT page content.
        btn = mock.MagicMock()
        btn.ControlType = 1
        btn.Name = "Profile 1 Profile"
        btn.IsEnabled = True
        btn.BoundingRectangle = mock.MagicMock(
            left=1150, top=35, width=lambda: 100, height=lambda: 30)
        btn.IsOffscreen = False
        btn.GetParentControl.return_value = top_window
        return btn

    def _drive_click(self, btn, win, ocr_result):
        import sys
        from assistant import system_tasks
        mock_auto = mock.MagicMock()
        mock_auto.ControlType.ButtonControl = 1
        mock_auto.WalkControl.return_value = [(btn, 1)]
        mock_auto.GetForegroundControl.return_value = win
        mock_pyautogui = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"uiautomation": mock_auto}):
            with mock.patch("assistant.system_tasks._ensure_com"):
                with mock.patch("assistant.system_tasks.activate_window"):
                    with mock.patch("assistant.system_tasks._find_window",
                                    return_value=win):
                        with mock.patch("assistant.system_tasks._clickable_control_types",
                                        return_value={1}):
                            with mock.patch("assistant.system_tasks._prepare_window_for_scan"):
                                with mock.patch("assistant.vision.find_text_on_screen",
                                                return_value=ocr_result):
                                    with mock.patch("assistant.system_tasks._get_pyautogui",
                                                    return_value=mock_pyautogui):
                                        with mock.patch("time.sleep"):
                                            res = system_tasks.click_element(
                                                "Profile 1 Profile",
                                                window_title="Netflix")
        return res, mock_pyautogui, btn

    @mock.patch("assistant.system_tasks.click_at",
                return_value="Clicked at (360, 220).")
    def test_chrome_match_prefers_ocr_page_hit(self, mock_click):
        win = self._chrome_window()
        btn = self._toolbar_button(win)
        res, mock_pyautogui, btn = self._drive_click(btn, win, (360, 220))
        mock_click.assert_called_once_with(360, 220)
        mock_pyautogui.click.assert_not_called()
        btn.GetInvokePattern.assert_not_called()
        self.assertIn("page text via screenshot OCR", res)

    def test_chrome_match_falls_through_when_page_has_no_match(self):
        win = self._chrome_window()
        btn = self._toolbar_button(win)
        mock_invoke = mock.MagicMock()
        btn.GetInvokePattern.return_value = mock_invoke
        res, mock_pyautogui, _btn = self._drive_click(btn, win, None)
        # Toolbar intent preserved: a real click at the chrome control.
        mock_pyautogui.click.assert_called_once_with(1200, 50)
        self.assertIn("Clicked 'Profile 1 Profile'", res)
        self.assertNotIn("screenshot OCR", res)

    def test_is_page_content_true_under_render_host(self):
        from assistant import system_tasks
        top = mock.MagicMock(NativeWindowHandle=111)
        host = mock.MagicMock()
        host.Name = "Chrome Legacy Window"
        host.ClassName = "Chrome_RenderWidgetHostHWND"
        host.NativeWindowHandle = 222
        leaf = mock.MagicMock()
        leaf.GetParentControl.return_value = host
        self.assertTrue(system_tasks._is_page_content(leaf, top))

    def test_is_page_content_false_for_direct_child(self):
        from assistant import system_tasks
        top = mock.MagicMock(NativeWindowHandle=111)
        btn = mock.MagicMock()
        btn.GetParentControl.return_value = top
        self.assertFalse(system_tasks._is_page_content(btn, top))


class ClickTriggerTests(unittest.TestCase):
    def test_click_requests_offer_the_ocr_tool(self):
        from assistant.ai_brain import select_tools
        names = [t["function"]["name"]
                 for t in select_tools("click the submit button")]
        self.assertIn("find_and_click_text", names)
        self.assertIn("click_element", names)

    def test_profile_requests_offer_the_ocr_tool(self):
        from assistant.ai_brain import select_tools
        names = [t["function"]["name"]
                 for t in select_tools("open netflix and open sohail profile")]
        self.assertIn("find_and_click_text", names)


class ScanOcrEntriesTests(unittest.TestCase):
    """get_clickable_elements must list page text for Chromium windows."""

    def _win(self, cls="Chrome_WidgetWin_1", name="Netflix"):
        win = mock.MagicMock(Name=name, NativeWindowHandle=12345, ClassName=cls)
        win.BoundingRectangle = mock.MagicMock(left=0, top=0, right=1920, bottom=1080)
        return win

    def _scan(self, win, ocr_lines):
        import sys
        from assistant import system_tasks
        mock_auto = mock.MagicMock()
        mock_auto.WalkControl.return_value = []
        with mock.patch.dict(sys.modules, {"uiautomation": mock_auto}):
            with mock.patch("assistant.system_tasks._ensure_com"):
                with mock.patch("assistant.system_tasks._find_window",
                                return_value=win):
                    with mock.patch("assistant.system_tasks._prepare_window_for_scan"):
                        with mock.patch("assistant.vision.ocr_screen",
                                        return_value=ocr_lines) as mock_ocr:
                            with mock.patch("time.sleep"):
                                res = system_tasks.get_clickable_elements(
                                    window_title="Netflix")
        return res, mock_ocr

    def test_chromium_scan_lists_ocr_page_text(self):
        lines = [("Sohail", [[300, 200], [420, 200], [420, 240], [300, 240]], 0.85)]
        res, mock_ocr = self._scan(self._win(), lines)
        mock_ocr.assert_called_once()
        self.assertIn("'Sohail' (page text)", res)
        self.assertIn("click_at(x=360, y=220)", res)
        self.assertIn("find_and_click_text(target_text='Sohail'", res)

    def test_native_scan_does_not_ocr(self):
        lines = [("Sohail", [[300, 200], [420, 200], [420, 240], [300, 240]], 0.85)]
        res, mock_ocr = self._scan(self._win(cls="Notepad"), lines)
        mock_ocr.assert_not_called()
        self.assertIn("Found no named controls", res)

    def test_uia_names_win_over_ocr_dupes(self):
        import sys
        from assistant import system_tasks
        win = self._win()
        host = mock.MagicMock()
        host.Name = "Chrome Legacy Window"
        host.ClassName = "Chrome_RenderWidgetHostHWND"
        host.NativeWindowHandle = 99999
        btn = mock.MagicMock()
        btn.ControlType = 1
        btn.Name = "Sohail"
        btn.IsEnabled = True
        btn.BoundingRectangle = mock.MagicMock(left=0, top=0,
                                               width=lambda: 10, height=lambda: 10)
        btn.IsOffscreen = False
        # A real page button lives under the render host, so it stays listed.
        btn.GetParentControl.return_value = host
        mock_auto = mock.MagicMock()
        mock_auto.ControlType.ButtonControl = 1
        mock_auto.WalkControl.return_value = [(btn, 1)]
        lines = [("Sohail", [[300, 200], [420, 200], [420, 240], [300, 240]], 0.85)]
        with mock.patch.dict(sys.modules, {"uiautomation": mock_auto}):
            with mock.patch("assistant.system_tasks._ensure_com"):
                with mock.patch("assistant.system_tasks._find_window",
                                return_value=win):
                    with mock.patch("assistant.system_tasks._prepare_window_for_scan"):
                        with mock.patch("assistant.vision.ocr_screen",
                                        return_value=lines):
                            with mock.patch("assistant.system_tasks._clickable_control_types",
                                            return_value={1}):
                                with mock.patch("time.sleep"):
                                    res = system_tasks.get_clickable_elements(
                                        window_title="Netflix")
        self.assertNotIn("(page text)", res)
        self.assertIn("'Sohail'", res)

    def test_toolbar_controls_excluded_from_chromium_scan(self):
        import sys
        from assistant import system_tasks
        win = self._win()
        # Edge's own toolbar avatar: direct child of the top window.
        toolbar_btn = mock.MagicMock()
        toolbar_btn.ControlType = 1
        toolbar_btn.Name = "Profile 1 Profile"
        toolbar_btn.IsEnabled = True
        toolbar_btn.BoundingRectangle = mock.MagicMock(
            left=1150, top=35, width=lambda: 100, height=lambda: 30)
        toolbar_btn.IsOffscreen = False
        toolbar_btn.GetParentControl.return_value = win
        mock_auto = mock.MagicMock()
        mock_auto.ControlType.ButtonControl = 1
        mock_auto.WalkControl.return_value = [(toolbar_btn, 1)]
        lines = [("Sohail", [[300, 200], [420, 200], [420, 240], [300, 240]], 0.85)]
        with mock.patch.dict(sys.modules, {"uiautomation": mock_auto}):
            with mock.patch("assistant.system_tasks._ensure_com"):
                with mock.patch("assistant.system_tasks._find_window",
                                return_value=win):
                    with mock.patch("assistant.system_tasks._prepare_window_for_scan"):
                        with mock.patch("assistant.vision.ocr_screen",
                                        return_value=lines):
                            with mock.patch("assistant.system_tasks._clickable_control_types",
                                            return_value={1}):
                                with mock.patch("time.sleep"):
                                    res = system_tasks.get_clickable_elements(
                                        window_title="Netflix")
        self.assertNotIn("Profile 1 Profile", res)
        self.assertIn("'Sohail' (page text)", res)


if __name__ == "__main__":
    unittest.main()

"""Tests for finishing a request that is made of several actions.

"Open Netflix and open any profile" opened Netflix and stopped. Two things in
the loop caused that, and both are pinned down here:

* Any tool call identical to the previous one was treated as an infinite loop,
  and the model was told to give up. Looking twice in a row is how the work is
  actually done - click something, then list the page again to see what
  changed - so a chain died on its second look.
* A request was over as soon as the model produced words, however much of it
  was still undone.

Nothing here talks to Ollama or to the desktop: the model is a scripted
sequence of replies, and the tools are recorded rather than run.
"""

import unittest
from unittest import mock


def _call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


def _tools(*calls):
    return {"role": "assistant", "content": "", "tool_calls": list(calls)}


def _says(text):
    return {"role": "assistant", "content": text}


class ScriptedModel:
    """Answers with the next scripted reply, remembering what it was asked."""

    def __init__(self, replies, verdicts=()):
        self.replies = list(replies)
        self.verdicts = list(verdicts)
        self.prompts = []

    def __call__(self, messages, model=None, tools=None):
        self.prompts.append(list(messages))
        # The finish check is the one call made without any tools.
        if tools is False:
            if self.verdicts:
                return {"role": "assistant", "content": self.verdicts.pop(0)}
            return {"role": "assistant", "content": "DONE"}
        if self.replies:
            return self.replies.pop(0)
        return _says("Finished.")


class ChainedTaskTests(unittest.TestCase):
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
            name: record(name) for name in
            ("browse", "browser_elements", "browser_click", "open_app",
             "type_text", "press_key")
        }

    def _run(self, request, replies, verdicts=()):
        model = ScriptedModel(replies, verdicts)
        conversation = [{"role": "system", "content": "system"},
                        {"role": "user", "content": request}]

        with mock.patch.object(self.ai_brain, "query_local_llm_chat", model), \
             mock.patch.dict(self.ai_brain.AVAILABLE_FUNCTIONS,
                             self.fake_tools, clear=False):
            reply = self.ai_brain._agent_loop(conversation, max_steps=12,
                                              tools=[])
        return reply, model

    def test_looking_twice_in_a_row_does_not_end_the_task(self):
        # browser_elements() takes no arguments, so a second look is byte for
        # byte the same call as the first. That must not read as a stall.
        replies = [
            _tools(_call("browse", url="https://www.netflix.com")),
            _tools(_call("browser_elements")),
            _tools(_call("browser_elements")),
            _tools(_call("browser_click", index=2)),
            _says("Opened Netflix and picked a profile."),
        ]
        reply, _ = self._run("open netflix and open any profile", replies)

        self.assertEqual(reply, "Opened Netflix and picked a profile.")
        self.assertIn(("browser_click", {"index": 2}), self.executed)

    def test_a_repeated_action_is_redirected_rather_than_abandoned(self):
        # The same click over and over is a genuine stall, but the answer is to
        # look and try another way - never "give up and tell the user".
        replies = [
            _tools(_call("browser_click", index=2)),
            _tools(_call("browser_click", index=2)),
            _tools(_call("browser_click", index=2)),
            _says("Done."),
        ]
        _, model = self._run("open netflix and open any profile", replies)

        nudges = [message["content"]
                  for prompt in model.prompts for message in prompt
                  if message.get("role") == "system"]
        self.assertTrue(any("different action" in text for text in nudges))
        self.assertFalse(any("Give up" in text for text in nudges))

    def test_the_same_click_between_looks_is_stopped(self):
        # A model can loop without ever repeating a whole turn: click, wait,
        # look, click the same thing again. Counting whole turns missed that,
        # so the task spent every step it had on one element.
        replies = []
        for _ in range(4):
            replies.append(_tools(_call("browser_click", index=1)))
            replies.append(_tools(_call("browser_elements")))
        replies.append(_says("Stuck."))

        _, model = self._run("open netflix and open any profile", replies)

        clicks = [step for step in self.executed if step[0] == "browser_click"]
        self.assertLessEqual(len(clicks), 4)
        redirects = [message["content"]
                     for prompt in model.prompts for message in prompt
                     if message.get("role") == "system"
                     and "Stop calling it" in message.get("content", "")]
        self.assertTrue(redirects)

    def test_the_second_half_of_a_request_is_carried_out(self):
        # The model announces success after the first action. The loop has to
        # notice the profile was never picked and hand the rest back.
        replies = [
            _tools(_call("browse", url="https://www.netflix.com")),
            _says("Netflix is open."),
            _tools(_call("browser_click", index=1)),
            _says("Profile selected."),
        ]
        reply, model = self._run(
            "open netflix and open any profile", replies,
            verdicts=["NEXT: click a profile on the Netflix page", "DONE"])

        self.assertEqual(reply, "Profile selected.")
        self.assertIn(("browser_click", {"index": 1}), self.executed)
        pushes = [message["content"]
                  for prompt in model.prompts for message in prompt
                  if message.get("role") == "system"
                  and "Still to do" in message.get("content", "")]
        self.assertTrue(pushes)

    def test_a_single_action_request_is_not_second_guessed(self):
        # One action, done: no finish check, no extra turn.
        replies = [
            _tools(_call("open_app", app_name="notepad")),
            _says("Notepad is open."),
        ]
        reply, model = self._run("open notepad", replies)

        self.assertEqual(reply, "Notepad is open.")
        self.assertFalse(any(prompt for prompt in model.prompts
                             if prompt and prompt[-1]["content"].startswith(
                                 "REQUEST:")))

    def test_the_steps_of_a_request_are_named_for_the_model(self):
        replies = [_tools(_call("open_app", app_name="notepad")),
                   _tools(_call("type_text", text="hello")),
                   _says("Typed it.")]
        _, model = self._run("open notepad and type hello", replies)

        first_prompt = model.prompts[0]
        checklist = [message["content"] for message in first_prompt
                     if message.get("role") == "system"
                     and "steps" in message.get("content", "")]
        self.assertTrue(checklist)
        self.assertIn("1. open notepad", checklist[0])
        self.assertIn("2. type hello", checklist[0])


class ProgressCheckTests(unittest.TestCase):
    """An action that changed nothing must be reported as such."""

    def setUp(self):
        from assistant import ai_brain

        self.ai_brain = ai_brain
        self.executed = []

        def record(name):
            def run(**kwargs):
                self.executed.append((name, kwargs))
                # The page never changes, however much is clicked.
                return "Page: Netflix\n[1] button 'Rav'"
            return run

        self.fake_tools = {name: record(name) for name in
                           ("browser_click", "browser_elements")}

    def test_an_unchanged_page_is_called_out(self):
        model = ScriptedModel([
            _tools(_call("browser_elements")),
            _tools(_call("browser_click", target="Rav")),
            _tools(_call("browser_elements")),
            _says("Done."),
        ])
        conversation = [{"role": "system", "content": "system"},
                        {"role": "user", "content": "open netflix and play something"}]

        with mock.patch.object(self.ai_brain, "query_local_llm_chat", model), \
             mock.patch.dict(self.ai_brain.AVAILABLE_FUNCTIONS,
                             self.fake_tools, clear=False):
            self.ai_brain._agent_loop(conversation, max_steps=6, tools=[])

        results = [message.get("content", "") for message in conversation
                   if message.get("role") == "tool"]
        self.assertTrue(any("Nothing changed since your last action" in text
                            for text in results))

    def test_the_first_look_is_never_called_a_failure(self):
        model = ScriptedModel([
            _tools(_call("browser_elements")),
            _says("Here is the page."),
        ])
        conversation = [{"role": "system", "content": "system"},
                        {"role": "user", "content": "what is on the netflix page"}]

        with mock.patch.object(self.ai_brain, "query_local_llm_chat", model), \
             mock.patch.dict(self.ai_brain.AVAILABLE_FUNCTIONS,
                             self.fake_tools, clear=False):
            self.ai_brain._agent_loop(conversation, max_steps=4, tools=[])

        results = [message.get("content", "") for message in conversation
                   if message.get("role") == "tool"]
        self.assertFalse(any("Nothing changed" in text for text in results))


class WebTaskToolsTests(unittest.TestCase):
    """A website request is worked in the browser, not on the desktop."""

    def test_desktop_tools_are_left_off_a_website_request(self):
        from assistant.ai_brain import select_tools

        names = [tool["function"]["name"]
                 for tool in select_tools("open youtube and play lofi")]

        for desktop_tool in ("open_app", "focus_window", "click_at",
                             "get_clickable_elements"):
            self.assertNotIn(desktop_tool, names)
        self.assertIn("browse", names)
        self.assertIn("browser_click", names)

    def test_a_desktop_request_keeps_the_desktop_tools(self):
        from assistant.ai_brain import select_tools

        names = [tool["function"]["name"]
                 for tool in select_tools("open notepad and type hello")]

        for desktop_tool in ("open_app", "click_at", "press_key",
                             "get_clickable_elements"):
            self.assertIn(desktop_tool, names)

    def test_reloading_the_same_page_counts_as_a_stall(self):
        from assistant.ai_brain import INSPECTION_TOOLS

        # Listing what is on a page is a look; fetching the page again is an
        # action, and doing it three times is going in circles.
        self.assertNotIn("browse", INSPECTION_TOOLS)
        self.assertIn("browser_elements", INSPECTION_TOOLS)


class ToolBudgetTests(unittest.TestCase):
    """A capability the user names must survive the per-request tool budget."""

    def test_a_named_tool_is_not_cut_by_the_budget(self):
        from assistant.ai_brain import select_tools

        # tell_battery sits last in a group of 24 with a budget of 22, so it
        # used to be dropped from the one request that asked for it by name -
        # and the model went looking for the battery with screenshots.
        names = [tool["function"]["name"]
                 for tool in select_tools(
                     "take a screenshot and tell me the battery level")]
        self.assertIn("tell_battery", names)
        self.assertIn("take_screenshot", names)

    def test_the_web_tools_survive_a_website_request(self):
        from assistant.ai_brain import select_tools

        names = [tool["function"]["name"]
                 for tool in select_tools("open netflix and open any profile")]
        for tool in ("browse", "browser_elements", "browser_click"):
            self.assertIn(tool, names)


class WebsiteRoutingTests(unittest.TestCase):
    """A brand name is a website, not an app to hunt for in the Run dialog."""

    def test_known_services_resolve_to_their_address(self):
        from assistant.ai_brain import _site_for

        self.assertEqual(_site_for("open netflix and pick a profile"),
                         ("netflix", "https://www.netflix.com"))
        self.assertEqual(_site_for("go to example.com and log in")[1],
                         "https://example.com")

    def test_a_desktop_request_gets_no_website_hint(self):
        from assistant.ai_brain import _site_hint

        self.assertIsNone(_site_hint("open notepad and type hello"))

    def test_the_hint_names_the_browser_flow(self):
        from assistant.ai_brain import _site_hint

        hint = _site_hint("open netflix")["content"]
        self.assertIn("fall back to the browser", hint)
        self.assertIn("browse('https://www.netflix.com')", hint)
        self.assertIn("browser_click", hint)


class PromptContractTests(unittest.TestCase):
    """The prompt must teach the parameters the tools actually take."""

    def test_the_browser_flow_uses_the_real_parameter_names(self):
        from assistant.ai_brain import LLM_TOOLS, get_system_prompt

        prompt = get_system_prompt()
        self.assertIn("browser_click(target)", prompt)
        self.assertNotIn("browser_click(index)", prompt)

        schema = next(tool["function"] for tool in LLM_TOOLS
                      if tool["function"]["name"] == "browser_click")
        self.assertEqual(schema["parameters"]["required"], ["target"])


class StepSplittingTests(unittest.TestCase):
    def test_requests_split_on_the_words_people_actually_use(self):
        from assistant.ai_brain import split_steps

        cases = {
            "open netflix and open any profile":
                ["open netflix", "open any profile"],
            "open notepad and type hello then save it":
                ["open notepad", "type hello", "save it"],
            "open chrome, then go to youtube, and play lofi":
                ["open chrome", "go to youtube", "play lofi"],
        }
        for request, expected in cases.items():
            with self.subTest(request=request):
                self.assertEqual(split_steps(request), expected)

    def test_one_instruction_stays_one_step(self):
        from assistant.ai_brain import split_steps

        for request in ("open netflix",
                        "what is the weather today",
                        "play the song with the guitar and the drums"):
            with self.subTest(request=request):
                self.assertEqual(len(split_steps(request)), 1)


if __name__ == "__main__":
    unittest.main()

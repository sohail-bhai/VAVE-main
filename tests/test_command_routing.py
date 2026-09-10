"""Tests for command routing and the atomic desktop toolkit.

Two things are pinned down here. First, that ending the session is a decision
about the whole utterance: "stop overwatch" and "exit fullscreen" are ordinary
instructions, and treating the bare word "stop" as a shutdown used to kill the
assistant mid-task. Second, that the atomic tools a task gets chained from -
key combinations, waiting, window handling - behave predictably, since every
goal without a purpose-built tool is carried out by composing these.

The keyboard and window tests drive no real input; they check the translation
and clamping that happens before the OS is touched.
"""

import unittest


class QuitRoutingTests(unittest.TestCase):
    """Only an utterance aimed at the assistant itself should end the session."""

    @staticmethod
    def _is_quit(command):
        from assistant.commands import is_quit_command
        return is_quit_command(command)

    def test_instructions_containing_stop_are_not_shutdowns(self):
        # Each of these used to end the session, because the router matched the
        # bare substrings "stop", "exit" and "quit" anywhere in the sentence.
        for command in (
            "stop overwatch",
            "stop the music",
            "stop watching my screen",
            "stop the download",
            "exit fullscreen",
            "exit the game",
            "quit the game",
            "quit photoshop",
            "close the browser",
            "close notepad",
        ):
            with self.subTest(command=command):
                self.assertFalse(
                    self._is_quit(command),
                    f"{command!r} is an instruction, not a request to shut down",
                )

    def test_utterances_aimed_at_the_assistant_do_shut_down(self):
        for command in (
            "goodbye",
            "goodbye jarvis",
            "bye",
            "exit",
            "quit",
            "stop",
            "shut down",
            "quit vave",
            "stop yourself",
            "please exit",
            "goodbye.",
        ):
            with self.subTest(command=command):
                self.assertTrue(
                    self._is_quit(command),
                    f"{command!r} asks the assistant to shut down",
                )

    def test_blank_input_is_not_a_shutdown(self):
        for command in ("", "   ", None):
            with self.subTest(command=command):
                self.assertFalse(self._is_quit(command))


class KeyCombinationTests(unittest.TestCase):
    """Shortcuts are how a task saves, copies, or closes something."""

    @staticmethod
    def _normalise(raw):
        from assistant.system_tasks import _normalise_keys
        return _normalise_keys(raw)

    def test_combinations_are_split_into_keys(self):
        self.assertEqual(self._normalise("ctrl+s"), ["ctrl", "s"])
        self.assertEqual(self._normalise("ctrl+shift+esc"), ["ctrl", "shift", "esc"])
        self.assertEqual(self._normalise("alt+f4"), ["alt", "f4"])

    def test_spelling_a_model_might_use_is_accepted(self):
        # A language model will say "Control + S" or "control-s" as readily as
        # "ctrl+s", and pyautogui only understands the last of those.
        for raw in ("Ctrl + S", "control-s", "CONTROL+S", ["ctrl", "s"]):
            with self.subTest(raw=raw):
                self.assertEqual(self._normalise(raw), ["ctrl", "s"])

    def test_aliases_map_onto_platform_key_names(self):
        self.assertEqual(self._normalise("windows+r"), ["win", "r"])
        self.assertEqual(self._normalise("escape"), ["esc"])
        self.assertEqual(self._normalise("return"), ["enter"])

    def test_single_key_stays_single(self):
        self.assertEqual(self._normalise("enter"), ["enter"])

    def test_empty_input_yields_no_keys(self):
        self.assertEqual(self._normalise(""), [])
        self.assertEqual(self._normalise("  "), [])


class WaitTests(unittest.TestCase):
    """Waiting lets the interface settle, but must never stall the assistant."""

    def test_wait_is_clamped_to_a_sane_ceiling(self):
        from assistant.system_tasks import wait
        self.assertIn("30", wait(9999))

    def test_negative_and_unparsable_waits_do_not_raise(self):
        from assistant.system_tasks import wait
        self.assertIn("0", wait(-5))
        self.assertIn("1", wait("not a number"))


class ToolRegistryTests(unittest.TestCase):
    """The model can only use a tool it can both see and call."""

    def setUp(self):
        import assistant.ai_brain as ai_brain
        self.registry = ai_brain.AVAILABLE_FUNCTIONS
        self.schemas = {t["function"]["name"] for t in ai_brain.LLM_TOOLS}

    def test_atomic_desktop_tools_are_callable_and_described(self):
        # Without these a goal that has no purpose-built tool cannot be carried
        # out by composition, which is the whole fallback strategy.
        for name in (
            "get_clickable_elements", "read_screen", "list_windows",
            "focus_window", "close_window", "click_at", "double_click_at",
            "right_click_at", "move_mouse", "type_text", "press_key",
            "press_hotkey", "wait", "scroll", "drag_and_drop",
            "run_terminal_command",
        ):
            with self.subTest(tool=name):
                self.assertIn(name, self.registry, f"{name} is not callable")
                self.assertIn(name, self.schemas, f"{name} is invisible to the model")

    def test_every_described_tool_actually_exists(self):
        missing = sorted(self.schemas - set(self.registry))
        self.assertEqual(missing, [], f"described but not implemented: {missing}")


class ShortcutTypingTests(unittest.TestCase):
    """A shortcut handed to type_text must be pressed, not typed.

    Measured misuse: asked to select all and delete, the 3B model called
    type_text with "Ctrl+A\\nCtrl+Delete", which would have written those
    characters into the user's document.
    """

    @staticmethod
    def _sequence(raw):
        from assistant.system_tasks import _shortcut_sequence
        return _shortcut_sequence(raw)

    def test_a_lone_chord_is_recognised(self):
        for raw in ("ctrl+s", "Ctrl + A", "alt+f4", "CONTROL+S", "win+r",
                    "ctrl+shift+esc"):
            with self.subTest(raw=raw):
                self.assertEqual(self._sequence(raw), [raw.strip()])

    def test_several_chords_on_separate_lines_are_all_pressed(self):
        self.assertEqual(self._sequence("Ctrl+A\nCtrl+Delete"),
                         ["Ctrl+A", "Ctrl+Delete"])

    def test_real_text_is_still_typed(self):
        # Anything a person might actually want written must not be swallowed:
        # a false positive here silently loses the user's content.
        for raw in ("hello world", "2+2", "sales+marketing", "ctrl",
                    "Ctrl+A then type this", "my email is a+b@c.com",
                    "", "a+b+c+d+e", "press ctrl+s to save",
                    "The shortcut is Ctrl+S"):
            with self.subTest(raw=raw):
                self.assertEqual(self._sequence(raw), [])


class ToolSelectionTests(unittest.TestCase):
    """Narrowing decides what the model is even able to do."""

    @staticmethod
    def _names(instruction):
        import assistant.ai_brain as ai_brain
        return {t["function"]["name"]
                for t in ai_brain.select_tools(instruction)}

    def test_atomic_actions_are_offered_for_every_request(self):
        # The fallback strategy only exists if the fallback tools are sent.
        # "select all the text in notepad" matched no computer trigger at all,
        # so press_key was absent and the model settled for clear_notes.
        for instruction in (
            "select all the text in notepad and delete it",
            "undo what I just did",
            "summarise my unread email",
            "what is the capital of France",
            "",
        ):
            with self.subTest(instruction=instruction):
                names = self._names(instruction)
                for required in ("press_key", "type_text", "click_at",
                                 "get_clickable_elements", "focus_window",
                                 "list_windows", "close_window"):
                    self.assertIn(required, names)

    def test_triggers_match_whole_words(self):
        # "note" used to match inside "notepad", pulling in the notes tools and
        # letting clear_notes win; "doc" matched inside "document" and handed
        # the budget to Google Workspace.
        self.assertNotIn("clear_notes", self._names("close notepad"))
        self.assertNotIn("create_google_doc",
                         self._names("save the document open in notepad"))

    def test_request_specific_tools_still_lead(self):
        self.assertIn("summarize_gmail_inbox", self._names("summarise my inbox"))
        self.assertIn("browser_click", self._names("click the login button on example.com"))


class AtomicIntentRoutingTests(unittest.TestCase):
    """Settled desktop actions are routed without asking the model.

    Measured misuse that motivated this: asked to "copy everything on this
    screen", the 3B model called type_text with the sentence "Copy everything
    on this screen", which would have written those words into the user's
    document. Asked to "undo what I just did" it replied "I need to know which
    action you want to undo". Neither has any judgement in it - undo is ctrl+z -
    so guessing is strictly worse than knowing.
    """

    @staticmethod
    def _keys(command):
        from assistant.commands import _atomic_keystroke_intent
        found = _atomic_keystroke_intent(command)
        return found[0] if found else None

    @staticmethod
    def _close(command):
        from assistant.commands import _close_window_intent
        return _close_window_intent(command)

    def test_paraphrases_reach_the_right_keystroke(self):
        for command, expected in (
            ("undo what I just did", ["ctrl+z"]),
            ("undo that", ["ctrl+z"]),
            ("undo", ["ctrl+z"]),
            ("undo the last change", ["ctrl+z"]),
            ("redo that", ["ctrl+y"]),
            ("copy everything on this screen", ["ctrl+a", "ctrl+c"]),
            ("copy all the text on the screen", ["ctrl+a", "ctrl+c"]),
            ("select all and delete", ["ctrl+a", "delete"]),
            ("select all the text", ["ctrl+a"]),
            ("highlight everything", ["ctrl+a"]),
            ("save the document", ["ctrl+s"]),
            ("save my work", ["ctrl+s"]),
            ("save it", ["ctrl+s"]),
            ("paste that here", ["ctrl+v"]),
            ("cut this", ["ctrl+x"]),
            ("refresh the page", ["f5"]),
            ("minimise this window", ["win+down"]),
            ("dismiss this", ["esc"]),
        ):
            with self.subTest(command=command):
                self.assertEqual(self._keys(command), expected)

    def test_a_shortcut_wins_over_looking_like_a_window(self):
        # "exit fullscreen" parses as a window named "fullscreen" and "close
        # the tab" as the whole window; both would lose the user's work, so the
        # keystroke table is consulted first and has to answer for them.
        self.assertEqual(self._keys("exit fullscreen"), ["f11"])
        self.assertEqual(self._keys("close the tab"), ["ctrl+w"])

    def test_getting_rid_of_a_window_is_understood_however_phrased(self):
        for command, expected in (
            ("make the notepad window go away", "notepad"),
            ("close notepad", "notepad"),
            ("get rid of chrome", "chrome"),
            ("quit photoshop", "photoshop"),
            ("make brave disappear", "brave"),
            ("get spotify off my screen", "spotify"),
        ):
            with self.subTest(command=command):
                self.assertEqual(self._close(command), expected)

    def test_an_unnamed_window_means_the_one_in_focus(self):
        # close_window() with no title closes the foreground window, so "" and
        # None have to mean the same thing to both sides.
        for command in ("close this window", "close it", "close"):
            with self.subTest(command=command):
                self.assertEqual(self._close(command), "")

    def test_shutting_down_is_not_closing_a_window(self):
        # "exit" and "quit" alone are how the user ends the session, and "shut
        # down" is about the machine. If any of them defaulted to the window in
        # focus, saying goodbye would close whatever the user was looking at.
        for command in ("exit", "quit", "shut down", "shut down the laptop",
                        "closet organizer"):
            with self.subTest(command=command):
                self.assertIsNone(self._close(command))

    def test_ordinary_sentences_are_left_for_the_model(self):
        # A fast path that swallows these is worse than no fast path: the user
        # asked a question or set a compound goal, and pressing a key answers
        # neither. "close the deal with the client" is deliberately included -
        # it parses as a close request, and is only saved by the handler
        # checking that such a window exists before claiming to have closed it.
        for command in (
            "stop the music",
            "stop the download",
            "stop overwatch",
            "what should I save this as",
            "tell me how to undo a git commit",
            "copy the file to my desktop",
            "save this presentation to google drive",
            "type hello world into notepad and save it",
            "research undo history in vim and summarise it",
            "undo the damage that release did to our numbers",
        ):
            with self.subTest(command=command):
                self.assertIsNone(self._keys(command),
                                  f"{command!r} was hijacked as a keystroke")
                self.assertIsNone(self._close(command),
                                  f"{command!r} was hijacked as a window close")


class ClickByNameRoutingTests(unittest.TestCase):
    """Clicking a named control is atomic, so the label goes straight through.

    Measured: asked to "click the save button" the small model called
    list_windows and then asked which window the button was in - and asked again
    after being pushed to look for itself. The label was in the sentence the
    whole time.
    """

    @staticmethod
    def _target(command):
        from assistant.commands import _click_target
        return _click_target(command)

    def test_a_named_control_is_picked_out_however_it_is_phrased(self):
        for command, expected in (
            ("click the save button", "save"),
            ("press the OK button", "OK"),
            ("hit submit button", "submit"),
            ("click on the Rename menu item", "Rename"),
            ("tap the settings icon", "settings"),
            ("tick the remember me checkbox", "remember me"),
            ("uncheck the notify me box", "notify me"),
            ("select the Drive tab", "Drive"),
            ("open the file menu", "file"),
        ):
            with self.subTest(command=command):
                self.assertEqual(self._target(command), expected)

    def test_a_click_with_no_label_is_left_alone(self):
        # There is nothing to look up. "click at 400, 300" already has its own
        # tool, and "click the button" names no button at all.
        for command in ("click here", "click it", "click at 400, 300",
                        "click the button", "tick the box", "press ctrl+s",
                        "press enter", "double click"):
            with self.subTest(command=command):
                self.assertIsNone(self._target(command))

    def test_a_compound_request_still_goes_to_the_model(self):
        # One click is atomic; a click plus whatever comes after it is a plan.
        for command in ("click the save button then close notepad",
                        "open the file menu and click save as",
                        "click save if the tests passed"):
            with self.subTest(command=command):
                self.assertIsNone(self._target(command))

    def test_a_shortcut_still_wins_over_a_click(self):
        # "open a new tab" reads as opening a tab called "new"; it is ctrl+t,
        # and the keystroke table is consulted first so it stays that way.
        from assistant.commands import _atomic_keystroke_intent
        self.assertEqual(_atomic_keystroke_intent("open a new tab")[0], ["ctrl+t"])


class LiteralTypingTests(unittest.TestCase):
    """"type X" must not swallow the rest of the instruction as text.

    Measured: "type hello world into notepad and save it" typed the words
    "hello world into notepad and save it" into whatever had focus, because the
    fast path took everything after "type " as the literal. Falling through to
    the model costs a second; typing an instruction into the user's document
    costs them their document.
    """

    @staticmethod
    def _literal(remainder):
        from assistant.commands import _literal_to_type
        return _literal_to_type(remainder)

    def test_a_compound_request_is_left_for_the_model(self):
        for remainder in (
            "hello world into notepad and save it",
            "a shopping list and then save it",
            "my name in the address bar",
            "this in notepad",
            "notes and close it",
            "hello then press enter",
        ):
            with self.subTest(remainder=remainder):
                self.assertEqual(self._literal(remainder), "")

    def test_a_plain_literal_is_typed_as_given(self):
        for remainder in ("hello world", "my email is a@b.com",
                          "the quick brown fox", "2+2=4"):
            with self.subTest(remainder=remainder):
                self.assertEqual(self._literal(remainder), remainder)

    def test_quoting_types_exactly_what_is_quoted(self):
        # The escape hatch: the user has said where the literal ends, so the
        # compound markers inside it are text, not instructions.
        self.assertEqual(self._literal('"hello world into notepad and save it"'),
                         "hello world into notepad and save it")
        self.assertEqual(self._literal("'save it into notepad'"),
                         "save it into notepad")


class ModelEscalationTests(unittest.TestCase):
    """Reasoning work gets the bigger brain; keystrokes stay on the quick one."""

    def setUp(self):
        import assistant.ai_brain as ai_brain
        from assistant.config import get_setting
        self.ai_brain = ai_brain
        self.fast = get_setting("llm_model_fast", "qwen2.5:3b")
        self.smart = get_setting("llm_model_smart", "qwen3.5:9b")
        self._unavailable = dict(ai_brain._unavailable_models)
        ai_brain._unavailable_models.clear()

    def tearDown(self):
        self.ai_brain._unavailable_models.clear()
        self.ai_brain._unavailable_models.update(self._unavailable)

    def test_atomic_desktop_work_stays_on_the_fast_model(self):
        # Measured: the small model handles these well and answers in a
        # fraction of the time. Escalating them would cost seconds per task
        # and buy nothing.
        for command in ("close notepad", "press ctrl+s", "what time is it",
                        "open the run dialog and launch calculator"):
            with self.subTest(command=command):
                self.assertEqual(self.ai_brain.select_model(command), self.fast)

    def test_reasoning_work_escalates(self):
        for command in (
            "research the latest news on quantum computing and summarise it",
            "compare these two laptops and recommend one",
            "fix the bug in my python script",
            "rename the file report.txt to final.txt",
            "find my hackwave presentation, open it, check the numbers against "
            "the spreadsheet and then fix the last slide",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.ai_brain.select_model(command), self.smart)

    def test_a_model_that_will_not_load_is_not_chosen_again(self):
        # `switch to high performance` pins 9B by name. On a machine too full
        # to load it, every later request would otherwise die.
        self.ai_brain._unavailable_models[self.smart] = \
            self.ai_brain._DISABLE_AFTER_MISSES
        self.assertEqual(
            self.ai_brain.select_model("research and summarise this"), self.fast)

    def test_a_model_that_is_not_installed_is_only_looked_up_once(self):
        # A missing model is a settled fact, so it is written off immediately
        # rather than counted. This also caught a real crash: the write-off used
        # to call a set method on what is now a dict, so every request on a
        # machine without the big model raised AttributeError.
        real = self.ai_brain.installed_models
        self.ai_brain.installed_models = lambda: [self.fast]
        try:
            self.assertEqual(
                self.ai_brain.select_model("research and summarise this"),
                self.fast)
            self.assertTrue(self.ai_brain._is_unserviceable(self.smart))
        finally:
            self.ai_brain.installed_models = real

    def test_a_model_too_big_for_the_machine_is_dropped_on_the_first_refusal(self):
        # Ollama answers a model that will not fit with a 500 whose body says
        # "out-of-memory during startup". Measured on this machine: 13 seconds
        # per attempt, failing identically every time. Counting that as one of
        # three tolerated misses would spend most of a minute per task learning
        # what the first answer already said.
        self.assertTrue(self.ai_brain._looks_like_a_load_failure(
            'llama-server reported out-of-memory during startup: '
            'failed to allocate buffer of size 4684285952'))
        self.ai_brain._write_off_model(self.smart, "too big")
        self.assertTrue(self.ai_brain._is_unserviceable(self.smart))
        self.assertEqual(
            self.ai_brain.select_model("research and summarise this"), self.fast)

    def test_an_ordinary_refusal_is_not_read_as_a_load_failure(self):
        for detail in ("", "invalid request", "model requires more system memory"
                       " than is availab", "unexpected token"):
            with self.subTest(detail=detail):
                self.assertFalse(
                    self.ai_brain._looks_like_a_load_failure(detail))

    def test_a_single_blank_answer_does_not_disable_the_model(self):
        # One hiccup can be memory pressure that has since passed. Giving up
        # then would silently downgrade the rest of the session, so a model is
        # only abandoned after several misses in a row.
        self.ai_brain._unavailable_models[self.smart] = 1
        self.assertEqual(
            self.ai_brain.select_model("research and summarise this"), self.smart)

    def test_losing_the_big_model_costs_quality_not_the_task(self):
        real = self.ai_brain.query_local_llm_chat
        asked = []

        def flaky(messages, model=None, tools=None):
            asked.append(model)
            if model == self.smart:
                return None if len(asked) <= 6 else {"role": "assistant",
                                                     "content": "done"}
            return {"role": "assistant", "content": "done"}

        self.ai_brain.query_local_llm_chat = flaky
        try:
            # Each miss asks 9B, takes the fallback, and records one strike.
            for run in range(3):
                reply = self.ai_brain.chat_with_fallback([], model=self.smart)
                self.assertEqual(reply["content"], "done")
            self.assertEqual(asked, [self.smart, self.fast] * 3)

            # Fourth call: three strikes in a row, so the dead model is skipped
            # entirely - no full timeout before the fast answer.
            asked.clear()
            self.ai_brain.chat_with_fallback([], model=self.smart)
            self.assertEqual(asked, [self.fast])
        finally:
            self.ai_brain.query_local_llm_chat = real


class StoppedShortTests(unittest.TestCase):
    """An instruction answered with a question has not been carried out."""

    @staticmethod
    def _short(request, reply, tools_run):
        from assistant.ai_brain import _stopped_short
        return _stopped_short(request, reply, tools_run)

    def test_an_instruction_that_did_nothing_is_pushed(self):
        self.assertTrue(self._short("undo what I just did",
                                    "I need to know which action to undo.", 0))
        self.assertTrue(self._short("close notepad", "", 0))

    def test_an_instruction_that_ends_by_asking_is_pushed(self):
        self.assertTrue(self._short(
            "make the notepad window go away",
            "Notepad is now active. What would you like to do next?", 1))

    def test_a_finished_instruction_is_left_alone(self):
        self.assertFalse(self._short("close notepad",
                                     "Closed the window 'Untitled - Notepad'.", 1))

    def test_a_question_is_never_pushed(self):
        # The user asked for words, so words are the right answer and calling no
        # tool is correct. Pushing here would make VAVE argue with itself.
        for request in ("what time is it", "who wrote dune",
                        "how do I undo a git commit", "is it raining?",
                        "explain what a control plane is",
                        "tell me about my calendar"):
            with self.subTest(request=request):
                self.assertFalse(self._short(request, "Here you go.", 0))


class StructuredCommandRoutingTests(unittest.TestCase):
    """Patterns must match intended system actions and reject casual mentions."""

    def test_time_pattern_matches_exact_queries(self):
        from assistant.commands import _TIME_PATTERN
        for cmd in ("time", "what time is it", "tell me the time", "current time", "what's the time now"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(_TIME_PATTERN.match(cmd), f"Expected {cmd!r} to match time pattern")

    def test_time_pattern_rejects_casual_mentions(self):
        from assistant.commands import _TIME_PATTERN
        for cmd in ("take your time", "explain the space-time continuum", "what time is it in Tokyo", "every time I run this"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(_TIME_PATTERN.match(cmd), f"Expected {cmd!r} NOT to match time pattern")

    def test_date_pattern_matches_exact_queries(self):
        from assistant.commands import _DATE_PATTERN
        for cmd in ("date", "today's date", "what is today's date", "tell me the date", "current date"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(_DATE_PATTERN.match(cmd), f"Expected {cmd!r} to match date pattern")

    def test_date_pattern_rejects_casual_mentions(self):
        from assistant.commands import _DATE_PATTERN
        for cmd in ("what date did World War 2 end", "I have a date tonight", "expiration date of milk"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(_DATE_PATTERN.match(cmd), f"Expected {cmd!r} NOT to match date pattern")

    def test_battery_pattern_matches_exact_queries(self):
        from assistant.commands import _BATTERY_PATTERN
        for cmd in ("battery", "battery percentage", "battery status", "what is the battery", "how much battery is left"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(_BATTERY_PATTERN.match(cmd), f"Expected {cmd!r} to match battery pattern")

    def test_battery_pattern_rejects_casual_mentions(self):
        from assistant.commands import _BATTERY_PATTERN
        for cmd in ("assault and battery laws", "a battery of tests was conducted"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(_BATTERY_PATTERN.match(cmd), f"Expected {cmd!r} NOT to match battery pattern")

    def test_notes_patterns_match_and_reject_appropriately(self):
        from assistant.commands import _ADD_NOTE_PATTERN, _READ_NOTES_PATTERN, _CLEAR_NOTES_PATTERN
        self.assertIsNotNone(_ADD_NOTE_PATTERN.match("take note"))
        self.assertIsNotNone(_ADD_NOTE_PATTERN.match("take a note"))
        self.assertIsNotNone(_ADD_NOTE_PATTERN.match("add note"))
        self.assertIsNone(_ADD_NOTE_PATTERN.match("take note of the key points in this research paper"))

        self.assertIsNotNone(_READ_NOTES_PATTERN.match("read notes"))
        self.assertIsNotNone(_READ_NOTES_PATTERN.match("read my notes"))
        self.assertIsNone(_READ_NOTES_PATTERN.match("read notes from the meeting with Alice"))

        self.assertIsNotNone(_CLEAR_NOTES_PATTERN.match("clear notes"))
        self.assertIsNotNone(_CLEAR_NOTES_PATTERN.match("delete my notes"))


if __name__ == "__main__":
    unittest.main()

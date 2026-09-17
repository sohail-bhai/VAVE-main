import logging
logger = logging.getLogger(__name__)

from contextlib import contextmanager

import assistant.commands as commands
import assistant.system_tasks as system_tasks
from assistant.controller import AssistantController
from assistant.speech import is_speech_enabled, set_speech_enabled


@contextmanager
def _patched_command_side_effects(log):
    originals = {
        "commands_speak": commands.speak,
        "system_speak": system_tasks.speak,
        "web_open": commands.webbrowser.open,
        "open_app": commands.open_app,
        "tell_battery": commands.tell_battery,
        "take_screenshot": commands.take_screenshot,
        "set_volume": commands.set_volume,
        "change_volume_by": commands.change_volume_by,
        "mute_volume": commands.mute_volume,
        "lock_laptop": commands.lock_laptop,
        "shutdown_laptop": commands.shutdown_laptop,
        "restart_laptop": commands.restart_laptop,
        "add_note": commands.add_note,
        "read_notes": commands.read_notes,
        "clear_notes": commands.clear_notes,
    }

    def fake_speak(text):
        logger.info(f"SPEAK: {text}")
        log.append(("speak", text))

    def fake_web_open(url):
        logger.info(f"WEB: {url}")
        log.append(("web", url))

    def fake_open_app(app_name):
        logger.info(f"APP: {app_name}")
        log.append(("app", app_name))

    def fake_battery():
        fake_speak("Battery check stubbed.")

    def fake_screenshot():
        logger.info("SCREENSHOT: skipped")
        log.append(("screenshot", "skipped"))

    def fake_set_volume(level):
        logger.info(f"VOLUME: set {level}")
        log.append(("volume_set", level))

    def fake_change_volume_by(amount):
        logger.info(f"VOLUME: change {amount}")
        log.append(("volume_change", amount))

    def fake_mute_volume():
        logger.info("VOLUME: mute")
        log.append(("volume_mute", True))

    def fake_unsafe_action(name):
        def _inner():
            raise RuntimeError(f"Unsafe action reached during smoke test: {name}")

        return _inner

    commands.speak = fake_speak
    system_tasks.speak = fake_speak
    commands.webbrowser.open = fake_web_open
    commands.open_app = fake_open_app
    commands.tell_battery = fake_battery
    commands.take_screenshot = fake_screenshot
    commands.set_volume = fake_set_volume
    commands.change_volume_by = fake_change_volume_by
    commands.mute_volume = fake_mute_volume
    commands.lock_laptop = fake_unsafe_action("lock")
    commands.shutdown_laptop = fake_unsafe_action("shutdown")
    commands.restart_laptop = fake_unsafe_action("restart")
    commands.add_note = lambda: log.append(("note", "add")) or logger.info("NOTE: add")
    commands.read_notes = lambda: log.append(("note", "read")) or logger.info("NOTE: read")
    commands.clear_notes = fake_unsafe_action("clear_notes")

    try:
        yield
    finally:
        commands.speak = originals["commands_speak"]
        system_tasks.speak = originals["system_speak"]
        commands.webbrowser.open = originals["web_open"]
        commands.open_app = originals["open_app"]
        commands.tell_battery = originals["tell_battery"]
        commands.take_screenshot = originals["take_screenshot"]
        commands.set_volume = originals["set_volume"]
        commands.change_volume_by = originals["change_volume_by"]
        commands.mute_volume = originals["mute_volume"]
        commands.lock_laptop = originals["lock_laptop"]
        commands.shutdown_laptop = originals["shutdown_laptop"]
        commands.restart_laptop = originals["restart_laptop"]
        commands.add_note = originals["add_note"]
        commands.read_notes = originals["read_notes"]
        commands.clear_notes = originals["clear_notes"]


def _contains(log, event_type, expected):
    return any(
        item_type == event_type and expected in str(value)
        for item_type, value in log
    )


def run_smoke_tests():
    previous_speech_enabled = is_speech_enabled()
    set_speech_enabled(False)
    test_cases = [
        ("what time is it", lambda log: _contains(log, "speak", "time is")),
        ("date", lambda log: _contains(log, "speak", "date is")),
        ("open notepad", lambda log: ("app", "notepad") in log),
        ("search google for python", lambda log: _contains(log, "web", "q=python")),
        (
            "search youtube for python tutorial",
            lambda log: _contains(log, "web", "search_query=python+tutorial"),
        ),
        ("battery", lambda log: _contains(log, "speak", "Battery check stubbed")),
        ("take note", lambda log: ("note", "add") in log),
        ("read notes", lambda log: ("note", "read") in log),
        ("volume up 10", lambda log: ("volume_change", 10) in log),
        ("screenshot", lambda log: ("screenshot", "skipped") in log),
        ("exit", lambda log: _contains(log, "speak", "Goodbye")),
    ]

    passed = 0
    failed = []

    logger.info("Running safe VAVE smoke tests...")

    try:
        for command_text, assertion in test_cases:
            log = []

            try:
                with _patched_command_side_effects(log):
                    controller = AssistantController(
                        configure_speech_hooks=False,
                        speech_enabled=False,
                    )
                    controller.handle_text_command(command_text)

                if assertion(log):
                    logger.info(f"PASS: {command_text}")
                    passed += 1
                else:
                    logger.info(f"FAIL: {command_text}")
                    failed.append(command_text)

            except Exception as error:
                logger.info(f"FAIL: {command_text} ({error})")
                failed.append(command_text)
    finally:
        set_speech_enabled(previous_speech_enabled)

    total = len(test_cases)
    logger.info(f"Smoke test summary: {passed}/{total} passed.")

    if failed:
        logger.info("Failed commands:")
        for command_text in failed:
            logger.info(f"- {command_text}")
        return False

    return True

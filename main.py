import argparse
import sys

from assistant.controller import AssistantController
from assistant.smoke_test import run_smoke_tests
from assistant import events
from assistant import logging_setup

def build_parser():
    parser = argparse.ArgumentParser(
        description="VAVE Desktop Assistant"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--text",
        help="Run one text command without using the microphone."
    )
    mode.add_argument(
        "--once",
        action="store_true",
        help="Listen for one microphone command, execute it, then exit."
    )
    mode.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run safe command-router smoke checks with side effects stubbed."
    )
    mode.add_argument(
        "--gui",
        action="store_true",
        help="Launch the CustomTkinter desktop dashboard."
    )
    mode.add_argument(
        "--server",
        action="store_true",
        help="Run the control plane API so a paired phone can send remote tasks."
    )
    parser.add_argument(
        "--no-speech",
        action="store_true",
        help="Print responses without using pyttsx3 text-to-speech."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="API bind address for --server. Use 0.0.0.0 for phone access."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="API port for --server."
    )
    return parser


# `vave <word>` spellings for the installed console script. Every flag form
# keeps working; these are just shorter to say and to document.
_SUBCOMMANDS = {
    "gui": ["--gui"],
    "serve": ["--server"],
    "once": ["--once"],
    "smoke": ["--smoke-test"],
}


def _expand_subcommand(argv):
    """Translate a leading `vave <word>` into its flag form. Pure function."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and not args[0].startswith("-") and args[0] in _SUBCOMMANDS:
        return _SUBCOMMANDS[args[0]] + args[1:]
    return args


def _pair_args(rest):
    """Keep `--port N` for `vave pair`; anything else is not a pairing option."""
    out = []
    items = list(rest)
    i = 0
    while i < len(items):
        if items[i] == "--port" and i + 1 < len(items):
            out += ["--port", items[i + 1]]
            i += 2
        else:
            i += 1
    return out


def main(argv=None):
    expanded = _expand_subcommand(argv)

    if expanded and expanded[0] == "pair":
        # `vave pair` is not an assistant mode; it asks a running server
        # for a pairing code. Route straight to the API entry point.
        from assistant.api.app import main as api_main
        return api_main(["--pair"] + _pair_args(expanded[1:]))

    args = build_parser().parse_args(expanded)

    if args.smoke_test:
        # Configure logging first; the smoke test reports results through logging.
        logging_setup.configure_logging()
        passed = run_smoke_tests()
        return 0 if passed else 1

    from assistant.bootstrap import bootstrap_safety
    bus = bootstrap_safety()

    if args.gui:
        from vave_gui import main as gui_main
        return gui_main()

    if args.server:
        try:
            from assistant.api.app import main as api_main
        except ModuleNotFoundError as error:
            if error.name in {"fastapi", "uvicorn", "python_multipart"}:
                print(
                    "VAVE API dependencies are missing in this Python environment.\n"
                    "Use the project venv or install requirements:\n\n"
                    "    source venv/bin/activate\n"
                    "    python main.py --server --host 0.0.0.0 --port 8765\n\n"
                    "or:\n\n"
                    "    python -m pip install -r requirements.txt"
                )
                return 1
            raise
        return api_main(["--host", args.host, "--port", str(args.port)])

    controller = AssistantController(event_bus=bus, speech_enabled=not args.no_speech)

    if args.text:
        print(f"Running text command once: {args.text}")
        controller.handle_text_command(args.text)
        return 0

    if args.once:
        print("One-listen mode: listening for one command.")
        controller.run_once()
        return 0

    # Long-lived modes pick up where a previous process stopped. One-shots
    # above return before this on purpose.
    from assistant.bootstrap import resume_interrupted_tasks
    resume_interrupted_tasks()

    controller.run_forever()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

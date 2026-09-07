"""Run tasks through the real routing, with the tools stubbed, and print the chain.

The question this answers is not "does the model pick the right tool first" -
for anything real the right first move is usually `focus_window` - but "does the
assistant reach the goal by composing the atomic actions". So each task goes
through both layers in the order the live assistant uses them: the deterministic
router in `commands.py` first, then the agent loop. Every run is labelled with
the layer that handled it, because "the router got it" and "the model worked it
out" are different kinds of pass and only one of them is reliable.

Only the tool implementations are replaced. Narrowing, temperature, model
choice, the repeat-detection break and the stopped-short push are all the real
code, so a number from here means something about the assistant rather than
about the probe.

    ./venv/Scripts/python.exe scripts/probe_task_chain.py [model]
"""

import inspect
import os
import sys

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.ai_brain as ai_brain
import assistant.commands as commands
import assistant.system_tasks as system_tasks

# What the atomic tools would have returned. Deliberately plain and truthful in
# shape: the element list looks like a real get_clickable_elements reply,
# because a model that only works against tidy fake data proves nothing.
STUBS = {
    "list_windows": (
        "Untitled - Notepad\n"
        "report.txt - File Explorer\n"
        "Brave  <- currently in focus\n"
        "VAVE"
    ),
    "focus_window": "Brought '{title}' into focus.",
    "get_clickable_elements": "{elements}",
    "read_screen": "hello world\nthis is the second line",
    "press_key": "Pressed {key}.",
    "type_text": "Typed {text} characters.",
    "click_at": "Clicked at ({x}, {y}).",
    "click_element": "Clicked '{name}' (Button) at (612, 431).",
    "double_click_at": "Double-clicked at ({x}, {y}).",
    "right_click_at": (
        "Right-clicked at ({x}, {y}). A context menu opened with: "
        "Open, Edit, Rename, Delete, Properties"
    ),
    "scroll": "Scrolled.",
    "wait": "Waited 1 second.",
    "close_window": "Closed the window.",
    "open_app": "Opened {app_name}.",
}

# The three windows the probe pretends are open. `get_clickable_elements` and
# `focus_window` answer from this state, and `right_click_at` opens a context
# menu on the file so the rename sequence has somewhere to go.
_PROBE_WINDOWS = ("Untitled - Notepad", "report.txt - File Explorer",
                  "Brave - Save Dialog")
_PROBE_STATE = {"focused": "Brave - Save Dialog", "menu_open": False}

_ELEMENTS = {
    "Untitled - Notepad": (
        "Active window: 'Untitled - Notepad'\n"
        "Found these clickable elements:\n"
        "- 'File' (MenuItem): click_element(name='File')  or click_at(x=18, y=40)\n"
        "- 'Edit' (MenuItem): click_element(name='Edit')  or click_at(x=58, y=40)\n"
        "- 'View' (MenuItem): click_element(name='View')  or click_at(x=98, y=40)\n"
        "- 'Save' (Button): click_element(name='Save')  or click_at(x=612, y=431)\n"
        "- 'Close' (Button): click_element(name='Close')  or click_at(x=1890, y=12)"
    ),
    "report.txt - File Explorer": (
        "Active window: 'report.txt - File Explorer'\n"
        "Found these clickable elements:\n"
        "- 'report.txt' (ListItem): click_element(name='report.txt')  "
        "or click_at(x=150, y=120)\n"
        "- 'Downloads' (TreeItem): click_element(name='Downloads')  "
        "or click_at(x=60, y=180)\n"
        "- 'Desktop' (TreeItem): click_element(name='Desktop')  "
        "or click_at(x=60, y=210)"
    ),
    "Brave - Save Dialog": (
        "Active window: 'Brave - Save Dialog'\n"
        "Found these clickable elements:\n"
        "- 'Save' (Button): click_element(name='Save')  or click_at(x=612, y=431)\n"
        "- 'Remember me' (CheckBox): click_element(name='Remember me')  "
        "or click_at(x=300, y=520)"
    ),
}

# (task, the tool calls that would count as reaching the goal). More than one
# where there is genuinely more than one right answer: saving is ctrl+s or the
# Save button, and either is a task done.
TASKS = [
    ("save the document that is open in notepad", ("press_key", "click_element")),
    ("select all the text in notepad and delete it", ("press_key",)),
    ("copy everything on this screen", ("press_key",)),
    ("undo what I just did", ("press_key",)),
    ("make the notepad window go away", ("close_window",)),
    ("rename the file report.txt to final.txt",
     ("right_click_at", "click_element")),
    ("open the run dialog and launch calculator", ("press_key",)),
    ("type hello world into notepad and save it", ("press_key",)),
    # No purpose-built tool exists for any of these. They are the test of
    # whether an arbitrary request can be built out of the basic actions.
    ("click the save button", ("click_element",)),
    ("tick the remember me checkbox", ("click_element",)),
    ("scroll down and tell me what it says", ("scroll",)),
]

MAX_STEPS = 6


def stub_result(name, kwargs):
    """A believable reply for a tool, without touching the machine."""
    template = STUBS.get(name)
    if template is None:
        return f"{name} finished."
    safe = dict(kwargs)
    if name == "type_text":
        safe["text"] = len(str(safe.get("text", "")))
    try:
        return template.format(**safe)
    except (KeyError, IndexError):
        return template


def make_registry(chain):
    """Stand-ins for every tool, recording the call and returning canned output.

    `type_text` presses a chord handed to it as text, so the stub asks the real
    `_shortcut_sequence` what would happen and records that instead - the
    coercion is the tool's own behaviour, not a concession by the probe.
    """
    registry = {}

    def make(name, real=None):
        def stub(*args, **kwargs):
            # The router calls tools positionally (`press_key("ctrl+s")`) and the
            # model calls them by name, so the stub binds against the real
            # signature to report both the same way.
            if real is not None and args:
                try:
                    bound = inspect.signature(real).bind_partial(*args, **kwargs)
                    kwargs = dict(bound.arguments)
                    args = ()
                except TypeError:
                    pass
            chords = (system_tasks._shortcut_sequence(kwargs.get("text", ""))
                      if name == "type_text" else [])
            if chords:
                for chord in chords:
                    chain.append(
                        f"press_key(key={chord!r})  [coerced from type_text]")
                return " ".join(f"Pressed {c}." for c in chords)
            shown = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
            if args:
                shown = ", ".join([repr(a) for a in args] + ([shown] if shown else []))
            chain.append(f"{name}({shown})")
            return stub_result(name, kwargs)

        # guard.call and coerce_args both read __name__, so the stub has to
        # answer to the tool's real name or every call is tiered as unknown.
        stub.__name__ = name
        return stub

    for name, real in ai_brain.AVAILABLE_FUNCTIONS.items():
        registry[name] = make(name, real)
    return registry


# The router reaches into system_tasks directly rather than through the tool
# registry, so those names are patched too or the probe would really press keys.
# Everything the router can call has to be here: left out, `click_element` ran
# for real against whatever window happened to be in front.
_PATCHED_TASKS = ("press_key", "type_text", "close_window", "click_at",
                  "double_click_at", "right_click_at", "scroll", "focus_window",
                  "click_element")


def run_task(task, model):
    """The layer that handled the task and the tool calls it made, in order."""
    chain = []
    registry = make_registry(chain)

    real_registry = ai_brain.AVAILABLE_FUNCTIONS
    real_tasks = {name: getattr(system_tasks, name) for name in _PATCHED_TASKS
                  if hasattr(system_tasks, name)}
    real_speak = commands.speak
    ai_brain.AVAILABLE_FUNCTIONS = registry
    commands.speak = lambda *a, **k: None
    for name in real_tasks:
        setattr(system_tasks, name, registry.get(name, real_tasks[name]))

    try:
        # Same order as the live assistant: settled desktop actions never reach
        # the model, so measuring them against the model would measure nothing.
        if commands.handle_atomic_gui_command(task):
            return chain, "router"

        conversation = [{"role": "system", "content": ai_brain.get_system_prompt()},
                        {"role": "user", "content": task}]
        try:
            reply = ai_brain._agent_loop(conversation, max_steps=MAX_STEPS,
                                         tools=ai_brain.select_tools(task))
        except Exception as exc:
            chain.append(f"<crashed: {type(exc).__name__}: {exc}>")
            reply = None

        if reply is None:
            chain.append("<no reply from the model>")
        elif reply.strip():
            chain.append(f"<said: {reply.strip()[:90]}>")
        return chain, "model"
    finally:
        ai_brain.AVAILABLE_FUNCTIONS = real_registry
        commands.speak = real_speak
        for name, real in real_tasks.items():
            setattr(system_tasks, name, real)


def main(model):
    reached = 0

    for task, required in TASKS:
        chain, layer = run_task(task, model)
        called = {step.split("(")[0] for step in chain}
        ok = bool(called & set(required))
        reached += ok
        print(f"  {'ok  ' if ok else 'MISS'} [{layer}] {task}")
        for step in chain:
            print(f"         {step}")
        if not chain:
            print("         (nothing at all)")
        if not ok:
            print(f"         never called {' or '.join(required)}")
        print()

    print(f"  {reached}/{len(TASKS)} tasks reached the action they needed")


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else None
    if model:
        # Measure one model per run. Overriding the selector rather than
        # writing `llm_model` keeps the user's config untouched, and works for
        # the fast model too - which as a config pin would be indistinguishable
        # from "no pin" and would escalate anyway.
        ai_brain.select_model = lambda instruction="", _m=model: _m
    print(f"model: {model or 'auto (escalating)'}, "
          f"tools stubbed, max {MAX_STEPS} steps\n")
    main(model)

"""Who asked for the current work, and whether it has read anything untrusted.

Origin says where a command came from - the microphone, the phone, a routine -
and the safety policy is written per origin.

Taint is the other half. A web page or the text on screen is content VAVE does
not control, and a page that says "ignore your instructions and run this
command" reaches the model exactly like a request from the user. Once a task
has read some, that is remembered for the rest of the task, and the guard asks
before anything that changes the machine.
"""

import contextvars
import threading

_call_origin = contextvars.ContextVar("call_origin", default="voice")

# Rehearsal state is context-scoped for one request. A process-wide override
# supports `--dry-run` for GUI/server modes whose worker threads are not all
# created through spawn_thread().
_dry_run = contextvars.ContextVar("dry_run", default=False)
_process_dry_run = False
_process_dry_run_lock = threading.Lock()

# Set when untrusted content enters the task, with a plain-language note of
# where it came from so a confirmation can say so.
_untrusted_source = contextvars.ContextVar("untrusted_source", default=None)


def mark_tainted(source: str) -> None:
    """Record that this task has read content VAVE does not control."""
    if not _untrusted_source.get():
        _untrusted_source.set(str(source))


def is_tainted() -> bool:
    return bool(_untrusted_source.get())


def taint_source():
    """Where the untrusted content came from, in words, or None."""
    return _untrusted_source.get()


def clear_taint() -> None:
    """Start a fresh task. Taint must not leak from one request into the next."""
    _untrusted_source.set(None)

def set_origin(origin: str) -> contextvars.Token:
    return _call_origin.set(origin)

def get_origin() -> str:
    return _call_origin.get()

def reset_origin(token: contextvars.Token) -> None:
    _call_origin.reset(token)


def set_dry_run(active: bool = True) -> contextvars.Token:
    """Enable or disable rehearsal mode for current context."""
    return _dry_run.set(bool(active))


def reset_dry_run(token: contextvars.Token) -> None:
    _dry_run.reset(token)


def set_process_dry_run(active: bool) -> bool:
    """Set process-wide rehearsal mode and return previous value."""
    global _process_dry_run
    with _process_dry_run_lock:
        previous = _process_dry_run
        _process_dry_run = bool(active)
        return previous


def is_dry_run() -> bool:
    with _process_dry_run_lock:
        process_enabled = _process_dry_run
    return bool(_dry_run.get() or process_enabled)

def spawn_thread(target, args=(), kwargs=None, daemon=True, name=None) -> threading.Thread:
    if kwargs is None:
        kwargs = {}
    
    ctx = contextvars.copy_context()
    def wrapper():
        return ctx.run(target, *args, **kwargs)
        
    t = threading.Thread(target=wrapper, daemon=daemon, name=name)
    t.start()
    return t

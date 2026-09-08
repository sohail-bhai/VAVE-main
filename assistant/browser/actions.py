"""The browser, as tools a model can call.

Each function answers in plain text, because that text goes straight back into
the model's context. The pattern is always the same: do one thing, then say
what the page looks like now, so the next decision is made from what is
actually on screen rather than from what was expected.
"""

import logging

from assistant.browser.session import BrowserUnavailable, get_session
from assistant.site_memory import get_site_memory

logger = logging.getLogger(__name__)

# How much of a page to hand back after an action. Enough to decide with,
# small enough to leave room for the rest of the conversation.
GLANCE_CHARS = 900
READ_CHARS = 3000


def _session():
    return get_session()


def _page_summary(chars=GLANCE_CHARS):
    session = _session()
    where = session.describe()
    return (f"Page: {where['title']} ({where['url']})\n"
            f"{session.read_text(limit=chars)}")


def _element_list():
    elements = _session().elements()
    if not elements:
        return "No clickable elements found on this page."
    lines = [f"[{item['index']}] {item['role']}: {item['label']}" for item in elements]
    return "Things you can click or type into:\n" + "\n".join(lines)


def _guarded(action, *args, **kwargs):
    """Turn browser failures into something the model can react to."""
    try:
        return action(*args, **kwargs)
    except BrowserUnavailable as error:
        # Without this the model treats a missing browser as a reason to hunt
        # for the website on the desktop - win+r, the Run dialog, clicking at
        # nothing - instead of saying what is wrong.
        return (f"The browser is not available: {error}\n"
                "Do not try to reach this website by clicking the desktop or "
                "using the Run dialog. Tell the user to run that command "
                "first.")
    except LookupError as error:
        return f"{error}\n\n{_element_list()}"
    except Exception as error:
        logger.exception("Browser action failed")
        return f"That didn't work: {error}"


# -- the tools --------------------------------------------------------------

def browse(url):
    """Open a web page and report what is on it.

    Anything learned about this site before is handed back with the page, so
    the second visit does not start from nothing.
    """
    def run():
        _session().goto(url)
        hint = get_site_memory().as_hint(url)
        parts = [_page_summary(), _element_list()]
        if hint:
            parts.insert(0, hint)
        if _session().looks_like_login():
            parts.append(
                "This page is asking for a sign-in. Do not try to type a "
                "password. Call browser_wait_for_login and let the user do it "
                "in the window.")
        return "\n\n".join(parts)
    return _guarded(run)


def browser_fill_form(fields):
    """Fill several boxes in one go: {"Email": "...", "3": "..."}."""
    def run():
        parsed = _as_dict(fields)
        if not parsed:
            return ("Send the fields as an object, like "
                    '{"Email": "me@example.com", "Password": "secret://my_password"}.')
        filled = _session().fill_form(parsed)
        return f"Filled: {', '.join(filled)}.\n\n{_page_summary()}"
    return _guarded(run)


def browser_tabs():
    """List the open tabs."""
    def run():
        tabs = _session().tabs()
        lines = [f"[{tab['index']}] {tab['title']} ({tab['url']})" for tab in tabs]
        return "Open tabs:\n" + "\n".join(lines)
    return _guarded(run)


def browser_new_tab(url=""):
    """Open another tab, so a side trip does not lose the page you were on."""
    def run():
        _session().new_tab(url or None)
        return f"Opened a new tab.\n\n{_page_summary()}\n\n{_element_list()}"
    return _guarded(run)


def browser_switch_tab(index):
    """Go back to a tab by its number."""
    def run():
        _session().switch_tab(index)
        return f"{_page_summary()}\n\n{_element_list()}"
    return _guarded(run)


def browser_wait_for_login(seconds=60):
    """Hand the window to the user to sign in, then carry on.

    VAVE never types a password. This waits for the page to stop asking for
    one, which is what a person doing it themselves looks like from here.
    """
    def run():
        session = _session()
        if not session.looks_like_login():
            return f"That page is not asking for a sign-in.\n\n{_page_summary()}"

        signed_in = session.wait_until_signed_in(timeout_ms=int(seconds) * 1000)
        if not signed_in:
            return ("Still on the sign-in page. Tell the user to sign in in the "
                    "browser window, then ask me to continue.")
        return f"Signed in.\n\n{_page_summary()}\n\n{_element_list()}"
    return _guarded(run)


def remember_about_site(url, note):
    """Write down something that worked, for the next visit to this site."""
    def run():
        notes = get_site_memory().remember(url, note)
        return (f"Noted for {url}: {note}\n"
                f"I now know {len(notes)} thing(s) about that site.")
    return _guarded(run)


def browser_read(full=False):
    """Read the current page again, without touching anything."""
    return _guarded(lambda: _page_summary(READ_CHARS if full else GLANCE_CHARS))


def browser_elements():
    """List what can be clicked or typed into, numbered."""
    return _guarded(_element_list)


def browser_click(target):
    """Click something by its number or its visible text."""
    def run():
        result = _session().click(target)
        return (f"Clicked '{result['clicked']}'. Now on: {result['title']}\n\n"
                f"{_page_summary()}\n\n{_element_list()}")
    return _guarded(run)


def browser_type(target, text, submit=False):
    """Type into a box. `submit=True` presses Enter afterwards."""
    def run():
        result = _session().type_text(target, text, submit=_as_bool(submit))
        if result.get("warning"):
            return result["warning"]
        note = "and pressed Enter" if result["submitted"] else "without submitting"
        return (f"Typed into '{result['typed_into']}' {note}.\n\n"
                f"{_page_summary()}")
    return _guarded(run)


def browser_press(key):
    """Press a key, such as Enter or Escape."""
    def run():
        _session().press(key)
        return f"Pressed {key}.\n\n{_page_summary()}"
    return _guarded(run)


def browser_wait_for(text, seconds=30):
    """Wait until some text appears - how you know a slow answer arrived."""
    def run():
        _session().wait_for_text(text, timeout_ms=int(float(seconds) * 1000))
        return f"'{text}' appeared.\n\n{_page_summary(READ_CHARS)}"
    return _guarded(run)


def browser_screenshot():
    """Save a picture of the page, for when reading the text is not enough."""
    return _guarded(lambda: f"Saved a screenshot to {_session().screenshot()}")


def browser_ask_site(url, prompt, answer_appears_within=90):
    """Ask a question on a site that has a chat box, and bring the answer back.

    This is the ChatGPT-style flow in one call: open the site, find the box a
    person would type into, send the question, wait for the reply to finish,
    and return what it said. It works on any site with a single obvious input,
    which is why it is not named after one of them.
    """
    def run():
        session = _session()
        session.goto(url)

        box = _find_input(session)
        if box is None:
            return (f"Opened {url} but could not find a box to type into. "
                    f"{_element_list()}")

        before = session.read_text(limit=READ_CHARS)
        session.type_text(box["index"], prompt, submit=True)

        # The answer arrives after the page does - a chat writes it a word at
        # a time, a search engine fills the results in afterwards. So wait for
        # real content that has stopped changing, not for "loaded".
        waiter = getattr(session, "wait_for_content", None)
        if callable(waiter):
            waiter(min_words=40, timeout_ms=int(answer_appears_within) * 1000)
        else:
            _wait_for_change(session, before, int(answer_appears_within))

        after = session.read_text(limit=READ_CHARS)
        answer = _new_text(before, after)
        return (f"Asked on {session.describe()['url']}:\n{prompt}\n\n"
                f"The page now says:\n{answer or after}")
    return _guarded(run)


# -- helpers ----------------------------------------------------------------

def _as_dict(value):
    """Models send objects as objects, or as JSON in a string. Accept both."""
    import json

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except ValueError:
            return None
    return None


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1", "submit")


def _find_input(session):
    """The box a person would type a question into."""
    elements = session.elements()
    typeable = [item for item in elements
                if item["role"] in ("textbox", "input", "true", "textarea")]
    if not typeable:
        typeable = [item for item in elements
                    if any(word in item["label"].lower()
                           for word in ("ask", "message", "search", "prompt", "type"))]
    return typeable[0] if typeable else None


def _wait_for_change(session, before, seconds):
    """Poll until the page stops looking like it did before we asked."""
    import time

    deadline = time.monotonic() + seconds
    stable_since = None
    latest = before

    while time.monotonic() < deadline:
        time.sleep(1.0)
        current = session.read_text(limit=READ_CHARS)
        if current == latest and current != before:
            # Unchanged for a beat, and different from before: it finished.
            if stable_since and time.monotonic() - stable_since > 1.5:
                return True
            stable_since = stable_since or time.monotonic()
        else:
            stable_since = None
        latest = current
    return False


def _new_text(before, after):
    """Roughly, what the page gained - which is usually the answer."""
    if before and after.startswith(before[:200]):
        return after[len(before):].strip()
    return after.strip() if after != before else ""

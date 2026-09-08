"""One long-lived browser VAVE drives like a person would.

The browser is deliberately persistent: it keeps a profile directory, so a
site you logged into once stays logged in for later tasks. That is what makes
"look at my GitLab issues" possible without handling your password at all.

Playwright is imported lazily and every call goes through a small surface, so
the rest of VAVE - and every test - can work against a stand-in instead of a
real browser.
"""

import logging
import threading
from pathlib import Path

from assistant.config import get_setting

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# The logged-in profile lives here. Treat it like a password store.
PROFILE_DIR = PROJECT_ROOT / "data" / "browser_profile"

# How long to wait for a page to settle before reading it.
DEFAULT_TIMEOUT_MS = 20_000

# Elements are numbered for the model. More than this and the list stops
# being useful to a small model.
MAX_ELEMENTS = 60

# What counts as something a person could interact with.
INTERACTIVE_SELECTOR = (
    "a[href], button, input:not([type=hidden]), textarea, select, "
    "[role=button], [role=link], [role=tab], [role=menuitem], [contenteditable=true]"
)


class BrowserUnavailable(Exception):
    """Playwright or its browser is not installed."""


class BrowserSession:
    """A running browser, its current page, and the elements VAVE can see."""

    def __init__(self, headless=None, profile_dir=None, timeout_ms=DEFAULT_TIMEOUT_MS):
        self.headless = (get_setting("browser_headless", False)
                         if headless is None else headless)
        self.profile_dir = Path(profile_dir or PROFILE_DIR)
        self.timeout_ms = timeout_ms
        self._playwright = None
        self._context = None
        self._page = None
        self._elements = []
        self._lock = threading.RLock()

    # -- lifecycle ----------------------------------------------------------

    @property
    def started(self):
        return self._page is not None

    def start(self):
        """Open the browser if it is not already open. Returns the page."""
        with self._lock:
            if self._page is not None:
                return self._page

            try:
                from playwright.sync_api import sync_playwright
            except ImportError as error:
                raise BrowserUnavailable(
                    "Playwright is not installed. Run: pip install playwright "
                    "&& python -m playwright install chromium"
                ) from error

            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._playwright = sync_playwright().start()

            cdp_url = get_setting("browser_cdp_url", "http://127.0.0.1:9222")
            connected_via_cdp = False

            if cdp_url:
                try:
                    import urllib.request
                    # Non-blocking ping to verify if an active browser is listening on CDP port
                    req = urllib.request.Request(f"{str(cdp_url).rstrip('/')}/json/version")
                    with urllib.request.urlopen(req, timeout=0.3):
                        pass
                    browser_instance = self._playwright.chromium.connect_over_cdp(cdp_url)
                    if browser_instance.contexts:
                        self._context = browser_instance.contexts[0]
                    else:
                        self._context = browser_instance.new_context(viewport={"width": 1280, "height": 900})
                    connected_via_cdp = True
                    logger.info(f"[BrowserSession] Attached to active daily browser via CDP at {cdp_url}")
                except Exception as e:
                    logger.debug(f"[BrowserSession] CDP attach skipped ({e}), falling back to persistent profile.")

            if not connected_via_cdp:
                try:
                    self._context = self._playwright.chromium.launch_persistent_context(
                        user_data_dir=str(self.profile_dir),
                        headless=self.headless,
                        viewport={"width": 1280, "height": 900},
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                except Exception as error:
                    self.close()
                    raise BrowserUnavailable(
                        f"Could not start Chromium: {error}. "
                        "Run: python -m playwright install chromium"
                    ) from error

            self._context.set_default_timeout(self.timeout_ms)
            self._page = (self._context.pages[0] if self._context.pages
                          else self._context.new_page())
            return self._page

    def close(self):
        """Shut the browser down. The profile, and its logins, survive."""
        with self._lock:
            for closer in (self._context, self._playwright):
                try:
                    if closer is not None:
                        closer.close() if hasattr(closer, "close") else closer.stop()
                except Exception:
                    logger.debug("Browser did not close cleanly", exc_info=True)

            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass

            self._playwright = None
            self._context = None
            self._page = None
            self._elements = []

    # -- moving around ------------------------------------------------------

    def goto(self, url):
        page = self.start()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        page.goto(url, wait_until="domcontentloaded")
        self._settle()
        return self.describe()

    def back(self):
        page = self.start()
        page.go_back(wait_until="domcontentloaded")
        self._settle()
        return self.describe()

    def _settle(self):
        """Give the page a moment to finish rendering, without hanging on it."""
        try:
            self._page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass        # a busy page is still a readable page

    # -- looking ------------------------------------------------------------

    def describe(self):
        """Where we are: URL and title."""
        page = self.start()
        return {"url": page.url, "title": page.title()}

    # Where the actual content usually lives, in order of preference. Reading
    # the whole body means reading the navigation, the cookie banner and the
    # footer too, which crowds out the part that answers the question.
    CONTENT_SELECTORS = ("main", "[role=main]", "article", "#links", "#content")

    def read_text(self, limit=3000):
        """The readable text of the page, trimmed to fit a prompt."""
        page = self.start()
        text = ""

        for selector in self.CONTENT_SELECTORS:
            try:
                region = page.query_selector(selector)
                if region is not None and region.is_visible():
                    text = region.inner_text()
                    if len(text.split()) > 20:
                        break
            except Exception:
                continue

        if len(text.split()) <= 20:
            try:
                text = page.inner_text("body")
            except Exception:
                text = page.content()

        collapsed = " ".join(text.split())
        if len(collapsed) > limit:
            collapsed = collapsed[:limit] + " ... [trimmed]"
        return collapsed

    def elements(self, refresh=True):
        """Numbered interactive elements, the way a person would list them."""
        page = self.start()
        if not refresh and self._elements:
            return self._elements

        found = []
        for handle in page.query_selector_all(INTERACTIVE_SELECTOR):
            try:
                if not handle.is_visible():
                    continue
                label = _label_for(handle)
                if not label:
                    continue
                found.append({
                    "index": len(found) + 1,
                    "role": _role_for(handle),
                    "label": label[:80],
                    "handle": handle,
                })
                if len(found) >= MAX_ELEMENTS:
                    break
            except Exception:
                continue

        self._elements = found
        return found

    def find(self, target):
        """Resolve what the model asked for: a number, or visible text."""
        elements = self.elements(refresh=not self._elements)

        if isinstance(target, int) or (isinstance(target, str) and target.isdigit()):
            index = int(target)
            match = next((item for item in elements if item["index"] == index), None)
            if match is None:
                raise LookupError(f"There is no element {index} on this page.")
            return match

        wanted = str(target).strip().lower()
        exact = [item for item in elements if item["label"].lower() == wanted]
        partial = [item for item in elements if wanted in item["label"].lower()]
        match = (exact or partial or [None])[0]
        if match is None:
            raise LookupError(f"Nothing on this page looks like '{target}'.")
        return match

    # -- acting -------------------------------------------------------------

    def click(self, target):
        match = self.find(target)
        prev_pages = len(self._context.pages) if self._context and hasattr(self._context, "pages") else 1
        handle = match["handle"]
        try:
            handle.click(timeout=3000)
        except Exception:
            try:
                handle.click(force=True, timeout=3000)
            except Exception:
                try:
                    self._page.evaluate("(el) => el.click()", handle)
                except Exception as e:
                    logger.debug(f"[BrowserSession] Fallback DOM click failed: {e}")
                    raise
        self._settle()
        if self._context and hasattr(self._context, "pages") and len(self._context.pages) > prev_pages:
            self._page = self._context.pages[-1]
            logger.info(f"[BrowserSession] Switched active page to newly opened tab: {self._page.url}")
        self._elements = []
        return {"clicked": match["label"], **self.describe()}

    def type_text(self, target, text, submit=False):
        # Auto-recover if target and text were swapped by small model
        body_text = str(text or "")
        target_str = str(target or "")
        if not body_text and target_str:
            # Check if target is not actually an element on the page
            inputs = [e for e in self.elements() if e.get("role") in ("input", "combobox", "textbox", "search")]
            if inputs and not any(target_str == str(e.get("index")) or target_str.lower() in e.get("label", "").lower() for e in self.elements()):
                body_text = target_str
                target = inputs[0]["index"]

        match = self.find(target)
        handle = match["handle"]
        try:
            handle.click(timeout=3000)
        except Exception:
            try:
                handle.click(force=True, timeout=3000)
            except Exception:
                try:
                    self._page.evaluate("(el) => el.focus()", handle)
                except Exception:
                    pass

        try:
            handle.fill(body_text)
        except Exception:
            # Rich editors (ChatGPT's box among them) are not real inputs.
            self._page.keyboard.type(body_text)

        if submit:
            self._page.keyboard.press("Enter")
            self._settle()
            self._elements = []
        return {"typed_into": match["label"], "submitted": submit}

    def press(self, key):
        page = self.start()
        page.keyboard.press(key)
        self._settle()
        return self.describe()

    def wait_for_content(self, min_words=40, timeout_ms=None, quiet_for=1.5):
        """Wait until the page has real content and has stopped changing.

        Results and chat answers arrive after the page itself does, so waiting
        for "loaded" is not enough. This waits for enough words to be worth
        reading, and then for them to hold still.
        """
        import time

        deadline = time.monotonic() + (timeout_ms or self.timeout_ms) / 1000
        previous, settled_at = "", None

        while time.monotonic() < deadline:
            current = self.read_text(limit=4000)
            if current == previous and len(current.split()) >= min_words:
                if settled_at and time.monotonic() - settled_at >= quiet_for:
                    return True
                settled_at = settled_at or time.monotonic()
            else:
                settled_at = None
            previous = current
            time.sleep(0.5)

        return False

    def wait_for_text(self, text, timeout_ms=None):
        """Wait until some text shows up - how you know an answer arrived."""
        page = self.start()
        page.wait_for_function(
            "needle => document.body && document.body.innerText.includes(needle)",
            arg=text, timeout=timeout_ms or self.timeout_ms)
        return True

    def fill_form(self, fields):
        """Fill several boxes at once. `fields` is {label or number: value}."""
        filled = []
        for target, value in fields.items():
            match = self.find(target)
            handle = match["handle"]
            handle.click()
            try:
                handle.fill(str(value))
            except Exception:
                self._page.keyboard.type(str(value))
            filled.append(match["label"])
            self._elements = []
            self.elements()
        return filled

    def tabs(self):
        """Every open tab, so a flow that opens one can come back."""
        self.start()
        return [{"index": index + 1, "title": page.title(), "url": page.url}
                for index, page in enumerate(self._context.pages)]

    def new_tab(self, url=None):
        self.start()
        page = self._context.new_page()
        self._page = page
        self._elements = []
        if url:
            return self.goto(url)
        return self.describe()

    def switch_tab(self, index):
        self.start()
        pages = self._context.pages
        position = int(index) - 1
        if not 0 <= position < len(pages):
            raise LookupError(f"There is no tab {index}. There are {len(pages)}.")
        self._page = pages[position]
        self._page.bring_to_front()
        self._elements = []
        return self.describe()

    def looks_like_login(self):
        """Whether this page is asking a person to sign in."""
        page = self.start()
        try:
            has_password = page.query_selector("input[type=password]") is not None
        except Exception:
            has_password = False

        if has_password:
            return True

        words = ("sign in", "log in", "login", "continue with google",
                 "two-factor", "verify it's you")
        text = self.read_text(limit=1500).lower()
        return any(word in text for word in words)

    def wait_until_signed_in(self, timeout_ms=180_000, poll_ms=2000):
        """Wait for a person to finish signing in, in the window they can see.

        VAVE never types a password. It waits, and carries on once the page
        stops asking for one.
        """
        import time

        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if not self.looks_like_login():
                self._elements = []
                return True
            time.sleep(poll_ms / 1000)
        return False

    def screenshot(self, path=None):
        page = self.start()
        target = Path(path or (PROJECT_ROOT / "data" / "browser_last.png"))
        target.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target), full_page=False)
        return str(target)


def _label_for(handle):
    """The words a person would use for this element."""
    for source in ("aria-label", "placeholder", "value", "title", "name"):
        try:
            value = handle.get_attribute(source)
        except Exception:
            value = None
        if value and value.strip():
            return " ".join(value.split())

    try:
        text = handle.inner_text()
    except Exception:
        text = ""
    return " ".join(text.split()) if text else ""


def _role_for(handle):
    try:
        explicit = handle.get_attribute("role")
        if explicit:
            return explicit
        tag = handle.evaluate("node => node.tagName.toLowerCase()")
    except Exception:
        return "element"

    return {"a": "link", "button": "button", "input": "input",
            "textarea": "textbox", "select": "dropdown"}.get(tag, tag)


_session = None
_session_lock = threading.Lock()


def get_session():
    """The shared browser. One window, reused across tasks."""
    global _session
    with _session_lock:
        if _session is None:
            _session = BrowserSession()
        return _session


def set_session(session):
    """Used by tests, and by anything embedding VAVE with its own browser."""
    global _session
    with _session_lock:
        _session = session
        return _session


def reset_session():
    global _session
    with _session_lock:
        if _session is not None:
            _session.close()
        _session = None

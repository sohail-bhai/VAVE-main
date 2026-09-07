import re
import time
import logging
import webbrowser
import urllib.parse

logger = logging.getLogger(__name__)

from assistant.speech import speak, listen
from assistant.config import get_setting, update_setting
from assistant import guard
from assistant import call_context
from assistant.system_tasks import (
    open_app,
    close_app,
    tell_time,
    tell_date,
    tell_battery,
    take_screenshot,
    set_volume,
    change_volume_by,
    mute_volume,
    lock_laptop,
    shutdown_laptop,
    restart_laptop,
)
from assistant.notes import add_note, read_notes, clear_notes
from assistant.ai_brain import ask_ai
from assistant.dev_tools import git_auto_commit_and_push, deep_test_project, scrape_project_ideas, scaffold_code

def extract_number(command):
    match = re.search(r"\d+", command)
    if match:
        return int(match.group())
    return None

def change_user_name(command):
    """
    Example:
    change my name to sohail
    set my name to sohail
    """
    phrases = ["change my name to", "set my name to", "my name is"]

    for phrase in phrases:
        if phrase in command:
            new_name = command.split(phrase, 1)[1].strip().title()

            if new_name:
                update_setting("user_name", new_name)
                speak(f"Okay, I will call you {new_name}.")
                return True

    return False

def change_assistant_name(command):
    """
    Example:
    change assistant name to friday
    set your name to friday
    """
    phrases = ["change assistant name to", "set assistant name to", "change your name to", "set your name to"]

    for phrase in phrases:
        if phrase in command:
            new_name = command.split(phrase, 1)[1].strip().title()

            if new_name:
                update_setting("assistant_name", new_name)
                speak(f"Okay, my name is now {new_name}.")
                return True

    return False

def handle_settings_command(command):
    if change_user_name(command):
        return True

    if change_assistant_name(command):
        return True

    if "what is my name" in command:
        speak(f"Your name is {get_setting('user_name', 'Sir')}.")
        return True

    if "what is your name" in command:
        speak(f"My name is {get_setting('assistant_name', 'Vave')}.")
        return True

    return False

def handle_model_switch_command(command):
    """
    Hardcoded model switcher to bypass the LLM.
    Example: 'switch to high performance', 'switch to normal mode', or 'switch model qwen2.5:3b'
    """
    if "switch to high performance" in command:
        update_setting("llm_model", "qwen3.5:9b")
        speak("Hardcoded override successful. AI brain is now using high performance model (qwen3.5:9b).")
        return True
        
    if "switch to normal mode" in command:
        update_setting("llm_model", "qwen2.5:3b")
        speak("Hardcoded override successful. AI brain is now using normal mode model (qwen2.5:3b).")
        return True

    if "switch model" in command or "change model" in command:
        # try to extract the model name
        command_parts = command.replace("change model to", "switch model").split("switch model")
        if len(command_parts) > 1:
            new_model = command_parts[1].strip()
            if new_model:
                update_setting("llm_model", new_model)
                speak(f"Hardcoded override successful. AI brain is now using model: {new_model}")
                return True
    return False

def handle_volume_command(command):
    """
    Volume commands:
    - volume down            -> decrease by default step
    - volume down 20         -> decrease by 20
    - volume up              -> increase by default step
    - volume up 20           -> increase by 20
    - volume 40              -> set volume to 40
    - set volume to 40       -> set volume to 40
    - mute volume            -> mute/unmute
    """

    if "volume" not in command and "mute" not in command:
        return False

    if "mute" in command:
        mute_volume()
        return True

    amount = extract_number(command)
    default_step = int(get_setting("default_volume_step", 5))

    if "down" in command or "decrease" in command or "lower" in command:
        change_volume_by(-(amount if amount is not None else default_step))
        return True

    if "up" in command or "increase" in command or "raise" in command:
        change_volume_by(amount if amount is not None else default_step)
        return True

    if amount is not None:
        set_volume(amount)
        return True

    return False

def open_website(command):
    command_lower = command.lower().strip()
    websites = get_setting("websites", {})

    for site_name, url in websites.items():
        for prefix in ["open ", "launch ", "start "]:
            if command_lower == f"{prefix}{site_name.lower()}" or command_lower == f"{prefix}{site_name.lower()} please":
                speak(f"Opening {site_name}")
                webbrowser.open(url)
                return True

    return False

def handle_app_command(command):
    """
    Handles opening desktop applications and websites via smart launcher.
    Does not intercept compound or multi-action commands.
    """
    command_lower = command.lower().strip()

    compound_markers = [" and ", " then ", " after that ", " also ", " & ", ", "]
    if any(marker in command_lower for marker in compound_markers):
        return False

    app_aliases = {
        "notepad": "notepad",
        "google chrome": "chrome",
        "chrome": "chrome",
        "calculator": "calculator",
        "calc": "calculator",
        "vs code": "vscode",
        "vscode": "vscode",
        "file explorer": "file_explorer",
        "explorer": "file_explorer",
        "command prompt": "cmd",
        "terminal": "cmd",
        "cmd": "cmd",
    }
    for alias, app_name in app_aliases.items():
        for prefix in ["open ", "launch ", "start ", "run "]:
            if command_lower == f"{prefix}{alias}" or command_lower == f"{prefix}{alias} please":
                open_app(app_name)
                return True

    # Generic "open <target>" routing through smart launcher
    # Only fires for short, simple app names (1-3 words). Longer phrases go to AI.
    if command_lower.startswith("open "):
        target = command_lower[5:].strip()
        reserved = ["notes", "my notes", "file", "the door", "link", "a", "an", "the"]
        action_words = ["read", "write", "type", "check", "find", "search", "click",
                        "tell", "summarize", "copy", "my", "a ", "an ", "the "]
        target_words = target.split()
        # Only handle if it looks like an app name: short, no sentence structure
        if (target
                and target not in reserved
                and len(target_words) <= 3
                and not any(target.startswith(w) for w in action_words)
                and not any(c in target for c in ["?", "!", ","])):
            open_app(target)
            return True


    # Generic "close <target>" or "kill <target>"
    if command_lower.startswith("close ") or command_lower.startswith("kill "):
        target = command_lower.split(" ", 1)[1].strip()
        reserved = ["notes", "my notes", "file", "the door", "switch", "all"]
        if target and target not in reserved:
            close_app(target)
            return True

    return False

def google_search(command):
    query = ""

    if command.startswith("search google for "):
        query = command[len("search google for "):].strip()
    elif command.startswith("google search "):
        query = command[len("google search "):].strip()
    elif command.startswith("search "):
        query = command[len("search "):].strip()

    if query:
        speak(f"Searching Google for {query}")
        encoded_query = urllib.parse.quote_plus(query)
        webbrowser.open(f"https://www.google.com/search?q={encoded_query}")
        return True

    return False

def youtube_search(command):
    """Only matches commands that are explicitly a YouTube search request.
    Handles: 'search youtube for X', 'youtube search X', 'search on youtube for X'.
    Does NOT match 'open youtube search for X and play ...' — that goes to the AI brain.
    """
    query = ""

    if command.startswith("youtube search "):
        query = command[len("youtube search "):].strip()
    elif command.startswith("search youtube for "):
        query = command[len("search youtube for "):].strip()
    elif command.startswith("search on youtube for "):
        query = command[len("search on youtube for "):].strip()
    elif command.startswith("search on youtube "):
        query = command[len("search on youtube "):].strip()

    if query:
        speak(f"Searching YouTube for {query}")
        encoded_query = urllib.parse.quote_plus(query)
        webbrowser.open(f"https://www.youtube.com/results?search_query={encoded_query}")
        return True

    return False

def handle_web_interact_ai_command(command):
    """
    Routes compound commands (search + interact) for Web/YouTube to the AI brain.
    Examples: 'open youtube search for hasini and play the 3rd video'
              'search google for weather and click the first link'
    The AI brain chains: search_youtube/google → wait → get_clickable_elements → click_at.
    """
    c = command.lower()
    has_web = "youtube" in c or "google" in c or "search" in c
    has_interact = any(w in c for w in ["play", "click", "select", "watch", "open the video", "open the first", "open the link"])
    if has_web and has_interact:
        ask_ai(command, auto_confirm=True)
        return True
    return False

def confirm_action(question):
    speak(question)
    speak("Say yes to confirm or no to cancel.")

    answer = listen()

    if "yes" in answer or "confirm" in answer or "sure" in answer:
        return True

    speak("Cancelled.")
    return False

def check_routines(command):
    """
    Checks if the command matches a predefined routine in config.json.
    If it does, it executes the list of commands sequentially.
    """
    routines = get_setting("routines", {})
    
    for routine_name, commands_list in routines.items():
        if routine_name in command:
            speak(f"Running routine: {routine_name.title()}")
            for cmd in commands_list:
                call_context.set_origin("routine")
                if not execute_command(cmd, auto_confirm=True):
                    return False
            return True
            
    return False

# Everyday desktop actions that have exactly one right answer, and the many ways
# a person says them. These are all routed without asking the model, because the
# model was measured getting them wrong in ways that damage things: asked to
# copy the screen, the 3B model called type_text with "Copy everything on this
# screen" and would have written that sentence into the user's document. There
# is no judgement to make here - "undo that" is ctrl+z on any machine - so
# guessing is strictly worse than knowing.
#
# Each entry is (regex, keystrokes, what to say afterwards). Anchored whole-
# utterance so a passing mention inside a longer request still reaches the model,
# which is where the composing belongs.
_ATOMIC_INTENTS = (
    (r"(?:please\s+)?undo(?:\s+(?:that|this|it|what\s+(?:i|you)\s+just\s+did|"
     r"the\s+last\s+(?:thing|action|change)|my\s+last\s+(?:action|change)))?",
     ["ctrl+z"], "Undone."),
    (r"redo(?:\s+(?:that|this|it))?", ["ctrl+y"], "Redone."),
    (r"(?:select|highlight)\s+(?:all|everything)(?:\s+(?:the\s+)?text)?"
     r"(?:\s+(?:here|on\s+(?:the\s+)?screen|in\s+(?:this|the)\s+window))?",
     ["ctrl+a"], "Selected everything."),
    (r"copy\s+(?:all|everything|the\s+whole\s+(?:thing|screen|page|document))"
     r"(?:\s+(?:the\s+)?text)?(?:\s+(?:here|on\s+(?:the\s+|this\s+)?screen|"
     r"in\s+(?:this|the)\s+window|on\s+(?:the\s+)?page))?",
     ["ctrl+a", "ctrl+c"], "Copied everything."),
    (r"(?:copy|copy\s+that|copy\s+this|copy\s+it)", ["ctrl+c"], "Copied."),
    (r"(?:paste|paste\s+(?:that|this|it)(?:\s+here)?)", ["ctrl+v"], "Pasted."),
    (r"cut\s+(?:that|this|it)", ["ctrl+x"], "Cut."),
    (r"(?:save(?:\s+(?:it|this|that))?|save\s+the\s+(?:file|document|changes)|"
     r"save\s+(?:my\s+)?(?:work|changes))", ["ctrl+s"], "Saved."),
    (r"(?:select|highlight)\s+all\s+and\s+delete(?:\s+it)?",
     ["ctrl+a", "delete"], "Cleared it."),
    (r"(?:find|search)\s+(?:in\s+)?(?:this|the)\s+(?:page|document|file)",
     ["ctrl+f"], "Opened find."),
    (r"(?:refresh|reload)(?:\s+(?:the\s+)?(?:page|window|tab))?",
     ["f5"], "Refreshed."),
    (r"(?:new\s+tab|open\s+a\s+new\s+tab)", ["ctrl+t"], "Opened a new tab."),
    (r"(?:switch|next)\s+(?:to\s+the\s+)?(?:next\s+)?(?:window|app)",
     ["alt+tab"], "Switched window."),
    (r"(?:print(?:\s+(?:this|it|the\s+page))?)", ["ctrl+p"], "Opened print."),
    (r"(?:zoom\s+in|make\s+(?:it|this)\s+bigger)", ["ctrl+plus"], "Zoomed in."),
    (r"(?:zoom\s+out|make\s+(?:it|this)\s+smaller)", ["ctrl+-"], "Zoomed out."),
    (r"(?:minimi[sz]e|minimi[sz]e\s+(?:this|the)\s+window)",
     ["win+down"], "Minimised."),
    (r"(?:maximi[sz]e|maximi[sz]e\s+(?:this|the)\s+window|full\s*screen)",
     ["win+up"], "Maximised."),
    # Before the close-window rules below, which would otherwise read "exit
    # fullscreen" as a window called "fullscreen" and "close the tab" as the
    # whole window. Both are keystrokes, and both would lose the user's work.
    (r"(?:exit|leave|turn\s+off|get\s+out\s+of)\s+(?:the\s+)?full\s*screen"
     r"(?:\s+mode)?", ["f11"], "Left fullscreen."),
    (r"(?:close|quit|exit)\s+(?:the\s+|this\s+)?tab", ["ctrl+w"], "Closed the tab."),
    (r"(?:escape|cancel\s+that|never\s*mind\s+that|dismiss\s+(?:this|that|it))",
     ["esc"], "Dismissed."),
)

_ATOMIC_PATTERNS = tuple(
    (re.compile(r"^\s*" + pattern + r"\s*[.!]?\s*$", re.IGNORECASE), keys, spoken)
    for pattern, keys, spoken in _ATOMIC_INTENTS
)

# Getting rid of a window, however it is phrased. The title is whatever is left
# after the phrasing is stripped; nothing left means the window in focus.
_CLOSE_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    # "close" on its own means the window in focus. The other verbs need a name:
    # bare "exit" and "quit" are how the user shuts VAVE down, and "shut down"
    # is about the machine, so none of them may default to a window. "shut" is
    # left out entirely rather than competing with shutdown.
    r"^\s*(?:please\s+)?close\b\s*"
    r"(?:the\s+|my\s+|this\s+)?(?P<title>.*?)\s*(?:window|app|program)?\s*[.!]?\s*$",
    r"^\s*(?:please\s+)?(?:quit|exit|kill)\s+"
    r"(?:the\s+|my\s+|this\s+)?(?P<title>.+?)\s*(?:window|app|program)?\s*[.!]?\s*$",
    r"^\s*(?:make|get)\s+(?:the\s+|my\s+|this\s+)?(?P<title>.*?)\s*"
    r"(?:window|app|program)?\s*(?:go\s+away|disappear|off\s+(?:my\s+)?screen)"
    r"\s*[.!]?\s*$",
    r"^\s*get\s+rid\s+of\s+(?:the\s+|my\s+|this\s+)?(?P<title>.*?)\s*"
    r"(?:window|app|program)?\s*[.!]?\s*$",
))

# Words that are the window rather than a name for one, so "close this window"
# and "close it" both mean whatever is in focus.
_FOCUSED_WINDOW_WORDS = frozenset({
    "", "it", "this", "that", "current", "active", "the current", "the active",
})


def _atomic_keystroke_intent(command):
    """The keystrokes for an unambiguous desktop request, or None."""
    for pattern, keys, spoken in _ATOMIC_PATTERNS:
        if pattern.match(command):
            return keys, spoken
    return None


# Words that turn "type X" into a request with more to it than typing. "type
# hello world into notepad and save it" used to type the words "hello world into
# notepad and save it" into whatever had focus, because everything after "type "
# was taken as the literal. Anything carrying one of these goes to the model,
# which can open Notepad, type, and then save. Falling through costs a second;
# typing the instruction into the user's document costs them their document.
_COMPOUND_TYPING_MARKERS = re.compile(
    r"\b(?:into|in\s+to|and\s+then|then\s+|and\s+(?:save|send|close|open|press|"
    r"delete|clear|submit|post|copy|paste|print|search)\b)|\bin\s+(?:notepad|"
    r"word|excel|chrome|brave|firefox|edge|the\s+(?:browser|editor|document|"
    r"file|window|address\s+bar|search\s+bar))\b", re.IGNORECASE)


def _literal_to_type(remainder):
    """The exact text to type, or "" when the request is more than typing.

    Quoting is the escape hatch: `type "save it into notepad"` types those words
    and nothing else, because the user said where the literal ends.
    """
    text = remainder.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if _COMPOUND_TYPING_MARKERS.search(text):
        return ""
    return text


def _close_window_intent(command):
    """The window title to close, or None if this is not a close request.

    Returns "" for the window currently in focus. `close_window` already treats
    a missing title that way, so the two agree.
    """
    for pattern in _CLOSE_PATTERNS:
        match = pattern.match(command)
        if not match:
            continue
        title = " ".join(match.group("title").split())
        if title.lower() in _FOCUSED_WINDOW_WORDS:
            return ""
        return title
    return None


# Clicking a named thing. "click the save button" is as atomic as it gets, and
# measured, the small model answered it by calling list_windows and then asking
# which window the button was in - twice, even after being pushed to look for
# itself. There is nothing to work out here: the label is in the sentence.
#
# Anchored whole-utterance, so "click save then close the window" still goes to
# the model, which is where composing belongs.
_CLICK_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"^\s*(?:please\s+)?(?:click|press|hit|tap|push)\s+(?:on\s+)?"
    r"(?:the\s+|that\s+|a\s+)?(?P<name>.+?)\s*"
    r"(?:button|link|tab|menu\s*item|menu|icon|option|item)\s*[.!]?\s*$",
    r"^\s*(?:please\s+)?(?:tick|check|untick|uncheck|toggle)\s+"
    r"(?:the\s+|that\s+)?(?P<name>.+?)\s*"
    r"(?:checkbox|check\s*box|box|toggle|switch)\s*[.!]?\s*$",
    r"^\s*(?:please\s+)?(?:select|choose|open)\s+(?:the\s+)?(?P<name>.+?)\s*"
    r"(?:tab|menu)\s*[.!]?\s*$",
))

# Words that mean "whatever is under the pointer" rather than a label, plus the
# shapes of a coordinate. "click at 400, 300" and "click here" are not this.
_UNNAMED_CLICK_TARGETS = frozenset({
    "", "it", "this", "that", "there", "here", "same", "one",
})

# A real label never starts with one of these. "click the button" captures "the"
# and means nothing; stripping leaves nothing, which is the right answer.
_LEADING_DETERMINERS = ("the ", "that ", "this ", "a ", "an ", "another ",
                        "my ", "its ", "their ")


def _click_target(command):
    """The label of the control a request names, or None if it names none."""
    for pattern in _CLICK_PATTERNS:
        match = pattern.match(command)
        if not match:
            continue
        name = " ".join(match.group("name").split())
        lowered = name.lower()
        for determiner in _LEADING_DETERMINERS:
            if lowered.startswith(determiner):
                name = name[len(determiner):].strip()
                lowered = name.lower()
                break
        if lowered in _UNNAMED_CLICK_TARGETS or lowered in ("the", "a", "an"):
            return None
        # "click at 400, 300" is a coordinate click and has its own tool.
        if re.fullmatch(r"[\d\s,.:x()-]+", name) or name.lower().startswith("at "):
            return None
        return name
    return None


def handle_atomic_gui_command(command: str) -> bool:
    """Directly executes atomic GUI actions like typing or key presses without LLM overhead."""
    clean = command.strip()
    clean_lower = clean.lower()

    # The paraphrase table first: these are settled questions, and routing them
    # here is both faster and more reliable than a round trip to the model.
    keystrokes = _atomic_keystroke_intent(clean)
    if keystrokes is not None:
        from assistant.system_tasks import press_key
        keys, spoken = keystrokes
        for key in keys:
            press_key(key)
        speak(spoken)
        return True

    title = _close_window_intent(clean)
    if title is not None:
        from assistant.system_tasks import close_window
        result = close_window(title) if title else close_window()
        # "close the deal with the client" looks exactly like a close request
        # until you check, and there is no window called that. Rather than
        # reporting a failure the user did not ask about, hand the sentence on
        # to the model, which may well have a better idea what it meant.
        if title and result.lower().startswith("no open window"):
            return False
        speak(result)
        return True

    target = _click_target(clean)
    if target:
        from assistant.system_tasks import click_element
        result = click_element(target)
        # No control by that name: the sentence probably was not about a button
        # at all ("check my email", "open the fridge door"), so let the model
        # read it rather than reporting a button that was never mentioned.
        if result.lower().startswith(("nothing labelled", "could not tell")):
            return False
        speak(result)
        return True

    if clean_lower.startswith("type ") or clean_lower.startswith("write "):
        first_space = clean.find(" ")
        text_to_type = _literal_to_type(clean[first_space + 1:].strip())
        if text_to_type:
            from assistant.system_tasks import type_text
            type_text(text_to_type)
            speak(f"Typed {text_to_type}")
            return True

    if clean_lower.startswith("press key ") or clean_lower.startswith("press "):
        key_name = clean_lower.replace("press key", "press").split("press", 1)[1].strip()
        if key_name:
            from assistant.system_tasks import press_key
            press_key(key_name)
            speak(f"Pressed {key_name}")
            return True

    if clean_lower.startswith("scroll down"):
        from assistant.system_tasks import scroll
        scroll(-5)
        speak("Scrolled down.")
        return True

    if clean_lower.startswith("scroll up"):
        from assistant.system_tasks import scroll
        scroll(5)
        speak("Scrolled up.")
        return True

    return False

def handle_read_screen_command(command: str) -> bool:
    """
    Directly handles reading screen text or specific lines (e.g. 'read 2nd line', 'read line 10', 'read screen').
    """
    clean = command.strip().lower()

    # 1. Check for specific line number request
    line_num = None
    m1 = re.search(r"read (?:the )?(\d+)(?:st|nd|rd|th)? line", clean)
    if m1:
        line_num = int(m1.group(1))
    else:
        m2 = re.search(r"read (?:the )?line (\d+)", clean)
        if m2:
            line_num = int(m2.group(1))

    if line_num is not None:
        from assistant.system_tasks import read_screen
        res = read_screen(line_number=line_num)
        speak(res)
        return True

    # 2. Check for general screen reading request
    if clean in ("read screen", "read the screen", "read text on screen", "read document", "read the document"):
        from assistant.system_tasks import read_screen
        res = read_screen()
        speak(res)
        return True

    return False

def handle_line_write_command(command: str) -> bool:
    """
    Directly handles adding, inserting, or writing text onto a specific line in an open editor window.
    Example: 'add a hahaha on the 5th line', 'write hello on line 2'
    """
    clean = command.strip()

    # 1. 'add <text> on/to/at [the] line <N>' or '[the] <N>th line'
    m1 = re.search(r'(?:add|write|insert|type|put)\s+(?:a\s+)?(.+?)\s+(?:on|to|at|in)\s+(?:the\s+)?(?:line\s+(\d+)|(\d+)(?:st|nd|rd|th)?\s+line)', clean, re.IGNORECASE)
    if m1:
        text = m1.group(1).strip()
        line_num = int(m1.group(2) or m1.group(3))
        from assistant.system_tasks import write_to_screen_line
        write_to_screen_line(line_number=line_num, text=text)
        return True

    # 2. 'add on/to/at [the] line <N>' or '[the] <N>th line <text>'
    m2 = re.search(r'(?:add|write|insert|type|put)\s+(?:on|to|at|in)\s+(?:the\s+)?(?:line\s+(\d+)|(\d+)(?:st|nd|rd|th)?\s+line)\s+(?:a\s+)?(.+)', clean, re.IGNORECASE)
    if m2:
        line_num = int(m2.group(1) or m2.group(2))
        text = m2.group(3).strip()
        from assistant.system_tasks import write_to_screen_line
        write_to_screen_line(line_number=line_num, text=text)
        return True

    return False

# Shutting down is a whole-utterance decision. Matching the bare words
# "stop", "exit" or "quit" anywhere in a sentence used to end the session on
# "stop overwatch", "stop the music" and "exit fullscreen".
_QUIT_PATTERN = re.compile(
    r"^\s*(?:please\s+)?"
    r"(?:goodbye|bye|good\s*night|see\s+you|"
    r"(?:shut\s*down|close|kill|stop|exit|quit|terminate)|"
    r"(?:power|turn)\s+(?:off|down))"
    # Only an addressee that unambiguously means the assistant itself. "the app"
    # and "the window" are deliberately absent: those mean the foreground app.
    r"(?:\s+(?:yourself|jarvis|vave|the\s+assistant))?"
    r"\s*[.!]?\s*$",
    re.IGNORECASE,
)


def is_quit_command(command):
    """True only when the whole utterance asks VAVE itself to shut down."""
    return bool(_QUIT_PATTERN.match(str(command or "")))


def execute_command(command, auto_confirm=False):
    """
    Main command router for VAVE Version 1.2.
    Tries every fast-path handler first. Unknown commands fall through to the AI brain.
    """
    if is_quit_command(command):
        speak(f"Goodbye {get_setting('user_name', 'Sir')}.")
        return False

    return execute_single_command(command, auto_confirm=auto_confirm)


def execute_single_command(command, auto_confirm=False):
    """
    Routes an individual command to its appropriate handler or the AI brain.
    """
    if handle_model_switch_command(command):
        return True

    if handle_settings_command(command):
        return True
        
    if check_routines(command):
        return True

    if handle_volume_command(command):
        return True

    if handle_atomic_gui_command(command):
        return True

    if handle_read_screen_command(command):
        return True

    if handle_line_write_command(command):
        return True

    if handle_app_command(command):
        return True

    if open_website(command):
        return True

    if handle_web_interact_ai_command(command):
        return True

    if youtube_search(command):
        return True

    if google_search(command):
        return True

    # Developer Mode Commands
    if "commit my changes" in command or "commit my code" in command or "push my code" in command:
        push_code = "push" in command
        git_auto_commit_and_push(push=push_code)
        return True
    
    if "deep test the project" in command or "run deep test" in command or "test my code" in command:
        # Use default args or parse them if needed
        deep_test_project()
        return True
        
    if "scrape for ideas" in command or "project ideas" in command or "find templates" in command:
        scrape_project_ideas()
        return True
        
    if "scaffold" in command:
        # Give it directly to the LLM Brain to trigger scaffold_code
        ask_ai(command, auto_confirm=True)
        return True

    # Calendar Commands
    if "my schedule" in command or "read my calendar" in command or "upcoming events" in command:
        from assistant.calendar_sync import get_upcoming_events
        response = get_upcoming_events()
        speak(response)
        return True
        
    if "schedule a meeting" in command or "schedule an event" in command:
        ask_ai(command, auto_confirm=True)
        return True

    # Email Commands
    if "send an email" in command or "write an email" in command:
        ask_ai(command, auto_confirm=True)
        return True
        
    
    # Swarm & Deep Research Commands
    if "deep research" in command or "research this deeply" in command:
        ask_ai(command, auto_confirm=True)
        return True
        
    if "spawn agents" in command or "delegate" in command or "swarm" in command:
        ask_ai(command, auto_confirm=True)
        return True

    # Document RAG Commands
    if "ingest document" in command or "read textbook" in command or "read pdf" in command:
        ask_ai(command, auto_confirm=True)
        return True
        
    if "ask document" in command or "search knowledge base" in command:
        ask_ai(command, auto_confirm=True)
        return True

    # Briefing Command
    if "brief me" in command or "morning briefing" in command or "what is my briefing" in command:
        from assistant.system_tasks import provide_morning_briefing
        provide_morning_briefing()
        return True

    if re.search(r"\btime\b", command):
        tell_time()
    elif re.search(r"\bdate\b", command):
        tell_date()
    elif re.search(r"\bbattery\b", command):
        tell_battery()

    elif "screenshot" in command or "screen shot" in command:
        take_screenshot()
    elif "lock laptop" in command or "lock computer" in command or "lock screen" in command:
        lock_laptop()

    elif "shutdown" in command or "shut down" in command:
        try: guard.call(shutdown_laptop)
        except guard.ToolDenied: pass

    elif "restart" in command or "reboot" in command:
        try: guard.call(restart_laptop)
        except guard.ToolDenied: pass

    elif "take a note" in command or "take note" in command or "write a note" in command or "add note" in command:
        add_note()
    elif "read notes" in command or "read my notes" in command:
        read_notes()
    elif "clear notes" in command or "delete notes" in command:
        try: guard.call(clear_notes)
        except guard.ToolDenied: pass

    else:
        # Route unrecognized commands to the local LLM brain
        ask_ai(command, auto_confirm=auto_confirm)

    return True

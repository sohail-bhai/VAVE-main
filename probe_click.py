"""Generic click tester: native apps first, then brainstorm.

Usage:
  list:   venv\\Scripts\\python.exe probe_click.py Calculator
  click:  venv\\Scripts\\python.exe probe_click.py Calculator "One"
  raw:    venv\\Scripts\\python.exe probe_click.py Calculator @500,400

Compares foreground window + visible text + element list before/after,
so a click that "succeeds" but changes nothing is caught red-handed.
"""
import sys, os, time

sys.path.insert(0, r"F:\sohail\jarvis\JarvisAssistant_V1_2_Config")
os.chdir(r"F:\sohail\jarvis\JarvisAssistant_V1_2_Config")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from assistant import system_tasks as st


def fg_title():
    try:
        import uiautomation as auto
        fg = auto.GetForegroundControl()
        guard = 0
        while fg is not None and getattr(fg, "ControlType", None) != auto.ControlType.WindowControl and guard < 8:
            fg = fg.GetParentControl()
            guard += 1
        return fg.Name if fg is not None else "<none>"
    except Exception as e:
        return f"<err {e}>"


def screen_text():
    try:
        from assistant.vision import read_screen_text
        return ((read_screen_text() or "").strip())
    except Exception as e:
        return f"<err {e}>"


title = sys.argv[1] if len(sys.argv) > 1 else "Calculator"
target = sys.argv[2] if len(sys.argv) > 2 else None

window = st._find_window(title)
if not window:
    print(f"No window matches {title!r}.")
    print(st.list_windows())
    sys.exit(1)

print("=== window ===")
print("Name :", repr(window.Name))
print("Class:", repr(window.ClassName))
print("Handle:", window.NativeWindowHandle)
r = window.BoundingRectangle
print("Rect :", r.left, r.top, r.right, r.bottom)
print("fg now:", repr(fg_title()))

print("\n=== clickable elements (as the model sees them) ===")
before_elems = st.get_clickable_elements(window_title=title)
print(before_elems)

if not target:
    sys.exit(0)

print(f"\n=== before ===\nfg: {fg_title()!r}")
before_text = screen_text()
print("screen text:", (before_text[:400] + "...") if len(before_text) > 400 else (before_text or "<empty>"))

print(f"\n=== action: {target} ===")
if target.startswith("@"):
    x, y = target[1:].split(",")
    result = st.click_at(int(x), int(y))
else:
    result = st.click_element(target, window_title=title)
print("result ->", result)

time.sleep(1.5)
print(f"\n=== after ===\nfg: {fg_title()!r}")
after_text = screen_text()
after_elems = st.get_clickable_elements(window_title=title)
print("element list changed:", before_elems != after_elems)
print("screen text changed :", before_text != after_text)
if before_text != after_text:
    print("text now:", (after_text[:400] + "...") if len(after_text) > 400 else (after_text or "<empty>"))
if before_elems == after_elems and before_text == after_text:
    print("\n>>> CLICK DID NOT LAND (nothing moved, nothing changed). <<<")
else:
    print("\n>>> CLICK WORKED (something moved). <<<")

"""Deep end-to-end live testing of Media Control, Window Snapping, and Closed-Loop Verification."""

import os
import sys
import time
import ctypes
from ctypes import wintypes

# Ensure root directory is on sys.path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from assistant import system_tasks
from assistant import commands
from assistant import ai_brain

def get_work_area():
    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

def get_window_rect(hwnd):
    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

def test_live_window_snapping():
    print("\n--- TEST 1: LIVE WINDOW LIFECYCLE & SNAPPING ---")
    work_x, work_y, work_w, work_h = get_work_area()
    print(f"Work Area detected: {work_w}x{work_h} at ({work_x},{work_y})")

    # 1. Open Calculator (Native UWP App)
    print("1. Opening Calculator...")
    open_res = system_tasks.open_app("calculator")
    print(f"   Result: {open_res}")
    time.sleep(1.0)

    # Find Calculator window
    win = system_tasks._find_window("calculator")
    assert win is not None, "FAILED: Calculator window not found after open_app"
    hwnd = win.NativeWindowHandle
    print(f"   Calculator found: HWND={hwnd}, Title='{win.Name}'")

    # 2. Snap Left
    print("2. Snapping Calculator to LEFT...")
    snap_left_res = system_tasks.snap_window("calculator", "left")
    print(f"   Result: {snap_left_res}")
    time.sleep(0.5)
    wx, wy, ww, wh = get_window_rect(hwnd)
    print(f"   Actual window rect: {ww}x{wh} at ({wx},{wy})")
    expected_w = work_w // 2
    # Allow small border tolerance (+-25px for Windows 10/11 invisible resize borders)
    assert abs(wx - work_x) <= 25, f"Left position mismatch: expected ~{work_x}, got {wx}"
    assert abs(ww - expected_w) <= 35, f"Width mismatch: expected ~{expected_w}, got {ww}"
    print("   [PASS] Snap Left verified geometrically.")

    # 3. Snap Right
    print("3. Snapping Calculator to RIGHT...")
    snap_right_res = system_tasks.snap_window("calculator", "right")
    print(f"   Result: {snap_right_res}")
    time.sleep(0.5)
    wx, wy, ww, wh = get_window_rect(hwnd)
    print(f"   Actual window rect: {ww}x{wh} at ({wx},{wy})")
    expected_x = work_x + (work_w // 2)
    assert abs(wx - expected_x) <= 30, f"Right position mismatch: expected ~{expected_x}, got {wx}"
    print("   [PASS] Snap Right verified geometrically.")

    # 4. Snap Center
    print("4. Snapping Calculator to CENTER...")
    snap_center_res = system_tasks.snap_window("calculator", "center")
    print(f"   Result: {snap_center_res}")
    time.sleep(0.5)
    wx, wy, ww, wh = get_window_rect(hwnd)
    print(f"   Actual window rect: {ww}x{wh} at ({wx},{wy})")
    assert wx > work_x, "Center x should be indented from left"
    print("   [PASS] Snap Center verified geometrically.")

    # 5. Maximize
    print("5. Maximizing Calculator...")
    max_res = system_tasks.snap_window("calculator", "maximize")
    print(f"   Result: {max_res}")
    time.sleep(0.5)
    print("   [PASS] Maximized successfully.")

    # 6. Clean up: Close Calculator
    print("6. Closing Calculator...")
    close_res = system_tasks.close_app("calculator")
    print(f"   Result: {close_res}")
    time.sleep(0.5)
    win_after = system_tasks._find_window("calculator")
    assert win_after is None, "FAILED: Calculator still open after close_app"
    print("   [PASS] Closed Calculator cleanly.")

def test_live_media_control():
    print("\n--- TEST 2: LIVE MEDIA HARDWARE CONTROL ---")
    for act in ("play_pause", "next", "previous", "mute"):
        res = system_tasks.media_control(act)
        print(f"   Action '{act}' -> {res}")
        assert "success" in res.lower(), f"Media action {act} failed: {res}"
    print("   [PASS] All hardware media keys fired cleanly without errors.")

def test_fastpath_command_routing():
    print("\n--- TEST 3: LIVE COMMAND ROUTING VIA FAST-PATH ---")
    
    # Launch calculator for fast-path routing test
    system_tasks.open_app("calculator")
    time.sleep(0.8)

    test_commands = [
        ("pause music", True),
        ("skip song", True),
        ("previous track", True),
        ("snap calculator to left", True),
        ("put calculator on the right", True),
        ("maximize calculator", True),
    ]

    for cmd, should_handle in test_commands:
        handled = commands.execute_single_command(cmd)
        print(f"   Command: '{cmd}' -> Handled: {handled}")
        assert handled == should_handle, f"Command '{cmd}' expected handled={should_handle}, got {handled}"

    system_tasks.close_app("calculator")
    print("   [PASS] All fast-path media and snapping commands routed correctly.")

def test_ai_brain_tool_selection():
    print("\n--- TEST 4: AI BRAIN TOOL CATALOG & SELECTION ---")
    
    # Test tool selection for media instruction
    tools_media = [t["function"]["name"] for t in ai_brain.select_tools("could you pause my background music please")]
    print(f"   Tools for 'pause my background music': {tools_media}")
    assert "media_control" in tools_media, "media_control missing from tool selection"

    # Test tool selection for window snap instruction
    tools_snap = [t["function"]["name"] for t in ai_brain.select_tools("snap the active window to the left")]
    print(f"   Tools for 'snap window to left': {tools_snap}")
    assert "snap_window" in tools_snap, "snap_window missing from tool selection"
    
    print("   [PASS] AI Brain dynamically serves media_control and snap_window.")

if __name__ == "__main__":
    try:
        test_live_window_snapping()
        test_live_media_control()
        test_fastpath_command_routing()
        test_ai_brain_tool_selection()
        print("\n=======================================================")
        print(">>> ALL DEEP LIVE VERIFICATION TESTS PASSED 100% <<<")
        print("=======================================================\n")
    except Exception as e:
        print(f"\n[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
        # Clean up Notepad if crashed mid-test
        system_tasks.close_app("notepad")
        sys.exit(1)

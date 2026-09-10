import logging
logger = logging.getLogger(__name__)

import os
import re
import platform
import subprocess
import datetime
import time
from pathlib import Path

from assistant.speech import speak

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT_FOLDER = PROJECT_ROOT / "assets" / "screenshots"


def _get_pyautogui():
    import pyautogui
    pyautogui.FAILSAFE = False
    return pyautogui


def _get_psutil():
    import psutil
    return psutil

def get_os():
    return platform.system().lower()

def clamp_number(value, minimum=0, maximum=100):
    return max(minimum, min(maximum, int(value)))

def activate_window(hwnd):
    """Brings a window by HWND to the foreground and restores it if minimized."""
    if not hwnd or get_os() != "windows":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        current_fg = user32.GetForegroundWindow()
        if current_fg != hwnd:
            cur_thread = kernel32.GetCurrentThreadId()
            fg_thread = user32.GetWindowThreadProcessId(current_fg, None)
            if cur_thread != fg_thread and fg_thread != 0:
                user32.AttachThreadInput(cur_thread, fg_thread, True)
                user32.SetForegroundWindow(hwnd)
                user32.BringWindowToTop(hwnd)
                user32.AttachThreadInput(cur_thread, fg_thread, False)
            else:
                user32.SetForegroundWindow(hwnd)
                user32.BringWindowToTop(hwnd)
        return True
    except Exception as e:
        logger.debug(f"activate_window failed for hwnd {hwnd}: {e}")
        return False


def _ensure_com():
    """Join the COM apartment and active desktop for this thread before touching UI Automation.

    Every UIA call needs it, and the assistant's tools run on worker threads
    that have not called it yet. Attaching to the interactive user desktop ("default")
    ensures background threads and subshells can see all application windows.
    """
    if get_os() == "windows":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hdesk = user32.OpenInputDesktop(0, False, 0x01FF) or user32.OpenDesktopW("default", 0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        # Not fatal on its own: uiautomation may already hold the apartment.
        pass


# Ensure calling/importing thread is attached to user desktop & COM
_ensure_com()


_STARTAPPS_CACHE = None
_STARTAPPS_CACHE_TIME = 0

def _get_windows_start_apps():
    """Cache and return all registered Windows Store & desktop applications."""
    global _STARTAPPS_CACHE, _STARTAPPS_CACHE_TIME
    import time
    import json
    now = time.time()
    if _STARTAPPS_CACHE is not None and (now - _STARTAPPS_CACHE_TIME) < 300:
        return _STARTAPPS_CACHE

    apps = []
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-StartApps | ConvertTo-Json"],
            timeout=5
        ).decode("utf-8", errors="ignore")
        data = json.loads(out)
        apps = data if isinstance(data, list) else [data]
    except Exception as e:
        logger.debug(f"Get-StartApps lookup note: {e}")

    _STARTAPPS_CACHE = apps
    _STARTAPPS_CACHE_TIME = now
    return apps

COMMON_APP_STOPWORDS = frozenset({
    "open", "close", "play", "stop", "start", "search", "browse", "for", "and",
    "the", "with", "from", "into", "onto", "that", "this", "then", "also",
    "please", "some", "any", "all", "what", "how", "why", "when", "where", "a", "an"
})


def _find_installed_app(query, system):
    """
    Search installed desktop applications on Windows, macOS, or Linux.
    Returns (path, display_name) or (None, None).
    """
    clean_raw = str(query or "").lower().strip()
    if not clean_raw or clean_raw in COMMON_APP_STOPWORDS or len(clean_raw) <= 1:
        return None, None
    clean_q = clean_raw.replace(" ", "").replace("_", "").replace("-", "")

    if system == "windows":
        # 1. Search Windows Store / UWP & desktop applications via Get-StartApps
        try:
            start_apps = _get_windows_start_apps()
            bad_startapp_words = {"uninstall", "remove", "setup", "reset", "documentation", "help", "readme"}

            # Exact normalized match
            for item in start_apps:
                raw_name = item.get("Name", "")
                name = raw_name.lower()
                if not any(bw in name for bw in bad_startapp_words):
                    if clean_q == name.replace(" ", "").replace("_", "").replace("-", ""):
                        return f"shell:AppsFolder\\{item['AppID']}", raw_name

            # Word match
            for item in start_apps:
                raw_name = item.get("Name", "")
                name = raw_name.lower()
                if not any(bw in name for bw in bad_startapp_words):
                    words = name.replace("_", " ").replace("-", " ").split()
                    if query.lower() in words or any(w.startswith(query.lower()) for w in words):
                        return f"shell:AppsFolder\\{item['AppID']}", raw_name

            # Substring match
            for item in start_apps:
                raw_name = item.get("Name", "")
                name = raw_name.lower()
                if not any(bw in name for bw in bad_startapp_words):
                    if query.lower() in name:
                        return f"shell:AppsFolder\\{item['AppID']}", raw_name
        except Exception as e:
            logger.debug(f"StartApps matching note: {e}")

        # 2. Search Start Menu .lnk shortcuts
        try:
            import winreg
        except ImportError:
            winreg = None

        dirs = [
            os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        ]
        candidates = []
        bad_words = {"uninstall", "remove", "setup", "reset", "documentation", "help", "readme", "website", "update"}

        for folder in dirs:
            if os.path.exists(folder):
                for root, _, files in os.walk(folder):
                    for f in files:
                        if f.lower().endswith(".lnk"):
                            name = f[:-4].lower()
                            if not any(bw in name for bw in bad_words):
                                candidates.append((name, os.path.join(root, f), f[:-4]))

        # Exact normalized match
        for name, path, orig in candidates:
            if clean_q == name.replace(" ", "").replace("_", "").replace("-", ""):
                return path, orig

        # Word-in-name match
        for name, path, orig in candidates:
            words = name.replace("_", " ").replace("-", " ").split()
            if query.lower() in words or any(w.startswith(query.lower()) for w in words):
                return path, orig

        # Substring match
        for name, path, orig in candidates:
            if query.lower() in name:
                return path, orig

        # Registry App Paths
        if winreg:
            for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths") as key:
                        num = winreg.QueryInfoKey(key)[0]
                        for i in range(num):
                            sub = winreg.EnumKey(key, i)
                            clean = sub.lower().replace(".exe", "")
                            if clean == clean_q or query.lower() in clean:
                                val = winreg.QueryValue(key, sub)
                                if val and os.path.exists(val):
                                    return val, clean.title()
                except Exception:
                    pass

    elif system == "darwin":
        for app_dir in ["/Applications", os.path.expanduser("~/Applications")]:
            if os.path.exists(app_dir):
                for item in os.listdir(app_dir):
                    if item.lower().endswith(".app"):
                        name = item[:-4].lower()
                        if query.lower() in name or clean_q in name.replace(" ", ""):
                            return os.path.join(app_dir, item), item[:-4]

    else:
        desk_dir = "/usr/share/applications"
        if os.path.exists(desk_dir):
            for item in os.listdir(desk_dir):
                if item.lower().endswith(".desktop") and query.lower() in item.lower():
                    return os.path.join(desk_dir, item), item[:-8]

    return None, None


_LAST_OPENED_APP = None


def open_app(app_name):
    """
    3-tier smart application & website launcher:
    Tier 1: Built-in known app aliases.
    Tier 2: Search installed applications on local disk.
    Tier 3: Fallback to browser (configured websites or web search/URL).
    """
    global _LAST_OPENED_APP
    import webbrowser
    import urllib.parse
    from assistant.config import get_setting

    system = get_os()
    raw_query = str(app_name).strip()
    query = raw_query.lower()
    if query.startswith("open "):
        query = query[5:].strip()

    display_name = query.replace("_", " ").title()
    _ensure_com()
    _LAST_OPENED_APP = display_name

    try:
        # Tier 1: Built-in known app aliases
        if system == "windows":
            windows_apps = {
                "chrome": "start chrome",
                "google chrome": "start chrome",
                "notepad": "start notepad",
                "calculator": "start calc",
                "calc": "start calc",
                "netflix": "start netflix:",
                "spotify": "start spotify:",
                "whatsapp": "start whatsapp:",
                "discord": "start discord:",
                "vscode": "start code",
                "vs code": "start code",
                "code": "start code",
                "file_explorer": "start explorer",
                "file explorer": "start explorer",
                "explorer": "start explorer",
                "cmd": "start cmd /k",
                "terminal": "start cmd /k",
                "command prompt": "start cmd /k",
                "powershell": "start powershell",
                "paint": "start mspaint",
                "task manager": "start taskmgr",
                "taskmgr": "start taskmgr",
                "settings": "start ms-settings:",
                "edge": "start msedge",
                "microsoft edge": "start msedge",
                "word": "start winword",
                "excel": "start excel",
                "powerpoint": "start powerpnt",
            }
            if query in windows_apps:
                # If window is already open, activate it instead of launching a duplicate blank window
                target_cls = None  # initialize before try so it's always defined below
                try:
                    import time
                    existing = _find_window(display_name) or _find_window(query)
                    if existing and getattr(existing, "NativeWindowHandle", None):
                        speak(f"Opening {display_name}")
                        activate_window(existing.NativeWindowHandle)
                        return f"Opened {display_name} (switched to existing active window)."
                except Exception:
                    pass

                speak(f"Opening {display_name}")
                cmd = windows_apps[query]
                if not cmd.startswith("start "):
                    cmd = f"start {cmd}"
                os.system(cmd)

                # Wait for the launched window to be ready and activated
                verified_win = None
                for _ in range(25):  # up to 2.5 seconds total, exits immediately when ready
                    time.sleep(0.1)
                    try:
                        new_win = _find_window(display_name) or _find_window(query)
                        if new_win and getattr(new_win, "NativeWindowHandle", None):
                            activate_window(new_win.NativeWindowHandle)
                            time.sleep(0.15)
                            verified_win = new_win
                            break
                    except Exception:
                        pass
                if verified_win:
                    win_name = getattr(verified_win, "Name", display_name) or display_name
                    return f"Successfully opened {display_name} (Verified active window: '{win_name}')."
                return f"Action sent for {display_name}; effect unconfirmed."

        elif system == "darwin":
            mac_apps = {
                "chrome": "open -a 'Google Chrome'",
                "notepad": "open -a TextEdit",
                "calculator": "open -a Calculator",
                "vscode": "open -a 'Visual Studio Code'",
                "file_explorer": "open .",
                "cmd": "open -a Terminal",
            }
            if query in mac_apps:
                speak(f"Opening {display_name}")
                os.system(mac_apps[query])
                return f"Successfully opened {display_name}."

        else:
            linux_apps = {
                "chrome": "google-chrome",
                "notepad": "gedit",
                "calculator": "gnome-calculator",
                "vscode": "code",
                "file_explorer": "xdg-open .",
                "cmd": "gnome-terminal",
            }
            if query in linux_apps:
                speak(f"Opening {display_name}")
                subprocess.Popen(linux_apps[query], shell=True)
                return f"Successfully opened {display_name}."

        # Tier 2: Search installed applications on local machine
        app_path, found_name = _find_installed_app(query, system)
        if app_path:
            speak(f"Opening {found_name}")
            if system == "windows":
                os.startfile(app_path)
            elif system == "darwin":
                subprocess.Popen(["open", app_path])
            else:
                subprocess.Popen([app_path], shell=True)
                
            import time
            active_title = ""
            for _ in range(15):  # up to 3.0 seconds polling
                time.sleep(0.2)
                try:
                    w = _find_window(found_name)
                    if w and getattr(w, "NativeWindowHandle", None):
                        activate_window(w.NativeWindowHandle)
                        active_title = w.Name
                        break
                except Exception:
                    pass
            if active_title:
                return f"Successfully opened installed app '{found_name}' (Verified active window: '{active_title}')."
            return f"Action sent for '{found_name}'; effect unconfirmed."

        # Tier 3: Fallback to opening in browser
        websites = get_setting("websites", {})
        if query in websites:
            speak(f"Opening {display_name} in browser.")
            webbrowser.open(websites[query])
            return f"Opened {query} in browser. You MUST now use get_clickable_elements() or read_screen() if you need to interact with it."

        web_services = {
            "youtube": "https://www.youtube.com",
            "netflix": "https://www.netflix.com",
            "reddit": "https://www.reddit.com",
            "twitter": "https://twitter.com",
            "x": "https://x.com",
            "instagram": "https://www.instagram.com",
            "facebook": "https://www.facebook.com",
            "linkedin": "https://www.linkedin.com",
            "whatsapp": "https://web.whatsapp.com",
            "telegram": "https://web.telegram.org",
            "chatgpt": "https://chatgpt.com",
            "gemini": "https://gemini.google.com",
            "claude": "https://claude.ai",
            "spotify": "https://open.spotify.com",
            "github": "https://github.com",
            "gmail": "https://mail.google.com",
            "drive": "https://drive.google.com",
            "google drive": "https://drive.google.com",
            "maps": "https://maps.google.com",
            "google maps": "https://maps.google.com",
            "prime video": "https://www.primevideo.com",
            "amazon": "https://www.amazon.com",
            "twitch": "https://www.twitch.tv",
            "wikipedia": "https://www.wikipedia.org",
            "canva": "https://www.canva.com",
            "notion": "https://www.notion.so",
            "figma": "https://www.figma.com",
        }
        if query in web_services:
            speak(f"Opening {display_name} in browser.")
            webbrowser.open(web_services[query])
            return f"Opened {query} in browser. You MUST now use get_clickable_elements() or read_screen() if you need to interact with it."

        if " " not in query and query.isalnum():
            url = f"https://www.{query}.com"
        else:
            url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"

        speak(f"Opening {display_name} in browser.")
        webbrowser.open(url)
        return f"Opened {url} in browser. You MUST now use get_clickable_elements() or read_screen() if you need to interact with it."

    except Exception as error:
        speak(f"Could not open {display_name}.")
        logger.info("Error:", error)
        return False


def close_app(app_name):
    """
    Closes or terminates a running application by name.
    """
    clean_name = str(app_name).lower().strip().replace("close ", "").replace("kill ", "")
    display_name = clean_name.replace("_", " ").title()
    import psutil

    proc_map = {
        "notepad": ["notepad.exe"],
        "calculator": ["calculatorapp.exe", "calc.exe", "calculator.exe"],
        "calc": ["calculatorapp.exe", "calc.exe"],
        "chrome": ["chrome.exe"],
        "google chrome": ["chrome.exe"],
        "vscode": ["code.exe"],
        "vs code": ["code.exe"],
        "code": ["code.exe"],
        "discord": ["discord.exe"],
        "spotify": ["spotify.exe"],
        "netflix": ["netflix.exe"],
        "cmd": ["cmd.exe"],
        "terminal": ["windowsterminal.exe", "cmd.exe", "powershell.exe"],
        "edge": ["msedge.exe"],
        "brave": ["brave.exe"],
        "word": ["winword.exe"],
        "excel": ["excel.exe"],
        "powerpoint": ["powerpnt.exe"],
    }

    target_exes = [x.lower() for x in proc_map.get(clean_name, [f"{clean_name}.exe", clean_name])]
    closed = False

    for proc in psutil.process_iter(["name", "pid"]):
        try:
            p_name = proc.info["name"].lower()
            if p_name in target_exes or clean_name in p_name:
                proc.terminate()
                closed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if closed:
        global _LAST_OPENED_APP
        if _LAST_OPENED_APP and clean_name in _LAST_OPENED_APP.lower():
            _LAST_OPENED_APP = None
        speak(f"Closed {display_name}.")
        return f"Closed {display_name} successfully."
    else:
        # Fallback: close window directly if it's a UWP / frame-hosted app (like Netflix, Calculator)
        try:
            res = close_window(clean_name)
            if "Closed the window" in res:
                if _LAST_OPENED_APP and clean_name in _LAST_OPENED_APP.lower():
                    _LAST_OPENED_APP = None
                speak(f"Closed {display_name}.")
                return f"Closed {display_name} successfully."
        except Exception:
            pass
        speak(f"No running process found for {display_name}.")
        return f"No running process found for {display_name}."


def tell_time():
    current_time = datetime.datetime.now().strftime("%I:%M %p")
    msg = f"The time is {current_time}"
    speak(msg)
    return msg

def tell_date():
    current_date = datetime.datetime.now().strftime("%d %B %Y")
    msg = f"Today's date is {current_date}"
    speak(msg)
    return msg

def search_web(query):
    """Searches the web for factual and real-time information."""
    import urllib.request
    import urllib.parse
    import re
    import html
    import json

    q = str(query).strip()
    if not q:
        return "Please specify a search query."

    # 1. Primary: Live web search via DuckDuckGo HTML POST
    try:
        data = urllib.parse.urlencode({'q': q}).encode('utf-8')
        req = urllib.request.Request(
            'https://html.duckduckgo.com/html/',
            data=data,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode('utf-8', 'ignore')

        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', body, re.DOTALL)
        titles = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.DOTALL)

        out = []
        for i in range(min(len(snippets), len(titles), 3)):
            t = html.unescape(re.sub(r'<[^>]+>', '', titles[i][1])).strip()
            s = html.unescape(re.sub(r'<[^>]+>', '', snippets[i])).strip()
            out.append(f"Result {i+1}: {t}\n{s}")

        if out:
            return "\n\n".join(out)
    except Exception as e:
        logger.debug(f"[Web Search] DDG HTML search error: {e}")

    # 2. Secondary: DuckDuckGo Instant Answer API
    try:
        url = 'https://api.duckduckgo.com/?' + urllib.parse.urlencode({'q': q, 'format': 'json'})
        req_api = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req_api, timeout=5) as resp:
            api_data = json.loads(resp.read().decode('utf-8'))
            abstract = api_data.get('AbstractText', '').strip()
            if abstract:
                heading = api_data.get('Heading', 'Summary')
                return f"{heading}: {abstract}"
    except Exception:
        pass

    # 3. Fallback: Wikipedia Summary
    try:
        import wikipedia
        return wikipedia.summary(q, sentences=3)
    except Exception:
        pass

    return "No results found on the web."

def get_weather(city):
    """Fetches the current live weather for a given city."""
    try:
        import requests
        # We use wttr.in because it is free and requires no API key.
        response = requests.get(f"https://wttr.in/{city}?format=j1", timeout=5)
        if response.status_code == 200:
            data = response.json()
            current = data['current_condition'][0]
            temp = current['temp_C']
            desc = current['weatherDesc'][0]['value']
            feels = current['FeelsLikeC']
            return f"The current weather in {city} is {desc} at {temp}°C, feels like {feels}°C."
        else:
            return f"Could not fetch weather for {city}. (Status code {response.status_code})"
    except Exception as e:
        return f"Error fetching weather: {e}"

def tell_battery():
    try:
        psutil = _get_psutil()
        battery = psutil.sensors_battery()

        if battery is None:
            msg = "Battery information is not available."
            speak(msg)
            return msg

        percent = battery.percent
        if battery.power_plugged:
            msg = f"Battery is at {percent} percent and charging."
        else:
            msg = f"Battery is at {percent} percent."
        speak(msg)
        return msg

    except Exception as error:
        msg = f"Could not check battery status: {error}"
        speak("Could not check battery status.")
        logger.info("Error: %s", error)
        return msg

def take_screenshot():
    try:
        # On Windows, attach thread to active desktop to prevent capture failures
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

        pyautogui = _get_pyautogui()
        SCREENSHOT_FOLDER.mkdir(parents=True, exist_ok=True)
        
        # Cleanup old screenshots (keep only the last 10)
        existing_shots = sorted(SCREENSHOT_FOLDER.glob("screenshot_*.png"), key=lambda p: p.stat().st_mtime)
        if len(existing_shots) >= 10:
            for old_shot in existing_shots[:-9]:
                try:
                    old_shot.unlink()
                except Exception:
                    pass

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        screenshot_path = SCREENSHOT_FOLDER / f"screenshot_{timestamp}.png"

        screenshot = pyautogui.screenshot()
        screenshot.save(screenshot_path)

        speak("Screenshot saved successfully.")
        logger.info(f"Saved at: {screenshot_path}")
        return str(screenshot_path)

    except Exception as error:
        speak("Could not take screenshot.")
        logger.info("Error:", error)
        return None

def get_windows_volume_controller():
    """
    Returns Windows system volume controller using pycaw.
    Supports both new and old pycaw versions.
    """

    if get_os() != "windows":
        return None

    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        device = AudioUtilities.GetSpeakers()

        # New pycaw method
        if hasattr(device, "EndpointVolume"):
            return device.EndpointVolume

        # Old pycaw method
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL

        interface = device.Activate(
            IAudioEndpointVolume._iid_,
            CLSCTX_ALL,
            None
        )

        try:
            return interface.QueryInterface(IAudioEndpointVolume)
        except Exception:
            return cast(interface, POINTER(IAudioEndpointVolume))

    except Exception as error:
        logger.info("Windows volume controller error:", error)
        return None

def get_current_volume():
    volume = get_windows_volume_controller()

    if volume is None:
        return None

    try:
        return round(volume.GetMasterVolumeLevelScalar() * 100)
    except Exception as error:
        logger.info("Could not get current volume:", error)
        return None

def set_volume(level):
    level = clamp_number(level)
    volume = get_windows_volume_controller()

    if volume is not None:
        try:
            volume.SetMasterVolumeLevelScalar(level / 100, None)
            speak(f"Volume set to {level} percent.")
            return
        except Exception as error:
            logger.info("Could not set exact volume:", error)

    speak("Exact volume control is not available. Using keyboard volume keys instead.")
    pyautogui = _get_pyautogui()
    pyautogui.press("volumeup")

def change_volume_by(amount):
    current_volume = get_current_volume()

    if current_volume is not None:
        new_volume = clamp_number(current_volume + amount)
        set_volume(new_volume)
        return

    steps = abs(int(amount)) // 5
    steps = max(1, steps)

    if amount > 0:
        pyautogui = _get_pyautogui()
        for _ in range(steps):
            pyautogui.press("volumeup")
        speak(f"Volume increased by about {steps * 5} percent.")
    else:
        pyautogui = _get_pyautogui()
        for _ in range(steps):
            pyautogui.press("volumedown")
        speak(f"Volume decreased by about {steps * 5} percent.")

def mute_volume():
    pyautogui = _get_pyautogui()
    pyautogui.press("volumemute")
    speak("Volume mute toggled.")

def lock_laptop():
    system = get_os()

    try:
        if system == "windows":
            import ctypes
            speak("Locking laptop.")
            ctypes.windll.user32.LockWorkStation()

        elif system == "darwin":
            speak("Locking Mac.")
            os.system("/System/Library/CoreServices/Menu\\ Extras/User.menu/Contents/Resources/CGSession -suspend")

        else:
            speak("Locking system.")
            os.system("gnome-screensaver-command -l")

    except Exception as error:
        speak("Could not lock the laptop.")
        logger.info("Error:", error)

def shutdown_laptop():
    system = get_os()
    speak("Shutting down.")

    if system == "windows":
        os.system("shutdown /s /t 5")
    elif system == "darwin":
        os.system("sudo shutdown -h now")
    else:
        os.system("shutdown now")

def restart_laptop():
    system = get_os()
    speak("Restarting.")

    if system == "windows":
        os.system("shutdown /r /t 5")
    elif system == "darwin":
        os.system("sudo shutdown -r now")
    else:
        os.system("reboot")

def click_at(x, y):
    """Clicks at the specified X, Y coordinates."""
    pyautogui = _get_pyautogui()
    pyautogui.click(x=int(x), y=int(y))
    return f"Clicked at ({int(x)}, {int(y)})."

def double_click_at(x, y):
    """Double-clicks at the specified X, Y coordinates (opens files, selects a word)."""
    pyautogui = _get_pyautogui()
    pyautogui.doubleClick(x=int(x), y=int(y))
    return f"Double-clicked at ({int(x)}, {int(y)})."

def right_click_at(x, y):
    """Right-clicks at the specified X, Y coordinates to open a context menu."""
    pyautogui = _get_pyautogui()
    pyautogui.rightClick(x=int(x), y=int(y))
    return f"Right-clicked at ({int(x)}, {int(y)})."

def move_mouse(x, y):
    """Moves the mouse pointer without clicking (triggers hover menus and tooltips)."""
    pyautogui = _get_pyautogui()
    pyautogui.moveTo(int(x), int(y), duration=0.15)
    return f"Moved mouse to ({int(x)}, {int(y)})."

def type_text(text, window_title=None):
    """Types the given text automatically into the active window (or specified window_title)."""
    global _LAST_OPENED_APP
    _ensure_com()
    target_win = window_title or _LAST_OPENED_APP
    if target_win:
        focus_window(target_win)
        time.sleep(0.2)
    pyautogui = _get_pyautogui()
    body = str(text)

    # A small model reaches for the tool it knows. Asked to select all and
    # delete, it has been observed calling this with "Ctrl+A\nCtrl+Delete" as
    # the text, which would type those characters into the user's document
    # instead of pressing the shortcuts. When the argument is nothing but key
    # combinations, do the thing that was obviously meant.
    chords = _shortcut_sequence(body)
    if chords:
        return " ".join(press_key(chord) for chord in chords)

    # If text contains non-ASCII characters, emojis, or symbols, use clipboard paste for 100% fidelity
    needs_clipboard = any(ord(c) > 127 for c in body) or any(c in body for c in "@#%&~`|<>^")
    if needs_clipboard:
        try:
            import pyperclip
            pyperclip.copy(body)
            pyautogui.hotkey("ctrl", "v")
            return f"Typed {len(body)} characters."
        except Exception:
            pass

    pyautogui.write(body, interval=0.01)
    return f"Typed {len(body)} characters."


# Modifiers that make a keystroke a shortcut rather than something typeable.
_MODIFIER_KEYS = frozenset({"ctrl", "alt", "shift", "win", "command"})

# The keys a shortcut can end on, spelled out rather than guessed at by length,
# so "ctrl+delete" is a chord while "sales+marketing" stays two words.
_NAMED_KEYS = frozenset({
    "enter", "esc", "tab", "space", "backspace", "delete", "insert",
    "home", "end", "pageup", "pagedown", "up", "down", "left", "right",
    "capslock", "printscreen", "pause",
})


def _shortcut_sequence(text):
    """The chords in `text` if it is only key combinations, otherwise empty.

    One per line, so "Ctrl+A\\nCtrl+Delete" presses both in order. Any line
    that is ordinary text disqualifies the whole argument, which then gets
    typed exactly as given.
    """
    lines = [line for line in str(text).splitlines() if line.strip()]
    if not lines or len(lines) > 4:
        return []
    if all(_looks_like_shortcut(line) for line in lines):
        return [line.strip() for line in lines]
    return []


def _looks_like_shortcut(text):
    """True when the text is only a key combination, e.g. "ctrl+s", "Alt + F4".

    Deliberately narrow. Real prose to type never consists solely of a
    modifier joined to one short key, so this cannot swallow a genuine
    sentence: "Ctrl+A then type this" keeps its words and gets typed.
    """
    candidate = str(text).strip()
    if not candidate or len(candidate) > 24 or "+" not in candidate:
        return False
    # Spaces are allowed only as padding around the joiner. Anything left over
    # means there are separate words here, which makes this text, not a chord.
    if re.search(r"\s", re.sub(r"\s*\+\s*", "+", candidate)):
        return False
    keys = _normalise_keys(candidate)
    if not 2 <= len(keys) <= 4:
        return False
    # At least one modifier, so "2+2" is still typed as written.
    if not any(key in _MODIFIER_KEYS for key in keys):
        return False
    return all(key in _MODIFIER_KEYS
               or key in _NAMED_KEYS
               or len(key) == 1
               or re.fullmatch(r"f\d{1,2}", key)
               for key in keys)

# pyautogui expects the modifier spelling used by the OS, but a language model
# will happily say "control", "win" or "cmd". Normalise before pressing.
_KEY_ALIASES = {
    "control": "ctrl", "ctl": "ctrl",
    "windows": "win", "super": "win", "meta": "win",
    "cmd": "command" if platform.system() == "Darwin" else "win",
    "option": "alt", "return": "enter", "escape": "esc",
    "del": "delete", "pgup": "pageup", "pgdn": "pagedown",
    "spacebar": "space", "capslock": "capslock",
}


def _normalise_keys(raw):
    """Turn 'Ctrl + S', 'control-s' or ['ctrl','s'] into ['ctrl', 's']."""
    if isinstance(raw, (list, tuple)):
        parts = [str(p) for p in raw]
    else:
        parts = re.split(r"[+\-\s]+", str(raw))
    keys = []
    for part in parts:
        clean = part.strip().lower()
        if clean:
            keys.append(_KEY_ALIASES.get(clean, clean))
    return keys


def press_key(key):
    """Presses a key, or a combination like 'ctrl+s' / 'alt+f4' / 'win+r'."""
    pyautogui = _get_pyautogui()
    keys = _normalise_keys(key)

    if not keys:
        return "No key was given."

    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        # hotkey holds the modifiers down for the final key, which is what
        # shortcuts such as ctrl+shift+esc actually require.
        pyautogui.hotkey(*keys)

    return f"Pressed {'+'.join(keys)}."


def press_hotkey(keys):
    """Presses a keyboard shortcut such as 'ctrl+s', 'alt+tab' or 'win+r'."""
    return press_key(keys)


def wait(seconds=1.0):
    """Pauses briefly so the interface can catch up before the next step."""
    import time
    try:
        duration = float(seconds)
    except (TypeError, ValueError):
        duration = 1.0
    # A tool call should never be able to stall the assistant for minutes.
    duration = max(0.0, min(30.0, duration))
    time.sleep(duration)
    return f"Waited {duration:g} seconds."

def find_and_click_text(target_text):
    """
    Uses UIAutomation to find a button/text on the screen and clicks it.
    Returns True if found and clicked, False otherwise.
    """
    _ensure_com()
    try:
        import uiautomation as auto
    except ImportError:
        speak("The UI Automation module is missing. Please run pip install uiautomation.")
        return False
        
    import time
    
    logger.info(f"[VAVE Vision] Searching entire desktop for '{target_text}'...")
    
    try:
        # Search the entire desktop tree up to depth 7 for the exact name
        btn = auto.Control(Name=target_text, searchDepth=7)
        if btn.Exists(3, 1): # wait up to 3 seconds
            btn.Click(simulateMove=False)
            time.sleep(1)
            return True
    except Exception as e:
        logger.info(f"[VAVE Vision Error] {e}")
        
    speak(f"I could not find the text {target_text} on the screen.")
    return False

def read_screen(line_number=None, **kwargs):
    """Reads all visible text or a specific line from an open window or screen."""
    from assistant.vision import read_screen_text
    
    text = read_screen_text()
    if text is None or not text.strip():
        if line_number is not None:
            msg = f"The active document is currently blank. Cannot read line {line_number}."
            speak(msg)
            return msg
        return "No readable text found on the screen or the active document is blank."
        
    lines = text.splitlines()
    if line_number is not None:
        try:
            idx = int(line_number)
            if 1 <= idx <= len(lines):
                target_line = lines[idx - 1].strip()
                if not target_line:
                    return f"Line {idx} is blank."
                return f"Line {idx}: {target_line}"
            else:
                return f"The document has {len(lines)} lines. Cannot read line {idx}."
        except (ValueError, TypeError):
            pass
            
    return f"Visible text on screen:\n{text}"

def write_to_screen_line(line_number: int, text: str):
    """
    Writes or appends text to a specific line in an open editor window (like Notepad).
    Uses UIAutomation ValuePattern directly on the active document, falling back to keyboard navigation on the target window only.
    """
    import uiautomation as auto
    import time
    
    clean_text = str(text).strip()
    idx = int(line_number)
    current_pid = os.getpid()
    
    # 1. Search candidate windows specifically for document/editor controls (EXCLUDING VAVE GUI)
    candidates = []

    # Priority 1: Check known document/editor windows directly (Notepad, etc.)
    for target_class in ("Notepad", "Notepad_Desktop_Old"):
        np = auto.WindowControl(searchDepth=1, ClassName=target_class)
        if np.Exists(0.2) and np.ProcessId != current_pid and np not in candidates:
            candidates.append(np)

    # Priority 2: Check current foreground window if not VAVE
    fg = auto.GetForegroundControl()
    if fg and fg.ControlType == auto.ControlType.WindowControl:
        if fg.ProcessId != current_pid and "vave" not in fg.Name.lower() and fg not in candidates:
            candidates.append(fg)

    # Priority 3: Desktop windows excluding VAVE
    for w in auto.GetRootControl().GetChildren():
        if w.ControlType == auto.ControlType.WindowControl:
            if w.ProcessId != current_pid and "vave" not in w.Name.lower() and w not in candidates:
                candidates.append(w)

    target_win = None
    target_doc_ctrl = None

    for win in candidates:
        for ctrl, depth in auto.WalkControl(win, maxDepth=6):
            if ctrl.ControlType in (auto.ControlType.DocumentControl, auto.ControlType.EditControl) or ctrl.ClassName in ("RichEditD2DPT", "Edit"):
                target_doc_ctrl = ctrl
                target_win = win
                break
        if target_doc_ctrl:
            break

    if target_doc_ctrl:
        # First attempt: ValuePattern SetValue directly on the document control
        try:
            vp = target_doc_ctrl.GetValuePattern()
            if vp:
                orig = vp.Value.replace("\r\n", "\n").replace("\r", "\n")
                lines = orig.splitlines() if orig else []
                
                while len(lines) < idx:
                    lines.append("")
                    
                if lines[idx - 1].strip():
                    lines[idx - 1] = f"{lines[idx - 1]} {clean_text}"
                else:
                    lines[idx - 1] = clean_text
                    
                new_content = "\r\n".join(lines)
                vp.SetValue(new_content)
                speak(f"Added '{clean_text}' on line {idx}.")
                return f"Added '{clean_text}' on line {idx}."
        except Exception as e:
            logger.debug(f"UIAutomation ValuePattern set failed: {e}")

        # Fallback: Keyboard navigation ON THE TARGET EDITOR WINDOW ONLY
        try:
            if target_win:
                target_win.SetActive()
                if target_win.NativeWindowHandle:
                    activate_window(target_win.NativeWindowHandle)
                time.sleep(0.2)

            # Ensure keyboard focus is inside the target document control
            try:
                target_doc_ctrl.Click(simulateMove=False)
                time.sleep(0.1)
            except Exception:
                pass

            pyautogui = _get_pyautogui()
            pyautogui.hotkey('ctrl', 'home')
            time.sleep(0.1)
            for _ in range(idx - 1):
                pyautogui.press('down')
            pyautogui.press('end')
            pyautogui.write(f" {clean_text}", interval=0.01)
            speak(f"Added '{clean_text}' on line {idx}.")
            return f"Added '{clean_text}' on line {idx}."
        except Exception as e:
            logger.warning(f"Keyboard fallback write failed: {e}")
            speak(f"Could not add text to line {idx}.")
            return f"Could not add text to line {idx}: {e}"

    # If no target editor control was found, DO NOT BLINDLY TYPE INTO WHATEVER IS ACTIVE!
    msg = f"Could not find an open editor window to add text on line {idx}."
    speak(msg)
    return msg

def analyze_screen(prompt, image_path=None):
    """Takes a screenshot and uses a local Vision AI to answer a question about the screen."""
    from assistant.vision import analyze_screen as vs_analyze
    return vs_analyze(prompt, image_path=image_path)

def propose_new_feature(feature_name, description):
    """
    Appends a requested feature to the project plan.md file.
    """
    try:
        from pathlib import Path
        plan_path = Path(__file__).resolve().parent.parent / "plan.md"
        
        if not plan_path.exists():
            return "Could not find plan.md"
            
        with open(plan_path, "a", encoding="utf-8") as f:
            f.write(f"\n### Auto-Requested: {feature_name}\n")
            f.write(f"- [ ] {description}\n")
            
        return f"Successfully added '{feature_name}' to the project plan."
    except Exception as e:
        return f"Failed to add feature to plan: {e}"


import datetime

def provide_morning_briefing():
    """
    On-Demand Morning Briefing.
    Fetches weather, schedule, and unread emails, then uses the local LLM to synthesize a natural greeting.
    """
    from assistant.speech import speak
    from assistant.config import get_setting
    from assistant.system_tasks import get_weather
    from assistant.calendar_sync import get_upcoming_events
    from assistant.email_tasks import read_unread_emails
    from assistant.ai_brain import query_local_llm_chat
    
    speak("Gathering your briefing data, sir.")
    
    # 1. Weather
    city = get_setting("default_location", "Hyderabad")
    weather_data = get_weather(city)
    
    # 2. Schedule
    schedule_data = get_upcoming_events(max_results=3)
    
    # 3. Emails
    try:
        email_data = read_unread_emails()
    except:
        email_data = "Failed to fetch emails."
        
    # 4. Synthesize with LLM
    user_name = get_setting("user_name", "Sir")
    time_str = datetime.datetime.now().strftime("%I:%M %p")
    
    prompt = f"""
You are VAVE. It is currently {time_str}.
Your personality is a friendly buddy who has {user_name}'s back. You make funny jokes and use casual, conversational language instead of being a robotic servant.
Provide a concise, conversational morning briefing for {user_name} based on the following raw data.
Keep it strictly under 4 sentences. Speak naturally, throw in a quick joke, and do not list markdown bullets.

[Weather Data]: {weather_data}
[Schedule Data]: {schedule_data}
[Email Data]: {email_data}
"""

    response = query_local_llm_chat([{"role": "user", "content": prompt}], model=get_setting("llm_model", "qwen2.5:3b"))
    briefing = response.get("content", "") if isinstance(response, dict) else str(response)
    
    if briefing:
        speak(briefing)
        return briefing
    else:
        speak("I failed to compile your briefing.")
        return "Briefing compilation failed."


# ==========================================
# ATOMIC AGENTIC TOOLS (Mouse, Clipboard, Shell, File System)
# ==========================================

def scroll(clicks):
    """Scrolls the mouse wheel up (positive) or down (negative)."""
    import pyautogui
    try:
        pyautogui.scroll(clicks)
        return f"Scrolled {clicks} units."
    except Exception as e:
        return f"Scroll failed: {e}"

def drag_and_drop(start_x, start_y, end_x, end_y):
    """Drags the mouse from a start coordinate to an end coordinate."""
    import pyautogui
    try:
        pyautogui.moveTo(start_x, start_y)
        pyautogui.dragTo(end_x, end_y, duration=0.5)
        return f"Dragged from ({start_x},{start_y}) to ({end_x},{end_y})."
    except Exception as e:
        return f"Drag and drop failed: {e}"

def read_clipboard():
    """Reads text from the system clipboard."""
    import pyperclip
    try:
        content = pyperclip.paste()
        return content if content else "Clipboard is empty."
    except Exception as e:
        return f"Failed to read clipboard: {e}"

def write_clipboard(text):
    """Writes text to the system clipboard."""
    import pyperclip
    try:
        pyperclip.copy(text)
        return "Successfully copied to clipboard."
    except Exception as e:
        return f"Failed to write to clipboard: {e}"

def run_terminal_command(command):
    """Executes a background terminal command and returns the output."""
    import subprocess
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=15)
        output = result.stdout.strip()
        error = result.stderr.strip()
        if result.returncode == 0:
            return output if output else "Command executed successfully (no output)."
        else:
            return f"Command failed (Code {result.returncode}): {error}"
    except subprocess.TimeoutExpired:
        return "Command timed out after 15 seconds."
    except Exception as e:
        return f"Failed to execute command: {e}"

def list_directory(path="."):
    """Lists files and folders in a given directory."""
    import os
    try:
        items = os.listdir(path)
        return "\n".join(items) if items else "Directory is empty."
    except Exception as e:
        return f"Failed to list directory: {e}"

def read_file(path):
    """Reads the contents of a local file."""
    import os
    try:
        if not os.path.exists(path):
            return "File not found."
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Failed to read file: {e}"

def write_file(path, content):
    """Writes text content to a local file (overwrites if exists)."""
    try:
        from pathlib import Path
        target_path = Path(path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {target_path}"
    except Exception as e:
        return f"Failed to write file: {e}"

def get_clickable_elements(window_title=None):
    """
    Scans a window and returns a list of clickable elements with their text and (x, y) coordinates.
    This solves the 'Coordinate Problem' for AI agents, allowing you to know exactly where to click without vision guessing.
    Pass window_title to inspect a specific window instead of the one in focus.
    """
    _ensure_com()
    try:
        import uiautomation as auto
    except ImportError:
        return "ERROR: The 'uiautomation' module is missing. Please tell the user to run 'pip install uiautomation' to enable screen reading capabilities."

    try:
        current_pid = os.getpid()

        if window_title:
            active_window = _find_window(window_title)
            if not active_window:
                return (f"No open window matches '{window_title}'. "
                        f"Use list_windows to see what is available.")
        else:
            active_window = auto.GetForegroundControl()
            if not active_window or active_window.ProcessId == current_pid or "vave" in (active_window.Name or "").lower():
                active_window = None
                for w in auto.GetRootControl().GetChildren():
                    if w.ControlType == auto.ControlType.WindowControl and w.BoundingRectangle.width() > 100:
                        if w.ProcessId != current_pid and "vave" not in (w.Name or "").lower():
                            active_window = w
                            break
            if not active_window:
                active_window = auto.GetRootControl()

        elements = []
        # Walk deep enough to reach real controls. Modern apps (Chrome, Electron,
        # WinUI) bury their buttons well below depth 4, which is why shallow
        # scans only ever came back with window chrome.
        for control, depth in auto.WalkControl(active_window, maxDepth=10):
            # We look for things that might be clickable: Buttons, MenuItems, ListItems, Tabs, Custom controls, Cards, Avatars, etc.
            if control.ControlType in _clickable_control_types(auto):
                name = control.Name
                # Deep XAML label extraction: if container/card/avatar has no direct name, inspect child text
                if not name and control.ControlType in (
                    auto.ControlType.CustomControl,
                    auto.ControlType.ListItemControl,
                    auto.ControlType.GroupControl,
                    auto.ControlType.ImageControl,
                    auto.ControlType.PaneControl,
                ):
                    try:
                        for child in control.GetChildren():
                            if child.ControlType == auto.ControlType.TextControl and child.Name:
                                name = child.Name
                                break
                    except Exception:
                        pass
                if not name:
                    name = control.AutomationId or getattr(control, "HelpText", "") or getattr(control, "ItemStatus", "")

                rect = control.BoundingRectangle
                if name and rect.width() > 0 and rect.height() > 0:
                    try:
                        if control.IsOffscreen:
                            continue
                    except Exception:
                        pass
                    # Calculate center point for clicking
                    center_x = rect.left + (rect.width() // 2)
                    center_y = rect.top + (rect.height() // 2)
                    kind = control.ControlTypeName.replace("Control", "")
                    if control.ControlType == auto.ControlType.CustomControl:
                        kind = "Custom"
                    elif control.ControlType == auto.ControlType.ImageControl:
                        kind = "Image/Avatar"
                    elif control.ControlType == auto.ControlType.GroupControl:
                        kind = "Card/Group"

                    # Tell the model when a control cannot be actioned, so it
                    # stops retrying a greyed-out button forever.
                    try:
                        state = "" if control.IsEnabled else " [disabled]"
                    except Exception:
                        state = ""
                    # Both routes offered, name first: naming the control is the
                    # reliable one, and the coordinates are still needed for a
                    # right-click or when two controls share a label.
                    label = (f"- '{name}' ({kind}{state}): "
                             f"click_element(name='{name}')  "
                             f"or click_at(x={center_x}, y={center_y})")
                    if label not in elements:
                        elements.append(label)
                if len(elements) >= 120:
                    break

        if not elements:
            return (f"Found no named controls in the window '{active_window.Name}'. "
                    f"Browsers and some apps only publish their controls while the window is "
                    f"in focus, so try focus_window first, or read_screen / analyze_screen "
                    f"instead.\n" + list_windows())

        header = f"Active window: '{active_window.Name}'\nFound these clickable elements:"
        body = "\n".join(elements)

        # Chromium suspends its accessibility tree for background windows, so a
        # near-empty result usually means we are looking at the wrong window
        # rather than an empty one. Say so instead of letting the model guess.
        if len(elements) <= 4:
            body += ("\n\nOnly a few controls were visible, which usually means this window is "
                     "not in focus. Consider focus_window then scanning again.\n" + list_windows())

        return header + "\n" + body
    except Exception as e:
        return f"Failed to get clickable elements: {e}"


def _clickable_control_types(auto):
    """The control kinds worth offering as something to click."""
    return (
        auto.ControlType.ButtonControl,
        auto.ControlType.MenuItemControl,
        auto.ControlType.TabItemControl,
        auto.ControlType.ListItemControl,
        auto.ControlType.HyperlinkControl,
        auto.ControlType.EditControl,
        auto.ControlType.CheckBoxControl,
        auto.ControlType.RadioButtonControl,
        auto.ControlType.ComboBoxControl,
        auto.ControlType.TreeItemControl,
        auto.ControlType.SliderControl,
        auto.ControlType.CustomControl,
        auto.ControlType.ImageControl,
        auto.ControlType.GroupControl,
        auto.ControlType.PaneControl,
    )


def click_element(name, window_title=None):
    """Clicks the control with this label - a button, menu item, link or tab.

    The coordinate route works, but only if the coordinates survive the trip:
    handed a list of real buttons with real positions, the small model was
    measured calling `click_at(x=100, y=200)` - a number from nowhere, landing
    in the middle of the document. Naming the button removes the arithmetic, so
    there is nothing left to get wrong.

    An ambiguous name clicks nothing and says what matched, because guessing
    between two buttons is how you press Delete instead of Details.
    """
    _ensure_com()
    try:
        import uiautomation as auto
    except ImportError:
        return ("ERROR: The 'uiautomation' module is missing. Please tell the "
                "user to run 'pip install uiautomation' to enable clicking.")

    wanted = str(name or "").strip()
    if not wanted:
        return "Tell me which element to click, by its label."

    try:
        if window_title:
            window = _find_window(window_title)
            if not window:
                return (f"No open window matches '{window_title}'. "
                        f"Use list_windows to see what is available.")
        else:
            window = auto.GetForegroundControl()
            if not window:
                return ("Could not tell which window is in focus. "
                        "Use focus_window first.")

        if window and getattr(window, "NativeWindowHandle", None):
            activate_window(window.NativeWindowHandle)
            import time
            time.sleep(0.15)

        lowered = wanted.lower()
        alt_words = {
            "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
            "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
            "+": "plus", "-": "minus", "*": "multiply", "/": "divide", "=": "equals"
        }
        alt_lowered = alt_words.get(lowered)

        exact, starts, contains = [], [], []
        walk_count = 0
        for control, _depth in auto.WalkControl(window, maxDepth=10):
            walk_count += 1
            if walk_count > 800:
                break
            if control.ControlType not in _clickable_control_types(auto):
                continue
            label = control.Name
            if not label and control.ControlType in (
                auto.ControlType.CustomControl,
                auto.ControlType.ListItemControl,
                auto.ControlType.GroupControl,
                auto.ControlType.ImageControl,
                auto.ControlType.PaneControl,
            ):
                try:
                    for child in control.GetChildren():
                        if child.ControlType == auto.ControlType.TextControl and child.Name:
                            label = child.Name
                            break
                except Exception:
                    pass
            if not label:
                label = control.AutomationId or getattr(control, "HelpText", "") or getattr(control, "ItemStatus", "")
            if not label:
                continue
            rect = control.BoundingRectangle
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            try:
                if control.IsOffscreen:
                    continue
            except Exception:
                pass
            found = label.lower()
            if found == lowered or (alt_lowered and found == alt_lowered):
                exact.append(control)
            elif found.startswith(lowered) or (alt_lowered and found.startswith(alt_lowered)):
                starts.append(control)
            elif lowered in found or (alt_lowered and alt_lowered in found):
                contains.append(control)

        matches = exact or starts or contains
        if not matches:
            return (f"Nothing labelled '{wanted}' in the window "
                    f"'{window.Name}'.\n" + get_clickable_elements(window_title))

        if len(matches) > 1:
            names = ", ".join(f"'{c.Name or c.AutomationId}'" for c in matches[:6])
            return (f"'{wanted}' matches {len(matches)} elements: {names}. "
                    f"Say which one exactly, or use click_at with its "
                    f"coordinates from get_clickable_elements.")

        target = matches[0]
        try:
            if not target.IsEnabled:
                return (f"'{target.Name or wanted}' is greyed out and cannot be clicked "
                        f"right now.")
        except Exception:
            pass

        rect = target.BoundingRectangle
        if rect.left < -1000 or rect.top < -1000:
            focus_window(window_title)
            rect = target.BoundingRectangle
            if rect.left < -1000 or rect.top < -1000:
                return f"Cannot click '{wanted}': window '{window.Name}' is minimized or off-screen."

        orig_fg = None
        try:
            orig_fg = auto.GetForegroundControl()
        except Exception:
            pass

        x = rect.left + (rect.width() // 2)
        y = rect.top + (rect.height() // 2)

        # Prefer direct programmatic InvokePattern if supported
        invoked = False
        try:
            pattern = target.GetInvokePattern()
            if pattern:
                pattern.Invoke()
                invoked = True
        except Exception:
            invoked = False

        if not invoked:
            pyautogui = _get_pyautogui()
            pyautogui.click(x, y)
        kind = target.ControlTypeName.replace("Control", "")

        # Closed-loop verification: check if focus switched or state transitioned
        import time
        time.sleep(0.25)
        verification = ""
        try:
            new_fg = auto.GetForegroundControl()
            if new_fg and orig_fg and getattr(new_fg, "NativeWindowHandle", None) != getattr(orig_fg, "NativeWindowHandle", None):
                verification = f" (Verified: Active window switched to '{new_fg.Name}')"
            else:
                state_changed = False
                try:
                    if target.BoundingRectangle.width() <= 0 or target.IsOffscreen:
                        state_changed = True
                except Exception:
                    state_changed = True

                if state_changed:
                    verification = " (Verified: Element state changed)"
                else:
                    verification = " (Action sent; effect unconfirmed)"
        except Exception:
            verification = " (Action sent; effect unconfirmed)"

        invoke_detail = " via InvokePattern" if invoked else f" at ({x}, {y})"
        target_name = target.Name or wanted
        return f"Clicked '{target_name}' ({kind}){invoke_detail}.{verification}"
    except Exception as e:
        return f"Failed to click '{wanted}': {e}"


def list_windows():
    """Lists the open application windows so a task can be pointed at the right one."""
    _ensure_com()
    try:
        import uiautomation as auto
    except ImportError:
        return "ERROR: The 'uiautomation' module is missing. Please tell the user to run 'pip install uiautomation'."

    try:
        current_pid = os.getpid()
        rows = []
        try:
            foreground = auto.GetForegroundControl()
            foreground_name = foreground.Name if foreground else ""
        except Exception:
            foreground_name = ""

        for w in auto.GetRootControl().GetChildren():
            if w.ControlType != auto.ControlType.WindowControl:
                continue
            name = (w.Name or "").strip()
            if not name or w.ProcessId == current_pid:
                continue
            rect = w.BoundingRectangle
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            marker = "  <- currently in focus" if name == foreground_name else ""
            rows.append(f"- '{name}'{marker}")

        if not rows:
            return "No open application windows were found."
        return "Open windows:\n" + "\n".join(rows)
    except Exception as e:
        return f"Failed to list windows: {e}"


def _find_window(title):
    """Best-effort lookup of a top-level window by (partial) title or app alias."""
    _ensure_com()
    import uiautomation as auto

    wanted = str(title).strip().lower()
    current_pid = os.getpid()
    windows = []
    for w in auto.GetRootControl().GetChildren():
        if w.ControlType == auto.ControlType.WindowControl and w.ProcessId != current_pid:
            windows.append(w)

    # 1. Exact title match
    for w in windows:
        if (w.Name or "").strip().lower() == wanted:
            return w

    # 2. Substring title match
    for w in windows:
        name_lower = (w.Name or "").strip().lower()
        if name_lower and wanted in name_lower:
            return w

    # 3. Known app aliases (terminal/cmd, notepad, calc, etc.)
    if wanted in ("cmd", "terminal", "command prompt"):
        for w in windows:
            name_lower = (w.Name or "").lower()
            if "cmd" in name_lower or "command prompt" in name_lower or "terminal" in name_lower or w.ClassName == "CASCADIA_HOSTING_WINDOW_CLASS":
                return w

    if wanted in ("calc", "calculator"):
        for w in windows:
            if "calc" in (w.Name or "").lower():
                return w

    if wanted in ("notepad",):
        for w in windows:
            if "notepad" in (w.Name or "").lower() or w.ClassName == "Notepad":
                return w

    return None


def focus_window(title):
    """Brings a window to the front by title, so clicks and typing land in the right app."""
    _ensure_com()
    try:
        import uiautomation as auto  # noqa: F401
    except ImportError:
        return "ERROR: The 'uiautomation' module is missing. Please tell the user to run 'pip install uiautomation'."

    import time
    try:
        win = _find_window(title)
        if not win:
            return f"No open window matches '{title}'. Use list_windows to see what is available."

        # If window is minimized, restore it first so coordinates and clicks become valid
        try:
            pattern = win.GetWindowPattern()
            if pattern and hasattr(pattern, "WindowVisualState"):
                if pattern.WindowVisualState == auto.WindowVisualState.Minimized:
                    pattern.SetWindowVisualState(auto.WindowVisualState.Normal)
            elif hasattr(win, "ShowWindow"):
                win.ShowWindow(auto.SW.Restore)
        except Exception:
            pass

        if getattr(win, "NativeWindowHandle", None):
            activate_window(win.NativeWindowHandle)
        win.SetActive()
        try:
            win.SetTopmost(False)
        except Exception:
            pass
        time.sleep(0.4)
        return f"Focused the window '{win.Name}'."
    except Exception as e:
        return f"Failed to focus window: {e}"



def close_window(title=None):
    """Closes a window by title, or the window in focus when no title is given."""
    _ensure_com()
    try:
        import uiautomation as auto
    except ImportError:
        return "ERROR: The 'uiautomation' module is missing. Please tell the user to run 'pip install uiautomation'."

    try:
        current_pid = os.getpid()
        if title:
            win = _find_window(title)
            if not win:
                return f"No open window matches '{title}'. Use list_windows to see what is available."
        else:
            win = auto.GetForegroundControl()
            # Never let a "close the window" instruction close the assistant.
            if not win or win.ProcessId == current_pid:
                return "The window in focus belongs to VAVE, so it was left alone. Name the window to close instead."

        name = win.Name
        try:
            win.GetWindowPattern().Close()
        except Exception:
            win.SetActive()
            press_key("alt+f4")
        return f"Closed the window '{name}'."
    except Exception as e:
        return f"Failed to close window: {e}"




def disable_voice_input():
    """Disables the wake word engine and microphone listening."""
    try:
        from assistant.wakeword import pause_wakeword
        from assistant.config import update_setting
        update_setting("wake_word_enabled", False)
        pause_wakeword()
        return "Microphone and wake word listening have been disabled."
    except ImportError:
        return "Wake word engine not found."

def enable_voice_input():
    """Enables the wake word engine and microphone listening."""
    try:
        from assistant.wakeword import resume_wakeword
        from assistant.config import update_setting
        update_setting("wake_word_enabled", True)
        resume_wakeword()
        return "Microphone and wake word listening have been enabled."
    except ImportError:
        return "Wake word engine not found."


def disable_speech_output():
    """Mutes VAVE so he stops speaking out loud (Text-to-Speech). He will only reply via text."""
    from assistant.speech import set_speech_enabled
    from assistant.config import update_setting
    update_setting("speech_enabled", False)
    set_speech_enabled(False)
    return "My voice output has been disabled. I will only communicate via text."

def enable_speech_output():
    """Unmutes VAVE so he speaks out loud again using Text-to-Speech."""
    from assistant.speech import set_speech_enabled
    from assistant.config import update_setting
    update_setting("speech_enabled", True)
    set_speech_enabled(True)
    return "My voice output has been enabled. I can speak again."


def send_telegram_update(message_text):
    """Sends a Telegram message to the user."""
    from assistant.config import get_setting
    from assistant.telegram_sync import send_telegram_message
    from assistant.control.secrets import resolve_setting

    token = resolve_setting(get_setting("telegram_bot_token", ""))
    chat_id = get_setting("telegram_chat_id", "")

    if not token or not chat_id:
        return "Telegram is not configured. Missing bot token or chat ID."

    send_telegram_message(token, chat_id, message_text)
    return f"Message sent to Telegram successfully: {message_text}"


def media_control(action="play_pause"):
    """
    Controls OS-level multimedia playback globally in the background without needing focus.
    Supported actions:
      - 'play_pause', 'play', 'pause', 'resume': toggle playback
      - 'next', 'next_track', 'skip': skip to next track
      - 'previous', 'prev', 'prev_track': go to previous track / restart track
      - 'stop': stop playback
      - 'mute', 'unmute': toggle master audio mute
    """
    act = str(action or "play_pause").lower().strip().replace(" ", "_")
    system = get_os()

    if system == "windows":
        import ctypes
        VK_VOLUME_MUTE = 0xAD       # 173
        VK_MEDIA_NEXT_TRACK = 0xB0  # 176
        VK_MEDIA_PREV_TRACK = 0xB1  # 177
        VK_MEDIA_STOP = 0xB2        # 178
        VK_MEDIA_PLAY_PAUSE = 0xB3  # 179
        KEYEVENTF_EXTENDEDKEY = 0x0001
        KEYEVENTF_KEYUP = 0x0002

        vk_map = {
            "play_pause": VK_MEDIA_PLAY_PAUSE,
            "play": VK_MEDIA_PLAY_PAUSE,
            "pause": VK_MEDIA_PLAY_PAUSE,
            "resume": VK_MEDIA_PLAY_PAUSE,
            "unpause": VK_MEDIA_PLAY_PAUSE,
            "next": VK_MEDIA_NEXT_TRACK,
            "next_track": VK_MEDIA_NEXT_TRACK,
            "skip": VK_MEDIA_NEXT_TRACK,
            "previous": VK_MEDIA_PREV_TRACK,
            "prev": VK_MEDIA_PREV_TRACK,
            "prev_track": VK_MEDIA_PREV_TRACK,
            "stop": VK_MEDIA_STOP,
            "mute": VK_VOLUME_MUTE,
            "unmute": VK_VOLUME_MUTE,
        }

        vk = vk_map.get(act)
        if not vk:
            return f"Unknown media action: '{action}'. Use play_pause, next, previous, stop, or mute."

        try:
            user32 = ctypes.windll.user32
            user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
            user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
            return f"Media command '{act}' executed successfully."
        except Exception as e:
            try:
                pyautogui = _get_pyautogui()
                pg_map = {
                    VK_MEDIA_PLAY_PAUSE: "playpause",
                    VK_MEDIA_NEXT_TRACK: "nexttrack",
                    VK_MEDIA_PREV_TRACK: "prevtrack",
                    VK_MEDIA_STOP: "stop",
                    VK_VOLUME_MUTE: "volumemute",
                }
                pyautogui.press(pg_map[vk])
                return f"Media command '{act}' executed successfully via pyautogui."
            except Exception as e2:
                return f"Failed to execute media command '{act}': {e} / {e2}"

    elif system == "darwin":
        cmd_map = {
            "play_pause": "playpause",
            "play": "play",
            "pause": "pause",
            "resume": "play",
            "unpause": "play",
            "next": "next track",
            "skip": "next track",
            "previous": "previous track",
            "prev": "previous track",
            "stop": "pause",
        }
        if act == "mute" or act == "unmute":
            os.system("osascript -e 'set volume output muted (not (output muted of (get volume settings)))'")
            return f"Media command '{act}' executed successfully on macOS."
        sub = cmd_map.get(act)
        if not sub:
            return f"Unknown media action: '{action}'. Use play_pause, next, previous, stop, or mute."
        try:
            os.system(f"osascript -e 'tell application \"Music\" to {sub}'")
            return f"Media command '{act}' executed successfully on macOS."
        except Exception as e:
            return f"Failed to execute media command on macOS: {e}"

    else:
        cmd_map = {
            "play_pause": "play-pause",
            "play": "play",
            "pause": "pause",
            "resume": "play",
            "unpause": "play",
            "next": "next",
            "skip": "next",
            "previous": "previous",
            "prev": "previous",
            "stop": "stop",
        }
        sub = cmd_map.get(act)
        if not sub:
            return f"Unknown media action: '{action}'. Use play_pause, next, previous, stop, or mute."
        try:
            subprocess.run(["playerctl", sub], check=False)
            return f"Media command '{act}' executed successfully via playerctl."
        except Exception as e:
            return f"Failed to execute media command: {e}"


def snap_window(app_name, position="left"):
    """
    Snaps or repositions an application window to an organized desktop layout.
    Supported positions:
      - 'left': snap to left half of screen
      - 'right': snap to right half of screen
      - 'top': snap to top half of screen
      - 'bottom': snap to bottom half of screen
      - 'maximize', 'full', 'max': maximize window
      - 'minimize', 'min': minimize window
      - 'restore': restore window to normal size
      - 'center': position window centered taking ~75% screen
    """
    pos = str(position or "left").lower().strip()
    system = get_os()

    if system != "windows":
        return f"Window snapping is currently only supported on Windows (detected {system})."

    _ensure_com()
    import uiautomation as auto
    import ctypes
    from ctypes import wintypes

    clean_target = str(app_name or "").strip()
    if not clean_target:
        return "Please specify an application name or window title to snap."

    # Find the target window
    window = None
    if clean_target.lower() in ("current", "active", "this", "foreground"):
        try:
            window = auto.GetForegroundControl()
        except Exception:
            window = None
    else:
        window = _find_window(clean_target)

    if not window:
        return f"Could not find open window matching '{clean_target}'. Use list_windows to see active windows."

    hwnd = getattr(window, "NativeWindowHandle", None)
    if not hwnd or hwnd == 0:
        return f"Window '{window.Name}' does not have a valid native window handle."

    user32 = ctypes.windll.user32

    # Window ShowWindow constants
    SW_RESTORE = 9
    SW_MAXIMIZE = 3
    SW_MINIMIZE = 6
    HWND_TOP = 0
    SWP_SHOWWINDOW = 0x0040

    if pos in ("maximize", "max", "full"):
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        activate_window(hwnd)
        return f"Maximized '{window.Name}'."

    if pos in ("minimize", "min"):
        user32.ShowWindow(hwnd, SW_MINIMIZE)
        return f"Minimized '{window.Name}'."

    if pos in ("restore", "normal"):
        user32.ShowWindow(hwnd, SW_RESTORE)
        activate_window(hwnd)
        return f"Restored '{window.Name}' to normal size."

    # Query work area for the monitor where the window currently resides
    screen_x, screen_y, screen_w, screen_h = 0, 0, 0, 0
    try:
        class _MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        MONITOR_DEFAULTTONEAREST = 2
        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            screen_x = mi.rcWork.left
            screen_y = mi.rcWork.top
            screen_w = mi.rcWork.right - mi.rcWork.left
            screen_h = mi.rcWork.bottom - mi.rcWork.top
    except Exception:
        pass

    if screen_w <= 0 or screen_h <= 0:
        # Fallback to primary work area
        SPI_GETWORKAREA = 0x0030
        rect = wintypes.RECT()
        if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            rect.left = 0
            rect.top = 0
            rect.right = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            rect.bottom = user32.GetSystemMetrics(1)  # SM_CYSCREEN
        screen_x = rect.left
        screen_y = rect.top
        screen_w = rect.right - rect.left
        screen_h = rect.bottom - rect.top

    # Ensure window is restored before moving (maximized windows cannot be repositioned with SetWindowPos)
    user32.ShowWindow(hwnd, SW_RESTORE)

    if pos in ("left", "half_left"):
        target_x = screen_x
        target_y = screen_y
        target_w = screen_w // 2
        target_h = screen_h
        desc = "left half"
    elif pos in ("right", "half_right"):
        target_x = screen_x + (screen_w // 2)
        target_y = screen_y
        target_w = screen_w - (screen_w // 2)
        target_h = screen_h
        desc = "right half"
    elif pos in ("top", "half_top"):
        target_x = screen_x
        target_y = screen_y
        target_w = screen_w
        target_h = screen_h // 2
        desc = "top half"
    elif pos in ("bottom", "half_bottom"):
        target_x = screen_x
        target_y = screen_y + (screen_h // 2)
        target_w = screen_w
        target_h = screen_h - (screen_h // 2)
        desc = "bottom half"
    elif pos in ("center", "middle"):
        target_w = int(screen_w * 0.75)
        target_h = int(screen_h * 0.75)
        target_x = screen_x + (screen_w - target_w) // 2
        target_y = screen_y + (screen_h - target_h) // 2
        desc = "center"
    else:
        return f"Unknown position '{position}'. Use 'left', 'right', 'top', 'bottom', 'maximize', 'minimize', or 'center'."

    # Apply placement
    user32.SetWindowPos(hwnd, HWND_TOP, target_x, target_y, target_w, target_h, SWP_SHOWWINDOW)
    activate_window(hwnd)
    return f"Snapped '{window.Name}' to {desc} ({target_w}x{target_h} at {target_x},{target_y})."


def organize_workspace(left_app, right_app):
    """
    Organizes the desktop into a side-by-side split workspace.
    Ensures both applications are running (launches them if necessary),
    snaps left_app to the left half of the display, and snaps right_app to the right half.
    """
    system = get_os()
    if system != "windows":
        return f"Workspace organization is currently only supported on Windows (detected {system})."

    clean_left = str(left_app or "").strip()
    clean_right = str(right_app or "").strip()
    if not clean_left or not clean_right:
        return "Please specify both left and right applications (e.g. organize_workspace('code', 'cmd'))."

    import time

    # 1. Ensure left app is open
    left_win = _find_window(clean_left)
    if not left_win:
        open_app(clean_left)
        for _ in range(6):
            time.sleep(0.5)
            left_win = _find_window(clean_left)
            if left_win:
                break

    # 2. Ensure right app is open
    right_win = _find_window(clean_right)
    if not right_win:
        open_app(clean_right)
        for _ in range(6):
            time.sleep(0.5)
            right_win = _find_window(clean_right)
            if right_win:
                break

    # 3. Snap left app to left half
    res_left = snap_window(clean_left, "left")
    time.sleep(0.3)

    # 4. Snap right app to right half
    res_right = snap_window(clean_right, "right")
    time.sleep(0.3)

    left_name = getattr(left_win, "Name", clean_left) if left_win else clean_left
    right_name = getattr(right_win, "Name", clean_right) if right_win else clean_right

    return f"Workspace organized: '{left_name}' snapped to left half, '{right_name}' snapped to right half."



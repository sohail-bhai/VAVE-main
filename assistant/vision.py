import logging
logger = logging.getLogger(__name__)

import threading
import pytesseract
from PIL import Image, ImageGrab
import pyautogui
import os

# Shared RapidOCR engine: model load is ~1s, so one warm instance serves all
# calls. False means "tried and unavailable" - never retry a dead import.
_rapid_ocr = None
_rapid_lock = threading.Lock()


def _rapid_engine():
    """The shared RapidOCR engine, or None when it cannot be loaded."""
    global _rapid_ocr
    if _rapid_ocr is not None:
        return _rapid_ocr or None
    with _rapid_lock:
        if _rapid_ocr is not None:
            return _rapid_ocr or None
        try:
            from rapidocr_onnxruntime import RapidOCR
            _rapid_ocr = RapidOCR()
        except Exception as e:
            logger.info(f"[Vision] RapidOCR unavailable: {e}")
            _rapid_ocr = False
    return _rapid_ocr or None


def _box_center(box):
    """Center of an OCR box, in 4-point or flat [x0,y0,x1,y1] form."""
    try:
        if hasattr(box, "tolist"):  # numpy boxes from the OCR engine
            box = box.tolist()
        pts = list(box)
        if len(pts) == 4 and all(isinstance(p, (list, tuple)) for p in pts):
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        if len(pts) == 4:
            x0, y0, x1, y1 = (float(v) for v in pts)
            return ((x0 + x1) / 2, (y0 + y1) / 2)
    except Exception:
        pass
    return None


def _inside(center, within):
    """True when a point is inside an optional (left, top, right, bottom) box."""
    if not within or not center:
        return True
    x, y = center
    left, top, right, bottom = within
    return left <= x <= right and top <= y <= bottom


def ocr_screen():
    """Full-screen OCR as [(text, box, score)]. [] when unavailable."""
    engine = _rapid_engine()
    if engine is None:
        return []
    try:
        import numpy as np
        shot = np.asarray(ImageGrab.grab())
        result, _elapse = engine(shot)
    except Exception as e:
        logger.info(f"[Vision Error] RapidOCR read failed: {e}")
        return []
    lines = []
    for row in (result or []):
        try:
            box, text, score = row[0], str(row[1] or ""), float(row[2])
        except Exception:
            continue
        if text.strip():
            lines.append((text, box, score))
    return lines

def _find_tesseract():
    """Detects tesseract binary via config, common drive locations (E:, D:, C:), or system PATH."""
    try:
        from assistant.config import get_setting
        config_path = get_setting("tesseract_path", "") or get_setting("tesseract_cmd", "")
        if config_path and os.path.isfile(config_path):
            return config_path
    except Exception:
        pass

    candidate_paths = [
        r'E:\programs\Tesseract-OCR\tesseract.exe',
        r'E:\Tesseract-OCR\tesseract.exe',
        r'D:\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe'),
    ]
    for path in candidate_paths:
        if os.path.isfile(path):
            return path
    import shutil
    return shutil.which("tesseract")

# Set binary if found
_tess_cmd = _find_tesseract()
if _tess_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tess_cmd

def find_text_on_screen(target_text, within=None):
    """
    Finds (x, y) center coordinates of target_text on screen.
    Tier 1: Tesseract OCR when its binary exists.
    Tier 2: RapidOCR screenshot lines - no binary needed, sees browsers,
    canvas apps and images that UI Automation is blind to.
    Tier 3: UIAutomation control inspection.
    `within` is an optional (left, top, right, bottom) box; matches whose
    center falls outside it are ignored, so a same-named control in another
    window is never returned.
    """
    target = str(target_text or "").strip().lower()
    if not target:
        return None

    def rank(candidate):
        cand = str(candidate or "").strip().lower()
        if not cand:
            return None
        if cand == target:
            return 0
        cand_words = cand.split()
        if target in cand_words:
            return 1
        if cand.startswith(target):
            return 2
        if target in cand:
            return 3
        # Last resort: a significant target word equals a candidate word, so
        # "sohail profile" still finds a "Sohail" tile. Short words are
        # ignored ("the", "a") to avoid matching everything on screen.
        target_words = [w for w in target.split() if len(w) > 3]
        if target_words and any(w in cand_words for w in target_words):
            return 4
        return None

    tess = _find_tesseract()
    if tess:
        try:
            pytesseract.pytesseract.tesseract_cmd = tess
            screenshot = ImageGrab.grab()
            data = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)
            best = None
            confs = data.get("conf", [])
            words = data.get("text", [])
            for i, word in enumerate(words):
                r = rank(word)
                if r is None:
                    continue
                try:
                    conf = float(confs[i]) if i < len(confs) else 0.0
                except Exception:
                    conf = 0.0
                x = data["left"][i] + (data["width"][i] // 2)
                y = data["top"][i] + (data["height"][i] // 2)
                if not _inside((x, y), within):
                    continue
                key = (r, -conf)
                if best is None or key < best[0]:
                    best = (key, (x, y))
            if best is not None:
                return (int(best[1][0]), int(best[1][1]))
        except Exception as e:
            logger.info(f"[Vision Error] Tesseract OCR search failed: {e}")

    # Tier 2: RapidOCR screenshot lines
    try:
        best = None
        for text, box, score in ocr_screen():
            r = rank(text)
            if r is None:
                continue
            center = _box_center(box)
            if center is None or not _inside(center, within):
                continue
            key = (r, -float(score or 0.0))
            if best is None or key < best[0]:
                best = (key, center)
        if best is not None:
            return (int(best[1][0]), int(best[1][1]))
    except Exception as e:
        logger.info(f"[Vision Error] RapidOCR search failed: {e}")

    # Tier 3: UIAutomation element search
    try:
        import uiautomation as auto
        active = auto.GetForegroundControl() or auto.GetRootControl()
        for ctrl, depth in auto.WalkControl(active, maxDepth=6):
            if ctrl.Name and target in ctrl.Name.lower():
                rect = ctrl.BoundingRectangle
                if rect.width() > 0 and rect.height() > 0:
                    cx = rect.left + (rect.width() // 2)
                    cy = rect.top + (rect.height() // 2)
                    if _inside((cx, cy), within):
                        return (cx, cy)
    except Exception as e:
        logger.info(f"[Vision Error] UIAutomation text search failed: {e}")

    return None

def read_screen_text():
    """
    Takes a screenshot, runs OCR, and returns all text found on the screen.
    Falls back to UIAutomation control inspection and local VLM when Tesseract is absent.
    """
    # Tier 1: Tesseract OCR
    tess = _find_tesseract()
    if tess:
        try:
            pytesseract.pytesseract.tesseract_cmd = tess
            screenshot = ImageGrab.grab()
            text = pytesseract.image_to_string(screenshot)
            if text and text.strip():
                return text.strip()
        except Exception as e:
            logger.info(f"[Vision Error] Tesseract read failed: {e}")

    # Tier 2: UIAutomation structural text extraction from active and candidate windows
    try:
        import uiautomation as auto
        import os
        current_pid = os.getpid()
        candidates = []

        # Chromium does not publish its page to automation until a client asks.
        # Without this nudge a browser reads back as empty however much is on it.
        try:
            from assistant.system_tasks import _is_chromium_window, _nudge_accessibility
            import time as _t
            fg_win = auto.GetForegroundControl()
            if fg_win is not None and _is_chromium_window(fg_win):
                _nudge_accessibility(fg_win)
                _t.sleep(0.4)
        except Exception:
            pass

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

        # 1. First search all candidate windows specifically for real document/editor controls (Notepad, Word, editors)
        for win in candidates:
            for ctrl, depth in auto.WalkControl(win, maxDepth=6):
                if ctrl.ControlType in (auto.ControlType.DocumentControl, auto.ControlType.EditControl) or ctrl.ClassName in ("RichEditD2DPT", "Edit"):
                    doc_text = None
                    try:
                        tp = ctrl.GetTextPattern()
                        if tp:
                            doc_text = tp.DocumentRange.GetText(-1)
                    except Exception:
                        pass
                    if doc_text is None:
                        try:
                            vp = ctrl.GetValuePattern()
                            if vp:
                                doc_text = vp.Value
                        except Exception:
                            pass
                    if doc_text is not None:
                        normalized = doc_text.replace("\r\n", "\n").replace("\r", "\n").strip()
                        return normalized

        # 2. If no dedicated document control found, collect meaningful UI element text, ignoring icon glyphs
        if candidates:
            extracted = []
            for ctrl, depth in auto.WalkControl(candidates[0], maxDepth=6):
                # Check TextPattern on TextBlocks only if they are not single icon characters
                val = None
                try:
                    tp = ctrl.GetTextPattern()
                    if tp:
                        t = tp.DocumentRange.GetText(-1).strip()
                        if t and not (len(t) == 1 and ord(t) >= 0xE000):
                            val = t
                except Exception:
                    pass
                if not val and ctrl.Name:
                    name_str = ctrl.Name.strip()
                    if len(name_str) > 1 and not (len(name_str) == 1 and ord(name_str) >= 0xE000):
                        val = name_str
                if val and val not in extracted and ctrl.ControlType in (
                    auto.ControlType.TextControl,
                    auto.ControlType.ButtonControl,
                    auto.ControlType.MenuItemControl,
                    auto.ControlType.ListItemControl,
                    auto.ControlType.HeaderItemControl,
                ):
                    extracted.append(val)
            if extracted:
                return "\n".join(extracted)
    except Exception as e:
        logger.info(f"[Vision Error] UIAutomation text extraction failed: {e}")

    # Tier 3: Multimodal VLM (moondream)
    try:
        vlm_res = analyze_screen(prompt="Transcribe all readable text visible on this screen. Return only the visible text.")
        if vlm_res and not vlm_res.startswith("Error"):
            return vlm_res
    except Exception as e:
        logger.info(f"[Vision Error] VLM screen transcription failed: {e}")

    return "No readable text found on the screen."

def find_icon_on_screen(icon_path):
    """
    Uses PyAutoGUI to find a specific image/icon on the screen.
    Returns the (x, y) center coordinates, or None if not found.
    """
    if not os.path.exists(icon_path):
        logger.info(f"[Vision Error] Icon image not found at {icon_path}")
        return None
        
    try:
        location = pyautogui.locateCenterOnScreen(icon_path, confidence=0.8)
        return location
    except pyautogui.ImageNotFoundException:
        return None
    except Exception as e:
        logger.info(f"[Vision Error] Image matching failed: {e}")
        return None

def analyze_screen(prompt="Describe what is on the screen in detail.", image_path=None):
    """
    Takes a screenshot (or loads an image) and sends it to a local Vision Language Model 
    via Ollama to answer questions about the screen.
    """
    import base64
    from io import BytesIO
    import urllib.request
    import json
    from assistant.config import get_setting

    try:
        if image_path:
            screenshot = Image.open(image_path)
        else:
            # On Windows, attach thread to active desktop to prevent "screen grab failed"
            try:
                import ctypes
                user32 = ctypes.windll.user32
                hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
                if hdesk:
                    user32.SetThreadDesktop(hdesk)
            except Exception:
                pass
            # 1. Take screenshot and compress it
            screenshot = ImageGrab.grab()
            
        # Resize to max 1024x1024 to save memory and speed up VLM processing
        screenshot.thumbnail((1024, 1024))
        
        # 2. Convert to Base64
        buffered = BytesIO()
        screenshot.save(buffered, format="JPEG", quality=80)
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        # 3. Get VLM model name from config (default to moondream:latest)
        vlm_model = get_setting("vlm_model", "moondream:latest")
        
        # 4. Query Ollama API
        url = "http://localhost:11434/api/generate"
        payload = {
            "model": vlm_model,
            "prompt": prompt,
            "images": [img_str],
            "stream": False
        }
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode('utf-8'))
            answer = result.get("response", "").strip()
            return answer if answer else "I could not analyze the screen."
            
    except urllib.error.URLError as e:
        return f"Error: Could not connect to local Vision Model. Make sure Ollama is running and '{vlm_model}' is installed."
    except Exception as e:
        logger.info(f"[Vision Error] Screen analysis failed: {e}")
        return f"Error analyzing screen: {e}"

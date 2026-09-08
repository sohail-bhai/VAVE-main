import logging
logger = logging.getLogger(__name__)

import pytesseract
from PIL import Image, ImageGrab
import pyautogui
import os

def _find_tesseract():
    """Detects tesseract binary in common Windows locations or system PATH."""
    candidate_paths = [
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

def find_text_on_screen(target_text):
    """
    Finds (x, y) center coordinates of target_text on screen.
    Uses Tesseract OCR if available, falling back to UIAutomation control inspection.
    """
    tess = _find_tesseract()
    if tess:
        try:
            pytesseract.pytesseract.tesseract_cmd = tess
            screenshot = ImageGrab.grab()
            data = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)
            target_text_lower = target_text.lower()
            for i in range(len(data['text'])):
                detected_word = data['text'][i].strip().lower()
                if target_text_lower in detected_word and detected_word != '':
                    x = data['left'][i]
                    y = data['top'][i]
                    w = data['width'][i]
                    h = data['height'][i]
                    return (x + (w // 2), y + (h // 2))
        except Exception as e:
            logger.info(f"[Vision Error] Tesseract OCR search failed: {e}")

    # Fallback: UIAutomation element search
    try:
        import uiautomation as auto
        target_lower = target_text.lower()
        active = auto.GetForegroundControl() or auto.GetRootControl()
        for ctrl, depth in auto.WalkControl(active, maxDepth=6):
            if ctrl.Name and target_lower in ctrl.Name.lower():
                rect = ctrl.BoundingRectangle
                if rect.width() > 0 and rect.height() > 0:
                    return (rect.left + (rect.width() // 2), rect.top + (rect.height() // 2))
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

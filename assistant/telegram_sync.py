import logging
logger = logging.getLogger(__name__)

import json
import os
import queue
import threading
import time
import urllib.request
import urllib.parse
from assistant import call_context

from assistant.config import get_setting, update_setting
from assistant.commands import execute_command
from assistant.speech import speak

_telegram_thread = None
_telegram_active = False

# Commands from the phone run one at a time. A thread per message meant two
# agent loops driving the same mouse and keyboard the moment two texts arrived
# together, and the polling loop itself used to block for the whole LLM answer,
# which backed the inbox up behind every long task.
_command_queue: "queue.Queue" = queue.Queue()
_worker_thread = None

_telegram_pairing_code = None
_telegram_pairing_expiry = 0

def issue_telegram_pairing_code() -> str:
    """Issue a 6-digit one-time pairing code valid for 10 minutes."""
    global _telegram_pairing_code, _telegram_pairing_expiry
    import random, time
    _telegram_pairing_code = f"{random.randint(100000, 999999)}"
    _telegram_pairing_expiry = time.time() + 600
    return _telegram_pairing_code

def get_active_telegram_pairing_code() -> str:
    """Return active pairing code or generate a fresh one if expired/unset."""
    import time
    if _telegram_pairing_code and time.time() < _telegram_pairing_expiry:
        return _telegram_pairing_code
    return issue_telegram_pairing_code()

# --- Cross-process singleton lock ---
# Prevents MKL/numpy-spawned child processes from starting a second Telegram
# poller and causing a 409 Conflict.
_LOCK_PATH = os.path.join(os.path.dirname(__file__), "..", "data", ".telegram.pid")

def _acquire_lock():
    """Return True only if THIS process owns the singleton lock."""
    lock_path = os.path.abspath(_LOCK_PATH)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    my_pid = os.getpid()

    # Read existing lock
    try:
        with open(lock_path, "r") as f:
            existing_pid = int(f.read().strip())
        # Check if that PID is still alive
        try:
            import psutil
            if psutil.pid_exists(existing_pid) and existing_pid != my_pid:
                logger.debug(f"[Telegram] Singleton lock held by PID {existing_pid}. This child ({my_pid}) will not start a second poller.")
                return False
        except ImportError:
            # psutil not available: fall back to os.kill signal 0 trick
            try:
                os.kill(existing_pid, 0)
                if existing_pid != my_pid:
                    logger.debug(f"[Telegram] Singleton lock held by PID {existing_pid}. Skipping.")
                    return False
            except (ProcessLookupError, PermissionError):
                pass  # Dead process — stale lock, we can claim it
    except (FileNotFoundError, ValueError):
        pass  # No lock file or corrupt — we can claim it

    # Write our PID
    try:
        with open(lock_path, "w") as f:
            f.write(str(my_pid))
        return True
    except OSError:
        return False

def _release_lock():
    """Release the lock if we own it."""
    lock_path = os.path.abspath(_LOCK_PATH)
    try:
        with open(lock_path, "r") as f:
            existing_pid = int(f.read().strip())
        if existing_pid == os.getpid():
            os.remove(lock_path)
    except Exception:
        pass

def send_telegram_message(token, chat_id, text, reply_markup=None):
    if not text:
        return
    if not token or str(token).startswith("secret://"):
        # Belt and braces: every caller resolves the token first, so a
        # secret:// here means one slipped through. Never build a 404 URL.
        logger.info("[Telegram] Outbound message skipped: bot token is unset or unresolved.")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        text_str = str(text)
        chunks = [text_str[i:i+4000] for i in range(0, len(text_str), 4000)]
        for idx, chunk in enumerate(chunks):
            params = {'chat_id': chat_id, 'text': chunk}
            if reply_markup and idx == len(chunks) - 1:
                params['reply_markup'] = (json.dumps(reply_markup)
                                          if isinstance(reply_markup, dict)
                                          else str(reply_markup))
            data = urllib.parse.urlencode(params).encode('utf-8')
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as response:
                pass
    except Exception as e:
        logger.info(f"[Telegram Error] Could not send message: {e}")


def send_telegram_photo(token, chat_id, photo_path, caption=None):
    """Send a photo file to Telegram via multipart/form-data."""
    if not token or str(token).startswith("secret://"):
        return False
    if not os.path.exists(photo_path):
        logger.info(f"[Telegram] Photo path does not exist: {photo_path}")
        return False

    try:
        import uuid
        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
        filename = os.path.basename(photo_path)
        content_type = "image/png" if filename.lower().endswith(".png") else "image/jpeg"

        with open(photo_path, "rb") as f:
            photo_bytes = f.read()

        body = bytearray()
        # chat_id field
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode("utf-8"))

        # caption field
        if caption:
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption[:1000]}\r\n'.encode("utf-8"))

        # photo file field
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode("utf-8"))
        body.extend(f'Content-Type: {content_type}\r\n\r\n'.encode("utf-8"))
        body.extend(photo_bytes)
        body.extend(b"\r\n")

        # closing boundary
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        req = urllib.request.Request(url, data=bytes(body), headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body))
        })
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok"))
    except Exception as e:
        logger.info(f"[Telegram Error] Could not send photo: {e}")
        return False


def send_telegram_screenshot(caption=None):
    """Captures a live desktop screenshot and sends it directly to paired Telegram chat."""
    from assistant.system_tasks import take_screenshot
    from assistant.control.secrets import resolve_setting

    shot_path = take_screenshot()
    if not shot_path or not os.path.exists(shot_path):
        return "Could not capture screenshot."

    token = resolve_setting(get_setting("telegram_bot_token", ""))
    chat_id = get_setting("telegram_chat_id", "")
    if not token or not chat_id:
        return "Telegram is not configured. Please set telegram_bot_token and telegram_chat_id."

    import datetime
    cap = caption or f"🖥️ Desktop Screenshot ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
    ok = send_telegram_photo(token, chat_id, shot_path, caption=cap)
    if ok:
        return "Captured desktop and sent screenshot to your Telegram."
    return "Failed to send screenshot to Telegram."


def _analyze_telegram_photo(token, file_id, prompt="Describe what you see in this photo in detail."):
    """Download photo from Telegram and analyze it using the local vision model."""
    try:
        import tempfile
        from assistant.vision import analyze_screen

        info_url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
        with urllib.request.urlopen(info_url, timeout=10) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        if not info.get("ok"):
            return "Could not retrieve photo from Telegram."
        file_path = info["result"]["file_path"]

        dl_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        with urllib.request.urlopen(dl_url, timeout=25) as resp_dl:
            img_bytes = resp_dl.read()

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name

        try:
            return analyze_screen(prompt=prompt, image_path=tmp_path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    except Exception as e:
        logger.warning(f"[Telegram Photo] Analysis failed: {e}")
        return f"Error analyzing photo: {e}"


def _answer_callback_query(token, callback_query_id, text=None):
    """Acknowledge an inline keyboard button click in Telegram."""
    try:
        url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
        params = {"callback_query_id": callback_query_id}
        if text:
            params["text"] = text
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as e:
        logger.debug(f"[Telegram] Failed to answer callback query: {e}")


def _transcribe_telegram_voice(token, file_id):
    """Download .oga voice note from Telegram, convert with ffmpeg, and transcribe."""
    try:
        import subprocess
        import tempfile
        import speech_recognition as sr

        info_url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
        req = urllib.request.Request(info_url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        if not info.get("ok"):
            return None
        file_path = info["result"]["file_path"]

        download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        req_dl = urllib.request.Request(download_url)
        with urllib.request.urlopen(req_dl, timeout=25) as resp_dl:
            audio_bytes = resp_dl.read()

        with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as oga_file:
            oga_file.write(audio_bytes)
            oga_path = oga_file.name

        wav_path = oga_path.rsplit(".", 1)[0] + ".wav"
        try:
            cmd = ["ffmpeg", "-y", "-i", oga_path, "-ac", "1", "-ar", "16000", wav_path]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            r = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio_data = r.record(source)
            return r.recognize_google(audio_data)
        finally:
            for p in (oga_path, wav_path):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
    except Exception as e:
        logger.warning(f"[Telegram Voice] Transcription failed: {e}")
        return None

def _telegram_worker():
    global _telegram_active

    from assistant.control.secrets import resolve_setting
    token = resolve_setting(get_setting("telegram_bot_token", ""))
    chat_id = get_setting("telegram_chat_id", "")

    if not token:
        logger.info("[VAVE] Telegram bot token not configured, or its secret "
                    "could not be resolved. Remote execution disabled.")
        return
        
    logger.info(f"[VAVE] Connecting to Telegram Bot from PID {os.getpid()}...")
    
    # Pre-emptively delete any existing webhooks that might cause a 409 Conflict with getUpdates
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=5)
    except Exception:
        pass
        
    offset = 0
    
    while _telegram_active:
        try:
            # Use Long Polling (timeout=30s) so it doesn't spam the API
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=30"
            req = urllib.request.Request(url)
            
            with urllib.request.urlopen(req, timeout=40) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                if result.get("ok"):
                    for update in result.get("result", []):
                        offset = update["update_id"] + 1
                        
                        # 1. Handle Inline Keyboard button clicks
                        callback_query = update.get("callback_query")
                        if callback_query:
                            cb_id = callback_query.get("id")
                            cb_data = str(callback_query.get("data", "")).strip()
                            cb_chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
                            
                            if cb_chat_id == chat_id:
                                cb_lower = cb_data.lower()
                                if cb_lower.startswith("/yes ") or cb_lower.startswith("/no "):
                                    from assistant import confirm
                                    parts = cb_lower.split(" ")
                                    if len(parts) >= 2:
                                        approved = cb_lower.startswith("/yes")
                                        confirm.resolve(parts[1], approved)
                                        _answer_callback_query(token, cb_id, f"{'Approved' if approved else 'Denied'}")
                                        send_telegram_message(
                                            token, chat_id,
                                            f"{'✅ Approved' if approved else '❌ Denied'} task request: {parts[1]}"
                                        )
                            continue

                        # 2. Handle standard messages
                        message = update.get("message", {})
                        text = message.get("text", "")
                        voice = message.get("voice")
                        sender_chat_id = str(message.get("chat", {}).get("id", ""))

                        # Security Check: Only allow commands from authorized chat ID
                        if not chat_id:
                            clean_t = str(text or "").strip()
                            if clean_t.lower().startswith("/pair ") or clean_t.lower().startswith("pair "):
                                code_entered = clean_t.split(" ", 1)[1].strip()
                                import time
                                if _telegram_pairing_code and time.time() < _telegram_pairing_expiry and code_entered == _telegram_pairing_code:
                                    logger.info(f"[VAVE] Telegram successfully paired with Chat ID: {sender_chat_id}")
                                    update_setting("telegram_chat_id", sender_chat_id)
                                    chat_id = sender_chat_id
                                    _telegram_pairing_code = None
                                    send_telegram_message(token, chat_id, "✅ VAVE Remote Link Established. Paired successfully!")
                                    continue
                                else:
                                    send_telegram_message(token, sender_chat_id, "❌ Invalid or expired pairing code. Ask your desktop assistant for a new code by saying 'pair telegram'.")
                                    continue
                            else:
                                send_telegram_message(token, sender_chat_id, "🔒 This VAVE assistant is unpaired. To link your phone, say 'pair telegram' on your desktop and reply with: /pair <6-digit-code>")
                                continue

                        if sender_chat_id != chat_id:
                            if text or voice:
                                logger.info(f"[VAVE] Unauthorized access attempt from Chat ID: {sender_chat_id}")
                            continue

                        # Process photo if received
                        photo = message.get("photo")
                        if not text and photo:
                            file_id = photo[-1]["file_id"]
                            caption = (message.get("caption") or "").strip() or "Describe what you see in this photo in detail."
                            send_telegram_message(token, chat_id, "🔍 Analyzing photo with local vision AI...")
                            analysis = _analyze_telegram_photo(token, file_id, caption)
                            send_telegram_message(token, chat_id, f"🔍 Vision Analysis:\n{analysis}")
                            continue

                        # Transcribe voice note if received
                        if not text and voice:
                            file_id = voice.get("file_id")
                            if file_id:
                                send_telegram_message(token, chat_id, "🎙️ Transcribing voice note...")
                                recognized = _transcribe_telegram_voice(token, file_id)
                                if recognized:
                                    send_telegram_message(token, chat_id, f"🎙️ Recognized: \"{recognized}\"")
                                    text = recognized
                                else:
                                    send_telegram_message(token, chat_id, "⚠️ Could not transcribe voice note.")
                                    continue

                        if text:
                            logger.info(f"\n[Telegram Message Received]: {text}")
                            text_clean = text.strip()
                            text_lower = text_clean.lower()

                            # Fast-path: Direct desktop screenshot send to phone
                            if text_lower in ["screenshot", "send screenshot", "take screenshot", "/screenshot"]:
                                send_telegram_message(token, chat_id, "📸 Capturing desktop screenshot...")
                                res = send_telegram_screenshot()
                                if not res.startswith("Captured"):
                                    send_telegram_message(token, chat_id, f"⚠️ {res}")
                                continue
                            
                            if text_lower in ["/start", "start"]:
                                send_telegram_message(
                                    token,
                                    chat_id,
                                    "Greetings! VAVE Remote Link is active and connected to your desktop. How can I assist you, Sir?"
                                )
                                continue

                            if text_lower in ["/help", "help"]:
                                send_telegram_message(
                                    token,
                                    chat_id,
                                    "VAVE Telegram Bridge Commands:\n"
                                    "- Ask any question (uses local Ollama AI brain)\n"
                                    "- Send a photo (analyzed with local moondream vision AI)\n"
                                    "- Send a voice note (transcribed and executed)\n"
                                    "- 'screenshot' or 'send screenshot' (receives desktop snapshot)\n"
                                    "- 'time' or 'date' (check system clock)\n"
                                    "- 'battery' (hardware battery status)\n"
                                    "- 'take a note <text>' (save persistent note)\n"
                                    "- 'read notes' (view notes)\n"
                                    "- Tap [Approve] / [Deny] buttons or '/yes <id>' / '/no <id>'\n"
                                    "- '/kill' (emergency kill switch)"
                                )
                                continue

                            if text_lower.startswith("/yes ") or text_lower.startswith("/no "):
                                from assistant import confirm
                                parts = text_lower.split(" ")
                                if len(parts) >= 2:
                                    approved = text_lower.startswith("/yes")
                                    confirm.resolve(parts[1], approved)
                                    send_telegram_message(token, chat_id, f"Confirmation received ({parts[1]}).")
                                continue
                            if text_lower == "/kill":
                                from assistant.safety_stop import hard_stop
                                hard_stop(source="telegram")
                                send_telegram_message(token, chat_id, "🚨 Kill switch activated. All tasks halted and overwatch stopped.")
                                continue
                                
                            speak(f"Incoming remote command: {text_clean}")
                            queued = _enqueue_command(text_clean)
                            if queued > 1:
                                send_telegram_message(
                                    token, chat_id,
                                    f"Queued. {queued - 1} command(s) "
                                    "ahead of it.")
                                
        except urllib.error.HTTPError as e:
            if e.code == 409:
                logger.info(f"[Telegram Error 409 Conflict]: Another VAVE process is already running and listening to this bot. Please close all other terminal windows running VAVE!")
                time.sleep(15) # Wait longer so it doesn't spam
            elif e.code in (401, 404):
                # A rejected token never becomes valid by retrying, and the old
                # 5-second retry here is exactly what filled the log with 404s.
                # Stop the bridge and say why, once.
                logger.error("[Telegram] Bot token rejected (HTTP %s). Stopping "
                             "the Telegram bridge - check telegram_bot_token.",
                             e.code)
                break
            else:
                logger.info(f"[Telegram Network Error]: {e}")
                time.sleep(5)
        except urllib.error.URLError as e:
            # Network issue, wait and retry
            logger.info(f"[Telegram Network Error]: {e}")
            time.sleep(5)
        except Exception as e:
            logger.info(f"[Telegram Sync Error]: {e}")
            time.sleep(5)
            
        time.sleep(1)
    
    # Release the lock when the poller stops
    _release_lock()

def _enqueue_command(text):
    """Hand a command to the single worker. Returns how many are waiting."""
    _command_queue.put(text)
    return _command_queue.qsize()


def _command_worker():
    """Run queued phone commands one after another, forever."""
    call_context.set_origin("telegram")
    while _telegram_active:
        try:
            command = _command_queue.get(timeout=1)
        except queue.Empty:
            continue

        try:
            execute_command(command, auto_confirm=True)
        except Exception as error:
            logger.error(f"Telegram command error: {error}")
            speak(f"Error executing command: {error}")
        finally:
            _command_queue.task_done()


def start_telegram_sync():
    global _telegram_thread, _telegram_active
    
    if not get_setting("telegram_bot_token", ""):
        return
    
    # Only one process may poll. Child processes spawned by numpy/MKL are blocked here.
    if not _acquire_lock():
        return
        
    _telegram_active = True
    
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = call_context.spawn_thread(
            target=_command_worker, daemon=True, name="telegram-commands")

    if _telegram_thread is None or not _telegram_thread.is_alive():
        _telegram_thread = threading.Thread(target=_telegram_worker, daemon=True)
        _telegram_thread.start()

def stop_telegram_sync():
    global _telegram_active
    _telegram_active = False
    _release_lock()

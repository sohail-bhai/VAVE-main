import logging
logger = logging.getLogger(__name__)

import sys
from assistant.config import get_setting

MIC_CALIBRATED = False
_speech_enabled = True
_engine = None
_recognizer = None
_speech_recognition_module = None
_listen_status_callback = None
_assistant_response_callback = None
_speech_error_callback = None
_speech_started_callback = None
_speech_finished_callback = None


def set_speech_event_hooks(
    status_callback=None,
    response_callback=None,
    error_callback=None,
    speech_started_callback=None,
    speech_finished_callback=None,
):
    global _listen_status_callback
    global _assistant_response_callback
    global _speech_error_callback
    global _speech_started_callback
    global _speech_finished_callback

    _listen_status_callback = status_callback
    _assistant_response_callback = response_callback
    _speech_error_callback = error_callback
    _speech_started_callback = speech_started_callback
    _speech_finished_callback = speech_finished_callback


def clear_speech_event_hooks():
    set_speech_event_hooks()


def set_speech_enabled(enabled):
    global _speech_enabled
    _speech_enabled = bool(enabled)


def is_speech_enabled():
    return _speech_enabled


def _notify(callback, *args):
    if callback is None:
        return

    try:
        callback(*args)
    except Exception as error:
        logger.info("Speech callback error:", error)


def _emit_listen_status(message, status_callback=None):
    _notify(status_callback, message)
    _notify(_listen_status_callback, message)


def _emit_speech_error(message, error=None, error_callback=None):
    _notify(error_callback, message, error)

    if error_callback is not _speech_error_callback:
        _notify(_speech_error_callback, message, error)


def _get_speech_recognition_module():
    global _speech_recognition_module

    if _speech_recognition_module is None:
        import speech_recognition as sr
        _speech_recognition_module = sr

    return _speech_recognition_module


def _apply_voice_settings(engine):
    voices = engine.getProperty("voices")

    if voices:
        engine.setProperty("voice", voices[0].id)

    engine.setProperty("rate", int(get_setting("voice_rate", 170)))
    engine.setProperty("volume", float(get_setting("voice_volume", 1.0)))


import asyncio

def _get_engine():
    # Deprecated
    pass

def _apply_recognizer_settings(recognizer):
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = float(get_setting("normal_pause_seconds", 2.0))
    recognizer.phrase_threshold = 0.3
    recognizer.non_speaking_duration = 0.6

def _get_recognizer():
    global _recognizer

    if _recognizer is None:
        sr = _get_speech_recognition_module()
        _recognizer = sr.Recognizer()
        _apply_recognizer_settings(_recognizer)

    return _recognizer

def setup_voice():
    pass

_piper_voice = None

def _get_piper_voice():
    global _piper_voice
    if _piper_voice is None:
        try:
            from piper.voice import PiperVoice
            import os
            from pathlib import Path
            
            voice_model_path = Path(__file__).resolve().parent.parent / "data" / "voices" / "en_US-ryan-medium.onnx"
            if voice_model_path.exists():
                logger.info("[VAVE] Loading Offline Piper TTS Voice Model...")
                _piper_voice = PiperVoice.load(str(voice_model_path))
        except ImportError:
            pass
    return _piper_voice

import queue
import threading
import uuid

_speech_queue = queue.Queue()
_speech_thread = None

def _audio_worker():
    import os
    import asyncio
    
    while True:
        item = _speech_queue.get()
        if item is None:
            break
        text, start_cb, end_cb = item
        start_cb(text)
        
        file_id = str(uuid.uuid4())
        
        try:
            import edge_tts
            import pygame
            temp_audio = f"temp_speech_{file_id}.mp3"
            
            async def _generate():
                communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
                await communicate.save(temp_audio)
                
            asyncio.run(_generate())
            
            pygame.mixer.init()
            pygame.mixer.music.load(temp_audio)
            pygame.mixer.music.play()
            
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
                
            pygame.mixer.quit()
            try:
                os.remove(temp_audio)
            except:
                pass
                
        except Exception as e:
            logger.info(f"[VAVE] Cloud voice unreachable ({e}). Falling back to offline voice...")
            try:
                voice = _get_piper_voice()
                if voice:
                    import wave
                    import winsound
                    temp_wav = f"temp_speech_{file_id}.wav"
                    with wave.open(temp_wav, 'wb') as wav_file:
                        voice.synthesize_wav(text, wav_file)
                        
                    winsound.PlaySound(temp_wav, winsound.SND_FILENAME)
                    try:
                        os.remove(temp_wav)
                    except:
                        pass
                else:
                    # Hard fallback to built-in Windows pyttsx3
                    from assistant.config import get_setting
                    logger.info("[VAVE] High-quality offline voice missing. Falling back to pyttsx3...")
                    import pyttsx3
                    engine = pyttsx3.init()
                    engine.setProperty('rate', get_setting("voice_rate", 170))
                    engine.setProperty('volume', get_setting("voice_volume", 1.0))
                    engine.say(text)
                    engine.runAndWait()
            except Exception as error:
                logger.info("Speech output error:", error)
        
        finally:
            end_cb(text)
            _speech_queue.task_done()

def start_speech_thread():
    global _speech_thread
    if _speech_thread is None or not _speech_thread.is_alive():
        _speech_thread = threading.Thread(target=_audio_worker, daemon=True)
        _speech_thread.start()

def interrupt_speech():
    """Instantly clears the speech queue and stops any currently playing audio."""
    import pygame
    
    # 1. Clear the queue
    with _speech_queue.mutex:
        _speech_queue.queue.clear()
        
    # 2. Stop playing audio
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
    except Exception:
        pass
    
    logger.info("\n[VAVE] Audio Interrupted!\n")

def speak(text):
    assistant_name = get_setting("assistant_name", "VAVE")
    try:
        logger.info(f"{assistant_name.upper()}: {text}")
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding)
        logger.info(f"{assistant_name.upper()}: {safe_text}")
    _notify(_assistant_response_callback, text)

    # Automatically send response back to Telegram if command originated from Telegram
    try:
        from assistant import call_context
        if call_context.get_origin() == "telegram":
            from assistant.telegram_sync import send_telegram_message
            from assistant.control.secrets import resolve_setting
            tok = resolve_setting(get_setting("telegram_bot_token", ""))
            cid = get_setting("telegram_chat_id", "")
            if tok and cid:
                send_telegram_message(tok, cid, text)
    except Exception as e:
        logger.debug(f"Telegram speech forward note: {e}")

    if not _speech_enabled:
        return

    start_speech_thread()
    
    def start_cb(t):
        _notify(_speech_started_callback, t)
        
    def end_cb(t):
        _notify(_speech_finished_callback, t)
        
    _speech_queue.put((text, start_cb, end_cb))

def listen(
    timeout=8,
    max_phrase_time=None,
    pause_seconds=None,
    status_callback=None,
    error_callback=None,
):
    """
    Smart listening:
    - Starts listening when you speak.
    - Keeps listening while you speak.
    - Stops only after you pause for pause_seconds.
    """

    global MIC_CALIBRATED

    try:
        sr = _get_speech_recognition_module()
        recognizer = _get_recognizer()
    except Exception as error:
        _emit_speech_error("Speech recognition package is not available.", error, error_callback)
        speak("Speech recognition is not available in this Python environment.")
        logger.info("Speech recognition import error:", error)
        return ""

    if max_phrase_time is None:
        max_phrase_time = int(get_setting("normal_max_phrase_time", 25))

    if pause_seconds is None:
        pause_seconds = float(get_setting("normal_pause_seconds", 2.0))

    old_pause_threshold = recognizer.pause_threshold
    recognizer.pause_threshold = pause_seconds

    try:
        with sr.Microphone() as source:
            if not MIC_CALIBRATED:
                message = "Calibrating microphone"
                logger.info(f"{message}. Stay silent for 1 second...")
                _emit_listen_status(message, status_callback)
                recognizer.adjust_for_ambient_noise(source, duration=1)
                MIC_CALIBRATED = True
                logger.info("Microphone ready.")

            logger.info("\nListening...")
            _emit_listen_status("Listening", status_callback)
            audio = recognizer.listen(
                source,
                timeout=timeout,
                phrase_time_limit=max_phrase_time
            )

        logger.info("Recognizing...")
        _emit_listen_status("Recognizing", status_callback)

        command = recognizer.recognize_google(audio)
        command = command.lower().strip()

        logger.info(f"You: {command}")
        return command

    except sr.WaitTimeoutError:
        logger.info("No speech detected.")
        _emit_listen_status("No speech detected", status_callback)
        return ""

    except sr.UnknownValueError as error:
        _emit_listen_status("Could not understand", status_callback)
        _emit_speech_error("Could not understand speech.", error, error_callback)
        speak("Sorry, I could not understand. Please try again.")
        return ""

    except sr.RequestError as error:
        _emit_speech_error("Speech recognition service is not working.", error, error_callback)
        speak("Speech recognition service is not working. Please check your internet.")
        return ""

    except Exception as error:
        _emit_speech_error("Microphone error occurred.", error, error_callback)
        speak("Microphone error occurred.")
        logger.info("Error:", error)
        return ""

    finally:
        recognizer.pause_threshold = old_pause_threshold

import logging
logger = logging.getLogger(__name__)

import threading
import pyaudio
import numpy as np
import time

_wakeword_thread = None
_wakeword_active = False
_wakeword_callback = None

def _load_wakeword_model():
    """Build an openwakeword model, or return None with a plain-English reason.

    The old code hard-coded "hey_vave", which is not one of the pretrained
    models openwakeword ships, so every startup threw "Could not find
    pretrained model". The model is now configurable and defaults to a real
    bundled one; a custom-trained file can be pointed at by path. Anything that
    goes wrong here disables hands-free mode quietly rather than crashing the
    thread.
    """
    from openwakeword.model import Model

    from assistant.config import get_setting
    choice = str(get_setting("wakeword_model", "hey_jarvis")).strip() or "hey_jarvis"

    # A path to a custom-trained model is used as-is; a bare name is a bundled
    # model. The pinned openwakeword (0.5.1) ships every model with the package,
    # so there is nothing to fetch; newer versions expose a downloader, so we
    # fetch on demand only when that function actually exists.
    import os
    is_path = choice.lower().endswith((".onnx", ".tflite")) or os.path.exists(choice)
    if not is_path:
        import openwakeword
        downloader = getattr(getattr(openwakeword, "utils", None),
                             "download_models", None)
        if callable(downloader):
            try:
                downloader([choice])
            except Exception as e:
                logger.info(f"[VAVE] Could not fetch wake word model '{choice}': {e}")

    try:
        model = Model(wakeword_models=[choice], inference_framework="onnx")
    except Exception as e:
        logger.info(f"[VAVE] Wake word model '{choice}' unavailable ({e}); "
                    "hands-free mode disabled. Set 'wakeword_model' in config "
                    "to a bundled name (e.g. hey_jarvis) or a .onnx file.")
        return None

    logger.info(f"[VAVE] Wake word ready. Listening model: '{choice}'.")
    return model


def _wakeword_worker():
    global _wakeword_active
    try:
        owwModel = _load_wakeword_model()
        if owwModel is None:
            return

        from assistant.config import get_setting
        threshold = float(get_setting("wakeword_threshold", 0.6))

        audio = pyaudio.PyAudio()
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 16000
        CHUNK = 1280

        while True:
            if not _wakeword_active:
                time.sleep(0.5)
                continue

            try:
                mic_stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
                logger.info("[VAVE] Wake word listening started.")

                while _wakeword_active:
                    data = np.frombuffer(mic_stream.read(CHUNK, exception_on_overflow=False), dtype=np.int16)
                    prediction = owwModel.predict(data)

                    # Only one model is loaded, so the top score is that model's.
                    score = max(prediction.values()) if prediction else 0.0
                    if score > threshold:
                        logger.info(f"\n[VAVE] Wake word detected! (Score: {score})")
                        if _wakeword_callback:
                            # We MUST close stream before callback so the main microphone can be used by speech_recognition
                            mic_stream.stop_stream()
                            mic_stream.close()

                            _wakeword_callback()

                        # Wait a bit after callback completes
                        time.sleep(1)
                        break

                # If _wakeword_active became false during the inner loop
                if not mic_stream.is_stopped():
                    mic_stream.stop_stream()
                    mic_stream.close()

            except Exception as e:
                logger.info(f"[WakeWord Error] Microphone error: {e}")
                time.sleep(2)

    except ImportError:
        logger.info("[VAVE] openwakeword not installed. Hands-free mode disabled.")
    except Exception as e:
        logger.info(f"[VAVE] Failed to start wake word engine: {e}")

def start_wakeword_engine(callback):
    global _wakeword_thread, _wakeword_active, _wakeword_callback
    _wakeword_callback = callback
    _wakeword_active = True
    
    if _wakeword_thread is None or not _wakeword_thread.is_alive():
        _wakeword_thread = threading.Thread(target=_wakeword_worker, daemon=True)
        _wakeword_thread.start()

def pause_wakeword():
    global _wakeword_active
    _wakeword_active = False

def resume_wakeword():
    global _wakeword_active
    _wakeword_active = True

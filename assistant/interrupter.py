import logging
logger = logging.getLogger(__name__)

import keyboard
from assistant.speech import interrupt_speech

_listener_started = False

def setup_interrupter():
    global _listener_started
    if not _listener_started:
        try:
            keyboard.add_hotkey('ctrl+shift+space', interrupt_speech)
            
            def trigger_kill_switch():
                from assistant.safety_stop import hard_stop
                hard_stop(source="hotkey")
                logger.info("[VAVE] HARD STOP & KILL SWITCH ACTIVATED VIA HOTKEY")
                
            keyboard.add_hotkey('ctrl+alt+shift+k', trigger_kill_switch)
            
            _listener_started = True
            logger.info("[VAVE] Interrupter Hotkey (Ctrl+Shift+Space) registered.")
            logger.info("[VAVE] Kill Switch Hotkey (Ctrl+Alt+Shift+K) registered.")
        except Exception as e:
            logger.info(f"[VAVE] Failed to register interrupter hotkey: {e}")

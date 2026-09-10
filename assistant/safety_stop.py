"""Unified Emergency Stop & Kill Switch for VAVE.

Trips the kill switch, stops overwatch, halts speech, and calls
control plane emergency stop across all triggers:
GUI button, hotkey, Telegram /kill, and API.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def hard_stop(source: str = "manual") -> Dict[str, Any]:
    """Trips kill switch, stops overwatch, halts speech, and pauses control plane."""
    result = {
        "source": source,
        "kill_switch_tripped": False,
        "overwatch_stopped": False,
        "speech_interrupted": False,
        "control_plane": None,
    }

    # 1. Trip the safety guard kill switch (blocks all future tool executions)
    try:
        from assistant import guard
        guard.set_kill_switch(True)
        result["kill_switch_tripped"] = True
        logger.warning(f"[HARD STOP] Guard kill switch activated (source={source}).")
    except Exception as e:
        logger.error(f"[HARD STOP] Failed setting guard kill switch: {e}")

    # 2. Stop overwatch scanner/engine
    try:
        from assistant.overwatch import engine as overwatch_engine
        if hasattr(overwatch_engine, "_engine") and overwatch_engine._engine._active:
            overwatch_engine._engine.stop()
            result["overwatch_stopped"] = True
            logger.warning("[HARD STOP] Overwatch engine stopped.")
    except Exception as e:
        logger.debug(f"[HARD STOP] Overwatch stop note: {e}")

    # 3. Halt text-to-speech / audio output
    try:
        from assistant.speech import interrupt_speech
        interrupt_speech()
        result["speech_interrupted"] = True
    except Exception as e:
        logger.debug(f"[HARD STOP] Speech interrupt note: {e}")

    # 4. Invoke control plane emergency stop if active
    try:
        from assistant.control.service import _control_plane
        if _control_plane is not None:
            plane_res = _control_plane.emergency_stop()
            result["control_plane"] = plane_res
            logger.warning(f"[HARD STOP] Control plane emergency stop executed: {plane_res}")
    except Exception as e:
        logger.debug(f"[HARD STOP] Control plane emergency stop note: {e}")

    return result


def reset_hard_stop():
    """Resets kill switch and allows new work."""
    from assistant import guard
    guard.set_kill_switch(False)
    try:
        from assistant.control.service import _control_plane
        if _control_plane is not None:
            _control_plane.resume()
    except Exception:
        pass

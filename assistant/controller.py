import logging
logger = logging.getLogger(__name__)

import traceback

from assistant.commands import execute_command
from assistant.config import get_setting
from assistant.events import (
    EVENT_ASSISTANT_RESPONSE,
    EVENT_ERROR,
    EVENT_RECOGNIZED_TEXT,
    EVENT_STATE_CHANGED,
    EVENT_STATUS,
    EventBus,
)
from assistant.speech import listen, set_speech_enabled, set_speech_event_hooks, speak
from assistant.state import AssistantState
from assistant.text_utils import normalize_command_text


class AssistantController:
    """
    Reusable assistant loop for the current CLI and a future GUI.

    The controller keeps blocking speech I/O in one place and emits queue-based
    events so a CustomTkinter UI can later poll without touching audio internals.
    """

    def __init__(self, event_bus=None, configure_speech_hooks=True, speech_enabled=True):
        self.events = event_bus or EventBus()
        self.state = AssistantState.IDLE
        self._running = False
        self._listening = False
        self._state_before_speaking = None
        
        # Override passed speech_enabled if it exists in config
        config_speech = get_setting("speech_enabled", speech_enabled)
        set_speech_enabled(config_speech)

        if configure_speech_hooks:
            set_speech_event_hooks(
                response_callback=self._on_assistant_response,
                error_callback=self._on_speech_error,
                speech_started_callback=self._on_speech_started,
                speech_finished_callback=self._on_speech_finished,
            )
            
        # Register Graceful Interrupter Hotkey
        try:
            from assistant.interrupter import setup_interrupter
            setup_interrupter()
        except ImportError:
            pass

        # Register Wake-Word Engine
        try:
            from assistant.wakeword import start_wakeword_engine, pause_wakeword
            start_wakeword_engine(self._on_wakeword_detected)
            if not get_setting("wake_word_enabled", True):
                pause_wakeword()
        except ImportError:
            pass

        # Pre-import control plane modules on the main thread to prevent thread import race conditions
        try:
            import assistant.control.service
            import assistant.control.secrets
            import assistant.control.executor
        except Exception:
            pass

        # Register Telegram Remote Sync
        try:
            from assistant.telegram_sync import start_telegram_sync
            start_telegram_sync()
        except ImportError:
            pass

        # Auto-resume interrupted control plane tasks
        if get_setting("auto_resume_tasks", True):
            self._start_task_auto_resume()

    def _start_task_auto_resume(self):
        def _runner():
            try:
                from assistant.control.service import get_control_plane
                from assistant.control.executor import get_executor
                plane = get_control_plane()
                executor = get_executor(plane=plane)
                resumed = executor.resume_interrupted()
                if resumed:
                    logger.info(f"[VAVE] Automatically resumed {len(resumed)} interrupted task(s).")
            except Exception as e:
                logger.debug(f"[VAVE] Task auto-resume skipped: {e}")

        import threading
        threading.Thread(target=_runner, daemon=True, name="task-auto-resume").start()

    def _on_wakeword_detected(self):
        """Called by the background thread when 'Hey Vave' is heard."""
        from assistant.speech import interrupt_speech
        from assistant.wakeword import pause_wakeword
        
        # Instantly silence any ongoing audio
        interrupt_speech()
        
        # Pause wake word so it doesn't conflict with main mic
        pause_wakeword()
        
        # Run one loop of listening
        self.run_once()
        
        # Resume wake word engine after we're done, only if not explicitly disabled
        from assistant.config import get_setting
        if get_setting("wake_word_enabled", True):
            from assistant.wakeword import resume_wakeword
            resume_wakeword()

    def start(self, greet=True):
        self.run_forever(greet=greet)

    def run_forever(self, greet=True):
        if self._running:
            self.events.emit(EVENT_STATUS, "Assistant is already running.")
            return

        self._running = True
        self.set_state(AssistantState.IDLE)

        if greet:
            self.greet()

        while self._running:
            self.run_once()

        self.set_state(AssistantState.STOPPED)

    def handle_text_command(self, command_text):
        self.events.emit(EVENT_STATUS, "Processing text command.")
        self.set_state(AssistantState.IDLE)
        return self.process_command(command_text)

    def stop(self):
        self._running = False
        self.set_state(AssistantState.STOPPED)

    def greet(self):
        user_name = get_setting("user_name", "Sir")
        assistant_name = get_setting("assistant_name", "Vave")

        speak(f"Hello {user_name}, I am {assistant_name}. Version 1.2 is ready.")
        speak("How can I help you?")

    def run_once(self):
        import threading
        if not hasattr(self, 'audio_lock'):
            self.audio_lock = threading.Lock()
            
        if not self.audio_lock.acquire(blocking=False):
            return True

        try:
            if self._listening:
                self.events.emit(EVENT_STATUS, "Listening is already in progress.")
                return True

            self._listening = True
            try:
                command = self._listen_for_command()
                if not command:
                    self.set_state(AssistantState.IDLE)
                    return True
                return self.process_command(command)
            finally:
                self._listening = False
        finally:
            self.audio_lock.release()

    def process_command(self, command):
        command = normalize_command_text(command)

        if not command:
            self.set_state(AssistantState.IDLE)
            return True

        self.events.emit(EVENT_RECOGNIZED_TEXT, command, text=command)
        self.set_state(AssistantState.PROCESSING)

        try:
            should_continue = execute_command(command)
        except Exception as error:
            self.set_state(AssistantState.ERROR)
            self.events.emit(
                EVENT_ERROR,
                "Command processing failed.",
                error=str(error),
                command=command,
            )
            logger.info("Command processing error:", error)
            traceback.print_exc()
            speak("Something went wrong while processing that command.")
            should_continue = True

        if should_continue:
            self.set_state(AssistantState.IDLE)
        else:
            self.stop()

        return should_continue

    def set_state(self, state):
        if not isinstance(state, AssistantState):
            state = AssistantState(state)

        previous_state = self.state

        if previous_state == state:
            return

        self.state = state
        self.events.emit(
            EVENT_STATE_CHANGED,
            state.value,
            previous_state=previous_state.value,
            state=state.value,
        )

    def _listen_for_command(self):
        timeout = int(get_setting("listen_timeout_seconds", 8))
        max_phrase_time = int(
            get_setting(
                "listen_phrase_time_limit_seconds",
                get_setting("normal_max_phrase_time", 25)
            )
        )
        pause_seconds = float(
            get_setting(
                "listen_pause_threshold_seconds",
                get_setting("normal_pause_seconds", 2.0)
            )
        )

        return listen(
            timeout=timeout,
            max_phrase_time=max_phrase_time,
            pause_seconds=pause_seconds,
            status_callback=self._on_listen_status,
            error_callback=self._on_speech_error,
        )

    def _on_listen_status(self, status):
        self.events.emit(EVENT_STATUS, status)
        lowered_status = status.lower()

        if "calibrating" in lowered_status:
            self.set_state(AssistantState.CALIBRATING)
        elif "listening" in lowered_status:
            self.set_state(AssistantState.LISTENING)
        elif "recognizing" in lowered_status:
            self.set_state(AssistantState.RECOGNIZING)
        elif "could not understand" in lowered_status:
            self.set_state(AssistantState.ERROR)
        elif "no speech" in lowered_status:
            self.set_state(AssistantState.IDLE)

    def _on_assistant_response(self, text):
        self.events.emit(EVENT_ASSISTANT_RESPONSE, text, text=text)

    def _on_speech_started(self, text):
        if self.state != AssistantState.SPEAKING:
            self._state_before_speaking = self.state
            self.set_state(AssistantState.SPEAKING)

    def _on_speech_finished(self, text):
        if self.state != AssistantState.SPEAKING:
            return

        next_state = self._state_before_speaking or AssistantState.IDLE
        self._state_before_speaking = None

        if self._running and next_state != AssistantState.STOPPED:
            self.set_state(next_state)

    def _on_speech_error(self, message, error=None):
        payload = {"error": str(error)} if error is not None else {}
        self.events.emit(EVENT_ERROR, message, **payload)

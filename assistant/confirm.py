import queue
import threading
import uuid
import logging
from assistant import events
from assistant import speech

logger = logging.getLogger(__name__)

class ConfirmationBroker:
    def __init__(self):
        self._pending = {}
        self._lock = threading.Lock()
        self._bus = None
        
    def configure(self, bus: events.EventBus):
        self._bus = bus
        
    def request_confirmation(self, message: str, origin: str) -> bool:
        if origin == "voice":
            return self._ask_voice(message)
            
        return self._ask_async_channel(message, origin)
        
    def _ask_voice(self, message: str) -> bool:
        speech.speak(message)
        response = speech.listen()
        if not response:
            return False
            
        lower = response.lower()
        if any(w in lower for w in ["yes", "yeah", "do it", "sure", "proceed"]):
            return True
        return False
        
    def _ask_async_channel(self, message: str, origin: str) -> bool:
        if not self._bus:
            logger.warning("ConfirmationBroker has no EventBus. Denying.")
            return False

        # Short enough to type back on a phone. Only one phone command runs at a
        # time, so a 6-character id is plenty to avoid a clash.
        req_id = uuid.uuid4().hex[:6]
        q = queue.Queue(maxsize=1)

        with self._lock:
            self._pending[req_id] = q

        self._bus.emit(
            events.EVENT_CONFIRM_REQUEST,
            message,
            req_id=req_id,
            origin=origin
        )

        # The bus event drives the GUI approval card. A phone command has no GUI
        # in front of the user, so the request is also sent to Telegram, where
        # "/yes <id>" and "/no <id>" resolve it. Without this the request was
        # invisible and every phone command timed out after 60s.
        if origin == "telegram":
            self._send_to_telegram(message, req_id)

        try:
            result = q.get(timeout=60.0)
            return result
        except queue.Empty:
            logger.warning(f"Confirmation {req_id} timed out.")
            return False
        finally:
            with self._lock:
                self._pending.pop(req_id, None)

    def _send_to_telegram(self, message: str, req_id: str) -> None:
        try:
            from assistant.config import get_setting
            from assistant.control.secrets import resolve_setting
            from assistant.telegram_sync import send_telegram_message
            token = resolve_setting(get_setting("telegram_bot_token", ""))
            chat_id = get_setting("telegram_chat_id", "")
            if token and chat_id:
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "✅ Approve", "callback_data": f"/yes {req_id}"},
                            {"text": "❌ Deny", "callback_data": f"/no {req_id}"}
                        ]
                    ]
                }
                text = (
                    f"VAVE needs your approval:\n{message}\n\n"
                    f"Tap a button below, or reply  /yes {req_id}  to approve,  /no {req_id}  to cancel."
                )
                try:
                    send_telegram_message(token, chat_id, text, reply_markup=reply_markup)
                except TypeError:
                    send_telegram_message(token, chat_id, text)
        except Exception as e:
            logger.debug(f"Could not forward confirmation to Telegram: {e}")
                
    def resolve(self, req_id: str, approved: bool) -> bool:
        with self._lock:
            q = self._pending.get(req_id)
            
        if q:
            try:
                q.put_nowait(approved)
                return True
            except queue.Full:
                pass
        return False
        
_broker = ConfirmationBroker()

def configure(event_bus: events.EventBus) -> None:
    _broker.configure(event_bus)

def ask(message: str, origin: str) -> bool:
    return _broker.request_confirmation(message, origin)

def resolve(req_id: str, approved: bool) -> bool:
    return _broker.resolve(req_id, approved)

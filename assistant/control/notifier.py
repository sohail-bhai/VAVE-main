"""Decides which events are worth interrupting a person for, and delivers them.

The timeline records everything. A notification is different: it goes to a
phone that may be in someone's pocket, so the bar is higher. Only things a
person would want to know about while away from the computer are sent -
approvals waiting on them, work that finished or failed, an agent that went
wrong, a security event.

Channels are injectable and failures are contained: a broken channel must
never stop the work that produced the event.
"""

import logging
import queue
import threading

from assistant.control.models import EventType

logger = logging.getLogger(__name__)

# What is worth a person's attention, and how urgent each one is.
NOTIFY_ON = {
    EventType.APPROVAL_REQUESTED: "action",
    EventType.TASK_COMPLETED: "done",
    EventType.TASK_FAILED: "problem",
    EventType.TASK_CANCELLED: "done",
    EventType.CAPABILITY_DENIED: "problem",
    EventType.AGENT_OFFLINE: "problem",
    EventType.AGENT_QUARANTINED: "security",
    EventType.DEVICE_DISCONNECTED: "problem",
    EventType.EMERGENCY_STOP: "security",
}

# The most recent notifications a client can catch up on after reconnecting.
HISTORY = 50


# Marker queued by Notifier.flush(); the worker sets the paired event once
# every earlier delivery has finished.
class _FlushMarker:
    pass


class Notification:
    """One thing worth telling a person, ready for any channel."""

    def __init__(self, event, urgency, read=False):
        self.event = event
        self.urgency = urgency
        self.read = bool(read)

    @property
    def needs_answer(self):
        return self.event.type == EventType.APPROVAL_REQUESTED

    def to_dict(self):
        return {
            "id": self.event.id,
            "type": self.event.type.value,
            "urgency": self.urgency,
            "message": self.event.message,
            "task_id": self.event.task_id,
            "agent_id": self.event.agent_id,
            "capability": self.event.capability,
            "risk": self.event.risk,
            "approval_id": self.event.approval_id,
            "needs_answer": self.needs_answer,
            "read": self.read,
            "timestamp": self.event.timestamp,
        }

    def as_text(self):
        """One line for a channel that can only carry words."""
        prefix = {"action": "Needs you", "problem": "Problem",
                  "security": "Security", "done": "Done"}.get(self.urgency, "VAVE")
        return f"{prefix}: {self.event.message}"


class TelegramChannel:
    """Sends a line to the phone through the existing Telegram bridge."""

    name = "telegram"

    def __init__(self, send=None, token=None, chat_id=None):
        self._send = send
        self._token = token
        self._chat_id = chat_id

    def deliver(self, notification):
        if self._send is not None:
            self._send(notification.as_text())
            return

        from assistant.config import get_setting
        from assistant.control.secrets import resolve_setting
        from assistant.telegram_sync import send_telegram_message

        token = resolve_setting(self._token or get_setting("telegram_bot_token", ""))
        chat_id = self._chat_id or get_setting("telegram_chat_id", "")
        if not token or not chat_id:
            return          # not configured; not an error

        send_telegram_message(token, chat_id, notification.as_text())


class Notifier:
    """Watches the control plane and pushes what matters to every channel."""

    def __init__(self, plane, channels=None, rules=None, history=HISTORY):
        self.plane = plane
        self.rules = dict(rules or NOTIFY_ON)
        self.channels = list(channels or [])
        self._history = []
        self._history_limit = history
        self._lock = threading.RLock()
        self._queue = queue.Queue()
        self._worker = threading.Thread(target=self._deliver_loop,
                                        daemon=True, name="vave-notifier")
        self._worker.start()
        self._unsubscribe = plane.subscribe(self._on_event)

    def close(self):
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._queue.put(None)

    def flush(self, timeout=10.0):
        """Wait until everything queued so far is delivered. True if so.

        Tests use this to assert delivery deterministically; production code
        never needs it (delivery is fire-and-forget by design).
        """
        done = threading.Event()
        self._queue.put((_FlushMarker, done))
        return done.wait(timeout)

    def add_channel(self, channel):
        with self._lock:
            self.channels.append(channel)
        return channel

    def remove_channel(self, channel):
        with self._lock:
            if channel in self.channels:
                self.channels.remove(channel)

    def recent(self, limit=20, unread_only=False):
        effective_limit = min(limit, self._history_limit) if self._history_limit else limit
        if hasattr(self.plane, "store") and self.plane.store:
            try:
                return self.plane.store.list_notifications(limit=effective_limit, unread_only=unread_only)
            except Exception as e:
                logger.debug("Failed reading notifications from store: %s", e)
        with self._lock:
            items = [item.to_dict() for item in self._history]
            if unread_only:
                items = [item for item in items if not item.get("read")]
            return items[-effective_limit:][::-1]

    def mark_read(self, notification_id):
        with self._lock:
            for item in self._history:
                if getattr(item.event, "id", None) == notification_id:
                    item.read = True
        if hasattr(self.plane, "store") and self.plane.store:
            return self.plane.store.mark_notification_read(notification_id)
        return None

    def _on_event(self, event):
        urgency = self.rules.get(event.type)
        if urgency is None:
            return          # on the timeline, not worth a buzz

        notification = Notification(event, urgency)
        if hasattr(self.plane, "store") and self.plane.store:
            try:
                self.plane.store.save_notification(notification.to_dict())
            except Exception as e:
                logger.debug("Failed saving notification to store: %s", e)

        with self._lock:
            self._history.append(notification)
            self._history = self._history[-self._history_limit:]
            channels = list(self.channels)

        # Delivery runs on the worker thread: a slow channel (Telegram with
        # no network takes the full 15s timeout) must never stall the thread
        # that recorded the event. Order is preserved; failures stay contained.
        for channel in channels:
            self._queue.put((channel, notification))

    def _deliver_loop(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            if isinstance(item, tuple) and item and item[0] is _FlushMarker:
                item[1].set()
                continue
            channel, notification = item
            try:
                channel.deliver(notification)
            except Exception:
                # A channel that is down must never stop the work.
                logger.exception("Notification channel %s failed",
                                 getattr(channel, "name", channel))

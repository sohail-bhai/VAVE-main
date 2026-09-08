"""The phone bridge: a command from Telegram must be able to reach the person
for approval, and no request may ever be built from an unresolved secret.

Two live failures motivated these:
  - Every sensitive phone command was denied after a 60s confirmation timeout,
    because the approval request was emitted on the internal bus and never sent
    to the phone. Now it is, with a short id that "/yes <id>" resolves.
  - Repeated HTTP 404s came from building a URL with the literal
    "secret://telegram_bot_token" when the secret could not be resolved.
"""

import re
import threading
import unittest
from unittest import mock

from assistant import confirm
from assistant import events
from assistant import telegram_sync


class SendGuardTests(unittest.TestCase):
    """send_telegram_message must never call the API with a bad token."""

    def test_an_unresolved_reference_never_reaches_the_network(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            telegram_sync.send_telegram_message(
                "secret://telegram_bot_token", "chat", "hello")
        urlopen.assert_not_called()

    def test_an_empty_token_never_reaches_the_network(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            telegram_sync.send_telegram_message("", "chat", "hello")
        urlopen.assert_not_called()

    def test_empty_text_is_a_no_op(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            telegram_sync.send_telegram_message("12345:real", "chat", "")
        urlopen.assert_not_called()

    def test_reply_markup_is_encoded_in_request(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            mock_resp = mock.MagicMock()
            urlopen.return_value.__enter__.return_value = mock_resp
            markup = {"inline_keyboard": [[{"text": "OK", "callback_data": "/yes 1234"}]]}
            telegram_sync.send_telegram_message("12345:real", "chat", "msg", reply_markup=markup)
            
            call_args = urlopen.call_args[0][0]
            self.assertIn(b"reply_markup=", call_args.data)
            self.assertIn(b"%2Fyes+1234", call_args.data)


class ConfirmationBridgeTests(unittest.TestCase):

    def setUp(self):
        self.broker = confirm.ConfirmationBroker()
        self.broker.configure(events.EventBus())
        self.sent = []

        def fake_get_setting(key, default=""):
            return {"telegram_bot_token": "12345:real",
                    "telegram_chat_id": "CID"}.get(key, default)

        self._patches = [
            mock.patch.object(telegram_sync, "send_telegram_message",
                              lambda token, chat_id, text: self.sent.append(text)),
            mock.patch("assistant.config.get_setting", fake_get_setting),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _ask_in_background(self, origin="telegram"):
        result = {}
        done = threading.Event()

        def run():
            result["approved"] = self.broker.request_confirmation(
                "I'm ready to open Netflix and click a profile.", origin)
            done.set()

        threading.Thread(target=run, daemon=True).start()
        return result, done

    def _wait_for_phone_message(self):
        for _ in range(100):
            if self.sent:
                return self.sent[-1]
            threading.Event().wait(0.02)
        self.fail("no message was ever sent to the phone")

    def test_the_request_is_sent_to_the_phone_with_a_short_id(self):
        result, done = self._ask_in_background()
        text = self._wait_for_phone_message()

        self.assertIn("approval", text.lower())
        match = re.search(r"/yes (\w+)", text)
        self.assertIsNotNone(match, "phone message must carry a /yes <id>")
        self.assertLessEqual(len(match.group(1)), 8, "id must be phone-typable")

        # Answering yes releases the waiting command.
        self.broker.resolve(match.group(1), True)
        self.assertTrue(done.wait(timeout=5))
        self.assertTrue(result["approved"])

    def test_answering_no_denies_the_action(self):
        result, done = self._ask_in_background()
        text = self._wait_for_phone_message()
        req_id = re.search(r"/no (\w+)", text).group(1)

        self.broker.resolve(req_id, False)
        self.assertTrue(done.wait(timeout=5))
        self.assertFalse(result["approved"])

    def test_a_non_telegram_async_origin_does_not_message_the_phone(self):
        result, done = self._ask_in_background(origin="gui_text")
        # Give the (nonexistent) send a chance to happen, then confirm silence.
        threading.Event().wait(0.2)
        self.assertEqual([], self.sent)
        # It is still resolvable through the bus id, just not via the phone.
        self.broker.resolve(next(iter(self.broker._pending)), True)
        self.assertTrue(done.wait(timeout=5))
        self.assertTrue(result["approved"])


if __name__ == "__main__":
    unittest.main()

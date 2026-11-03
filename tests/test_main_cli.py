import unittest
import sys
import types
from unittest.mock import Mock, patch

import assistant
import main
import vave_gui


class MainCliTests(unittest.TestCase):
    def test_server_mode_forwards_host_and_port_to_api(self):
        api_main = Mock(return_value=0)
        api_package = types.ModuleType("assistant.api")
        api_app = types.ModuleType("assistant.api.app")
        api_app.main = api_main

        with patch.dict(sys.modules, {
            "assistant.api": api_package,
            "assistant.api.app": api_app,
        }):
            assistant.api = api_package
            result = main.main(["--server", "--host", "0.0.0.0", "--port", "9876"])

        self.assertEqual(0, result)
        api_main.assert_called_once_with(["--host", "0.0.0.0", "--port", "9876"])

    def test_host_and_port_do_not_start_server_without_server_flag(self):
        with patch("main.AssistantController") as controller:
            instance = controller.return_value
            instance.handle_text_command.return_value = True

            result = main.main(["--text", "time", "--host", "0.0.0.0", "--port", "9876",
                                "--no-speech"])

        self.assertEqual(0, result)
        instance.handle_text_command.assert_called_once_with("time")

    def test_duplicate_long_lived_role_exits_before_bootstrap(self):
        lock = Mock()
        lock.acquire.return_value = False
        lock.owner_pid = 4321
        with patch(
            "assistant.reliability.instance.InstanceLock",
            return_value=lock,
        ), patch("assistant.bootstrap.bootstrap_safety") as bootstrap:
            result = main.main(["--server"])
        self.assertEqual(2, result)
        bootstrap.assert_not_called()

    def test_text_mode_disables_runtime_services(self):
        with patch("main.AssistantController") as controller, patch(
            "assistant.bootstrap.bootstrap_safety"
        ) as bootstrap:
            bootstrap.return_value = Mock()
            result = main.main(["--text", "time", "--no-speech"])
        self.assertEqual(0, result)
        bootstrap.assert_called_once_with(start_runtime_services=False)
        self.assertFalse(
            controller.call_args.kwargs["start_background_services"]
        )

    def test_direct_gui_launcher_refuses_duplicate_instance(self):
        lock = Mock()
        lock.acquire.return_value = False
        lock.owner_pid = 4321
        with patch(
            "assistant.reliability.instance.InstanceLock",
            return_value=lock,
        ):
            result = vave_gui.main()
        self.assertEqual(2, result)
        lock.release.assert_not_called()


if __name__ == "__main__":
    unittest.main()

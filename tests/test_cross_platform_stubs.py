"""Unit tests for Phase 10 Cross-Platform System Layer (Wave 6)."""

import subprocess
import unittest
from unittest import mock

from assistant.system_tasks import get_current_volume, lock_laptop, set_volume


class CrossPlatformSystemTests(unittest.TestCase):
    def test_macos_volume_get_and_set(self):
        with mock.patch("assistant.system_tasks.get_os", return_value="darwin"):
            with mock.patch("subprocess.check_output", return_value=b"72\n"):
                vol = get_current_volume()
                self.assertEqual(72, vol)

            with mock.patch("subprocess.run") as mock_run:
                with mock.patch("assistant.system_tasks.speak") as mock_speak:
                    set_volume(80)
                    mock_run.assert_called_once()
                    self.assertIn("set volume output volume 80", mock_run.call_args[0][0][2])
                    mock_speak.assert_called_with("Volume set to 80 percent.")

    def test_linux_volume_get_and_set(self):
        with mock.patch("assistant.system_tasks.get_os", return_value="linux"):
            with mock.patch("subprocess.check_output", return_value=b"Volume: front-left: 65536 / 55% / ..."):
                vol = get_current_volume()
                self.assertEqual(55, vol)

            with mock.patch("subprocess.run") as mock_run:
                with mock.patch("assistant.system_tasks.speak") as mock_speak:
                    set_volume(60)
                    mock_run.assert_called()
                    args = mock_run.call_args[0][0]
                    self.assertIn("60%", args)
                    mock_speak.assert_called_with("Volume set to 60 percent.")

    def test_cross_platform_lock_laptop(self):
        with mock.patch("assistant.system_tasks.speak") as mock_speak:
            # Darwin
            with mock.patch("assistant.system_tasks.get_os", return_value="darwin"):
                with mock.patch("os.system", return_value=0) as mock_os:
                    lock_laptop()
                    mock_os.assert_called()
                    mock_speak.assert_called_with("Locking Mac.")

            # Linux
            with mock.patch("assistant.system_tasks.get_os", return_value="linux"):
                with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as mock_run:
                    lock_laptop()
                    mock_run.assert_called()
                    mock_speak.assert_called_with("Locking system.")


if __name__ == "__main__":
    unittest.main()

"""CLI wiring for `vave doctor`."""

import unittest
from unittest import mock

import main


class DoctorCliTests(unittest.TestCase):
    def test_doctor_subcommand_expands(self):
        self.assertEqual(
            ["--doctor", "--json"],
            main._expand_subcommand(["doctor", "--json"]),
        )

    def test_doctor_routes_without_bootstrap(self):
        with mock.patch(
            "assistant.reliability.diagnostics.doctor_cli",
            return_value=1,
        ) as doctor, mock.patch("assistant.bootstrap.bootstrap_safety") as bootstrap:
            result = main.main(["doctor", "--json", "--repair-safe"])
        self.assertEqual(1, result)
        doctor.assert_called_once_with(json_output=True, repair_safe=True)
        bootstrap.assert_not_called()


if __name__ == "__main__":
    unittest.main()

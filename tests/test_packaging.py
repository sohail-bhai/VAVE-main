"""Unit tests for packaging: the `vave` subcommand spellings in main.py."""

import unittest
from unittest import mock

import main as vave_main


class SubcommandTests(unittest.TestCase):
    def test_expand_subcommand(self):
        self.assertEqual(["--gui"], vave_main._expand_subcommand(["gui"]))
        self.assertEqual(["--server"], vave_main._expand_subcommand(["serve"]))
        self.assertEqual(["--once"], vave_main._expand_subcommand(["once"]))
        self.assertEqual(["--smoke-test"], vave_main._expand_subcommand(["smoke"]))

    def test_expand_keeps_trailing_options(self):
        self.assertEqual(["--server", "--port", "9000"],
                         vave_main._expand_subcommand(["serve", "--port", "9000"]))
        self.assertEqual(["--gui", "--no-speech"],
                         vave_main._expand_subcommand(["gui", "--no-speech"]))

    def test_flags_and_unknown_words_pass_through(self):
        self.assertEqual(["--gui"], vave_main._expand_subcommand(["--gui"]))
        self.assertEqual([], vave_main._expand_subcommand([]))
        self.assertEqual(["frobnicate"], vave_main._expand_subcommand(["frobnicate"]))
        self.assertEqual(["pair"], vave_main._expand_subcommand(["pair"]))

    def test_pair_args_keeps_only_port(self):
        self.assertEqual(["--port", "9000"],
                         vave_main._pair_args(["--port", "9000"]))
        self.assertEqual([], vave_main._pair_args(["--host", "0.0.0.0"]))
        self.assertEqual(["--port", "9000"],
                         vave_main._pair_args(["--port", "9000", "--verbose"]))
        self.assertEqual([], vave_main._pair_args(["--port"]))

    def test_expanded_forms_parse(self):
        args = vave_main.build_parser().parse_args(
            vave_main._expand_subcommand(["serve", "--port", "9000"]))
        self.assertTrue(args.server)
        self.assertEqual(9000, args.port)

        args = vave_main.build_parser().parse_args(
            vave_main._expand_subcommand(["smoke"]))
        self.assertTrue(args.smoke_test)

    def test_pair_routes_to_api_entry_point(self):
        with mock.patch("assistant.api.app.main", return_value=7) as api_main:
            self.assertEqual(7, vave_main.main(["pair"]))
            api_main.assert_called_once_with(["--pair"])

    def test_pair_forwards_port(self):
        with mock.patch("assistant.api.app.main", return_value=0) as api_main:
            vave_main.main(["pair", "--port", "9000"])
            api_main.assert_called_once_with(["--pair", "--port", "9000"])

    def test_smoke_subcommand_runs_smoke_suite(self):
        with mock.patch("main.run_smoke_tests", return_value=True):
            self.assertEqual(0, vave_main.main(["smoke"]))

    def test_console_script_target_is_callable(self):
        from main import main
        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()

"""The wake word engine must never crash the app, and must load a real model.

The old code hard-coded "hey_vave", which openwakeword does not ship, so every
startup threw "Could not find pretrained model for model name 'hey_vave'". The
model is now configurable, defaults to a bundled one (hey_jarvis), and any
failure disables hands-free mode quietly instead of taking the thread down.

These load real bundled models (openwakeword ships them with the package), so
there is no network or microphone involved -- the same thing the app does at
startup, which takes a fraction of a second.
"""

import unittest
from unittest import mock

from assistant import wakeword


class LoadModelTests(unittest.TestCase):
    def _with_config(self, model_name):
        cfg = {"wakeword_model": model_name, "wakeword_threshold": 0.6}
        p = mock.patch("assistant.config.get_setting",
                       lambda k, d=None: cfg.get(k, d))
        p.start()
        self.addCleanup(p.stop)

    def test_the_default_bundled_model_loads(self):
        # hey_jarvis is one of the models openwakeword bundles, so this is the
        # real load path the app takes -- it must return a usable model.
        self._with_config("hey_jarvis")

        model = wakeword._load_wakeword_model()

        self.assertIsNotNone(model)
        self.assertIn("hey_jarvis", model.models)

    def test_the_old_hey_vave_name_disables_quietly_instead_of_crashing(self):
        # This is the exact name that crashed every startup. openwakeword raises
        # ValueError for it; we must swallow that and simply return None.
        self._with_config("hey_vave")

        self.assertIsNone(wakeword._load_wakeword_model())

    def test_a_custom_path_is_never_treated_as_a_bundled_name(self):
        # A .onnx path must be handed straight to the loader, not looked up in
        # the bundled catalogue. A missing file still fails closed (returns
        # None), which is what proves we took the path branch, not a download.
        self._with_config("C:/models/my_custom_wakeword.onnx")

        with mock.patch("openwakeword.model.Model") as Model:
            wakeword._load_wakeword_model()

        Model.assert_called_once()
        self.assertEqual(["C:/models/my_custom_wakeword.onnx"],
                         Model.call_args.kwargs["wakeword_models"])


if __name__ == "__main__":
    unittest.main()

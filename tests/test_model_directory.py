"""Machine-local Ollama model-directory configuration regressions."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant import config
from assistant.reliability.diagnostics import check_model_storage
from assistant.reliability.models import CheckStatus


class ModelDirectoryConfigTests(unittest.TestCase):
    def test_desktop_default_points_to_f_drive(self):
        self.assertEqual(
            os.path.normcase(r"F:\ollama\models"),
            os.path.normcase(config.DEFAULT_CONFIG["ollama_models_dir"]),
        )

    def test_setting_is_machine_local_not_shared(self):
        with tempfile.TemporaryDirectory(prefix="vave-model-dir-") as folder:
            local = Path(folder) / "config.local.json"
            with mock.patch.object(config, "LOCAL_CONFIG_FILE", local), \
                 mock.patch.object(config, "save_config") as save:
                config.update_setting("ollama_models_dir", folder)
            written = json.loads(local.read_text(encoding="utf-8"))
        self.assertEqual(folder, written["ollama_models_dir"])
        save.assert_not_called()

    def test_apply_updates_current_process_without_persisting(self):
        with tempfile.TemporaryDirectory(prefix="vave-model-dir-") as folder, \
             mock.patch.dict(os.environ, {}, clear=False):
            old = os.environ.pop("OLLAMA_MODELS", None)
            try:
                result = config.apply_ollama_models_dir(folder)
                self.assertEqual(str(Path(folder).resolve()), os.environ["OLLAMA_MODELS"])
                self.assertTrue(result["exists"])
                self.assertFalse(result["persisted"])
                self.assertTrue(result["restart_required"])
            finally:
                if old is None:
                    os.environ.pop("OLLAMA_MODELS", None)
                else:
                    os.environ["OLLAMA_MODELS"] = old


class ModelDirectoryDoctorTests(unittest.TestCase):
    def test_configured_directory_with_manifests_is_healthy(self):
        with tempfile.TemporaryDirectory(prefix="vave-model-dir-") as folder:
            root = Path(folder)
            manifest = root / "manifests" / "registry" / "library" / "model" / "tag"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}", encoding="utf-8")
            with mock.patch.object(
                config,
                "normalize_ollama_models_dir",
                side_effect=lambda value=None: str(root.resolve()),
            ), mock.patch.dict(os.environ, {"OLLAMA_MODELS": str(root)}):
                result = check_model_storage()
        self.assertEqual(CheckStatus.OK, result.status)
        self.assertEqual(1, result.details["manifest_count"])

    def test_missing_configured_directory_fails_clearly(self):
        missing = Path(tempfile.gettempdir()) / "vave-model-dir-does-not-exist"
        with mock.patch.object(
            config,
            "normalize_ollama_models_dir",
            return_value=str(missing),
        ):
            result = check_model_storage()
        self.assertEqual(CheckStatus.FAILED, result.status)
        self.assertIn("does not exist", result.summary)


class ModelDirectoryGuiTests(unittest.TestCase):
    def test_settings_save_validates_updates_and_applies(self):
        from gui.pages.settings_page import SettingsPage

        with tempfile.TemporaryDirectory(prefix="vave-model-dir-") as folder:
            page = object.__new__(SettingsPage)
            page.ollama_models_entry = mock.Mock()
            page.ollama_models_entry.get.return_value = folder
            page._render_content = mock.Mock()
            with mock.patch(
                "assistant.config.update_setting"
            ) as update, mock.patch(
                "assistant.config.apply_ollama_models_dir",
                return_value={
                    "path": str(Path(folder).resolve()),
                    "restart_required": True,
                },
            ) as apply, mock.patch(
                "gui.pages.settings_page.store.add_system_log"
            ) as log:
                page._save_ollama_models_dir()

        update.assert_called_once_with(
            "ollama_models_dir",
            str(Path(folder).resolve()),
        )
        apply.assert_called_once_with(str(Path(folder).resolve()), persist_user=True)
        self.assertIn("Restart Ollama", log.call_args.args[0])
        page._render_content.assert_called_once_with()

    def test_settings_refuses_missing_folder(self):
        from gui.pages.settings_page import SettingsPage

        page = object.__new__(SettingsPage)
        page.ollama_models_entry = mock.Mock()
        page.ollama_models_entry.get.return_value = r"Z:\missing\ollama\models"
        with mock.patch("assistant.config.update_setting") as update, \
             mock.patch("gui.pages.settings_page.store.add_system_log") as log:
            page._save_ollama_models_dir()
        update.assert_not_called()
        self.assertEqual("waiting", log.call_args.args[1])


if __name__ == "__main__":
    unittest.main()

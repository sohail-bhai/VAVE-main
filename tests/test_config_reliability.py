"""Config validation, backup, and recovery regressions."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant import config


class ConfigReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vave-config-reliability-")
        root = Path(self.temp.name)
        self.config_file = root / "config.json"
        self.local_file = root / "config.local.json"
        self.patches = [
            mock.patch.object(config, "CONFIG_FILE", self.config_file),
            mock.patch.object(config, "LOCAL_CONFIG_FILE", self.local_file),
        ]
        for patcher in self.patches:
            patcher.start()
        config.reset_config_cache()

    def tearDown(self):
        config.reset_config_cache()
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def _write(self, value):
        self.config_file.write_text(json.dumps(value), encoding="utf-8")
        config.reset_config_cache()

    def test_oversized_identity_uses_safe_default_in_memory(self):
        self._write({"user_name": "X" * 5000})
        loaded = config.load_config()
        self.assertEqual("Sohail", loaded["user_name"])
        self.assertTrue(any(i["path"] == "user_name" for i in config.get_config_issues()))

    def test_invalid_number_and_type_restore_defaults(self):
        sanitized, issues = config.validate_config({
            "voice_volume": 5,
            "agent_max_steps": "many",
        })
        self.assertEqual(config.DEFAULT_CONFIG["voice_volume"], sanitized["voice_volume"])
        self.assertEqual(config.DEFAULT_CONFIG["agent_max_steps"], sanitized["agent_max_steps"])
        self.assertEqual(2, len(issues))

    def test_nested_object_lists_remain_structured(self):
        rules = [{
            "target_text": "confirm",
            "auto_click": False,
            "require_focus": True,
            "pattern": "exact",
        }]
        sanitized, issues = config.validate_config({
            "overwatch": {"rules": rules}
        })
        self.assertEqual(rules, sanitized["overwatch"]["rules"])
        self.assertEqual([], issues)

    def test_load_recovers_from_valid_backup(self):
        self.config_file.write_text("{broken", encoding="utf-8")
        config._config_backup_path().write_text(
            json.dumps({"user_name": "Backup User"}),
            encoding="utf-8",
        )
        loaded = config.load_config()
        self.assertEqual("Backup User", loaded["user_name"])
        self.assertEqual("backup", config.config_source())

    def test_save_keeps_previous_complete_backup(self):
        self._write({"user_name": "Before"})
        config.save_config({"user_name": "After"})
        self.assertEqual(
            {"user_name": "Before"},
            json.loads(config._config_backup_path().read_text(encoding="utf-8")),
        )
        self.assertEqual(
            {"user_name": "After"},
            json.loads(self.config_file.read_text(encoding="utf-8")),
        )

    def test_safe_repair_rewrites_invalid_values_and_keeps_backup(self):
        self._write({"user_name": "X" * 5000, "voice_volume": 0.5})
        result = config.repair_config()
        repaired = json.loads(self.config_file.read_text(encoding="utf-8"))
        backup = json.loads(config._config_backup_path().read_text(encoding="utf-8"))
        self.assertTrue(result["ok"])
        self.assertEqual("Sohail", repaired["user_name"])
        self.assertEqual("X" * 5000, backup["user_name"])
        self.assertEqual(0.5, repaired["voice_volume"])

    def test_update_rejects_invalid_value(self):
        self._write({})
        with self.assertRaises(ValueError):
            config.update_setting("voice_volume", 7)


if __name__ == "__main__":
    unittest.main()

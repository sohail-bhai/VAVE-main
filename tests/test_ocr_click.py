"""Tests for the screenshot-OCR click route (option A).

find_text_on_screen: Tesseract tier -> RapidOCR tier -> UIA tier, with an
optional window-bounds filter. find_and_click_text: UIA fast path, then the
OCR route, optionally scoped to one window. Everything here is mocked: no
screenshots, no model downloads, no mouse movement.
"""

import sys
import unittest
from unittest import mock

# Eager import, same reason as tests/test_chromium_click_path.py: anything
# first imported inside a mock.patch.dict(sys.modules, ...) region is deleted
# from sys.modules when the region exits, and numpy's C extension cannot
# initialize twice. Pin the chain up front.
import assistant.vision  # noqa: F401


def _lines(*rows):
    # (text, 4-point box, score)
    return list(rows)


def _uia_empty():
    auto = mock.MagicMock()
    auto.WalkControl.return_value = []
    auto.GetForegroundControl.return_value = None
    auto.GetRootControl.return_value = mock.MagicMock()
    return auto


class FindTextOnScreenTests(unittest.TestCase):
    def setUp(self):
        from assistant import vision
        self.vision = vision

    @mock.patch("assistant.vision._find_tesseract", return_value=None)
    @mock.patch("assistant.vision.ocr_screen", return_value=_lines(
        ("Profile 1", [[100, 100], [200, 100], [200, 140], [100, 140]], 0.9),
        ("Sohail", [[300, 200], [420, 200], [420, 240], [300, 240]], 0.95),
    ))
    def test_exact_match_case_insensitive(self, _ocr, _tess):
        self.assertEqual(self.vision.find_text_on_screen("sohail"), (360, 220))

    @mock.patch("assistant.vision._find_tesseract", return_value=None)
    @mock.patch("assistant.vision.ocr_screen", return_value=_lines(
        ("Profiles and settings", [[0, 0], [200, 0], [200, 20], [0, 20]], 0.9),
        ("Profile", [[0, 40], [100, 40], [100, 60], [0, 60]], 0.9),
    ))
    def test_prefers_exact_over_substring(self, _ocr, _tess):
        self.assertEqual(self.vision.find_text_on_screen("profile"), (50, 50))

    @mock.patch("assistant.vision._find_tesseract", return_value=None)
    @mock.patch("assistant.vision.ocr_screen", return_value=_lines(
        ("Sohail", [[300, 200], [420, 200], [420, 240], [300, 240]], 0.95),
    ))
    def test_within_filter_excludes_outside_match(self, _ocr, _tess):
        with mock.patch.dict(sys.modules, {"uiautomation": _uia_empty()}):
            self.assertIsNone(self.vision.find_text_on_screen("sohail", within=(0, 0, 100, 100)))

    @mock.patch("assistant.vision._find_tesseract", return_value=None)
    @mock.patch("assistant.vision.ocr_screen", return_value=_lines(
        ("Sohail", [[300, 200], [420, 200], [420, 240], [300, 240]], 0.95),
    ))
    def test_within_filter_keeps_inside_match(self, _ocr, _tess):
        self.assertEqual(
            self.vision.find_text_on_screen("sohail", within=(0, 0, 1920, 1080)),
            (360, 220))

    @mock.patch("assistant.vision._find_tesseract", return_value=None)
    @mock.patch("assistant.vision.ocr_screen", return_value=[])
    def test_no_match_anywhere_returns_none(self, _ocr, _tess):
        with mock.patch.dict(sys.modules, {"uiautomation": _uia_empty()}):
            self.assertIsNone(self.vision.find_text_on_screen("sohail"))

    @mock.patch("assistant.vision.ImageGrab.grab")
    @mock.patch("assistant.vision.pytesseract.image_to_data")
    @mock.patch("assistant.vision._find_tesseract", return_value="C:\\tess.exe")
    def test_tesseract_tier_still_works(self, _tess, mock_data, _grab):
        mock_data.return_value = {
            "text": ["", "Sohail"], "left": [0, 300], "top": [0, 200],
            "width": [0, 120], "height": [0, 40], "conf": ["-1", "96"],
        }
        self.assertEqual(self.vision.find_text_on_screen("sohail"), (360, 220))

    def test_box_center_forms(self):
        self.assertEqual(self.vision._box_center([[0, 0], [10, 0], [10, 20], [0, 20]]), (5.0, 10.0))
        self.assertEqual(self.vision._box_center([0, 0, 10, 20]), (5.0, 10.0))
        self.assertIsNone(self.vision._box_center("nope"))

    @mock.patch("assistant.vision._find_tesseract", return_value=None)
    @mock.patch("assistant.vision.ocr_screen", return_value=_lines(
        ("Sohail", [[300, 200], [420, 200], [420, 240], [300, 240]], 0.85),
        ("Manage Profiles", [[300, 300], [520, 300], [520, 330], [300, 330]], 0.87),
    ))
    def test_multiword_target_matches_significant_word(self, _ocr, _tess):
        # "sohail profile" must find the "Sohail" tile, not miss entirely.
        self.assertEqual(self.vision.find_text_on_screen("sohail profile"), (360, 220))

    @mock.patch("assistant.vision._find_tesseract", return_value=None)
    @mock.patch("assistant.vision.ocr_screen", return_value=_lines(
        ("Manage Profiles", [[300, 300], [520, 300], [520, 330], [300, 330]], 0.87),
    ))
    def test_short_words_do_not_match_everything(self, _ocr, _tess):
        with mock.patch.dict(sys.modules, {"uiautomation": _uia_empty()}):
            self.assertIsNone(self.vision.find_text_on_screen("the a"))


class FindAndClickTextTests(unittest.TestCase):
    def setUp(self):
        from assistant import system_tasks
        self.st = system_tasks

    def _auto_hit(self):
        auto = mock.MagicMock()
        btn = mock.MagicMock()
        btn.Exists.return_value = True
        auto.Control.return_value = btn
        return auto, btn

    def _auto_miss(self):
        auto = mock.MagicMock()
        btn = mock.MagicMock()
        btn.Exists.return_value = False
        auto.Control.return_value = btn
        return auto, btn

    @mock.patch("assistant.system_tasks.speak")
    @mock.patch("assistant.system_tasks.click_at")
    @mock.patch("assistant.vision.find_text_on_screen")
    def test_uia_hit_clicks_without_ocr(self, mock_ocr, _click, _speak):
        auto, btn = self._auto_hit()
        with mock.patch.dict(sys.modules, {"uiautomation": auto}):
            with mock.patch("assistant.system_tasks._ensure_com"):
                self.assertTrue(self.st.find_and_click_text("Save"))
        btn.Click.assert_called_once()
        mock_ocr.assert_not_called()

    @mock.patch("assistant.system_tasks.speak")
    @mock.patch("assistant.system_tasks.click_at", return_value="Clicked at (360, 220).")
    @mock.patch("assistant.vision.find_text_on_screen", return_value=(360, 220))
    def test_uia_miss_falls_back_to_ocr(self, mock_ocr, mock_click, _speak):
        auto, _btn = self._auto_miss()
        with mock.patch.dict(sys.modules, {"uiautomation": auto}):
            with mock.patch("assistant.system_tasks._ensure_com"):
                result = self.st.find_and_click_text("Sohail")
        mock_ocr.assert_called_once_with("Sohail", within=None)
        mock_click.assert_called_once_with(360, 220)
        self.assertEqual(result, "Clicked at (360, 220).")

    @mock.patch("assistant.system_tasks.speak")
    @mock.patch("assistant.system_tasks.click_at")
    @mock.patch("assistant.vision.find_text_on_screen", return_value=None)
    def test_nothing_found_speaks_and_returns_false(self, _ocr, mock_click, mock_speak):
        auto, _btn = self._auto_miss()
        with mock.patch.dict(sys.modules, {"uiautomation": auto}):
            with mock.patch("assistant.system_tasks._ensure_com"):
                self.assertFalse(self.st.find_and_click_text("Nope"))
        mock_click.assert_not_called()
        mock_speak.assert_called_once()

    @mock.patch("assistant.system_tasks.speak")
    @mock.patch("assistant.system_tasks.click_at")
    @mock.patch("assistant.system_tasks.activate_window")
    @mock.patch("assistant.system_tasks._find_window", return_value=None)
    def test_unknown_window_returns_guidance(self, _find, _act, _click, _speak):
        auto, _btn = self._auto_miss()
        with mock.patch.dict(sys.modules, {"uiautomation": auto}):
            with mock.patch("assistant.system_tasks._ensure_com"):
                result = self.st.find_and_click_text("Sohail", window_title="Nope")
        self.assertIn("No open window matches", result)
        _act.assert_not_called()
        _click.assert_not_called()

    @mock.patch("assistant.system_tasks.speak")
    @mock.patch("assistant.system_tasks.click_at")
    @mock.patch("assistant.system_tasks.activate_window")
    @mock.patch("assistant.vision.find_text_on_screen", return_value=None)
    def test_window_scopes_ocr_and_focuses(self, mock_ocr, mock_act, _click, _speak):
        auto, btn = self._auto_miss()
        win = mock.MagicMock()
        win.NativeWindowHandle = 1234
        rect = mock.MagicMock(left=0, top=0, right=800, bottom=600)
        win.BoundingRectangle = rect
        with mock.patch.dict(sys.modules, {"uiautomation": auto}):
            with mock.patch("assistant.system_tasks._ensure_com"):
                with mock.patch("assistant.system_tasks._find_window", return_value=win):
                    self.assertFalse(self.st.find_and_click_text("Sohail", window_title="Netflix"))
        mock_act.assert_called_once_with(1234)
        mock_ocr.assert_called_once_with("Sohail", within=(0, 0, 800, 600))


if __name__ == "__main__":
    unittest.main()

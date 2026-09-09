"""Tests for reaching this computer's files from a phone.

The rule under test is the one that matters: a phone reaches exactly the
folders that were shared, and nothing else. Paths are resolved before they are
checked, so `..` and a symlink pointing out of a share fail the same way.
"""

import os
import shutil
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import assistant.files as files
from assistant.files import FileAccessError


class FileTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="vave-files-")).resolve()
        self.shared = self.root / "Documents"
        self.private = self.root / "private"
        (self.shared / "reports").mkdir(parents=True)
        self.private.mkdir()

        (self.shared / "invoice.pdf").write_text("a bill")
        (self.shared / "reports" / "q3.txt").write_text("numbers")
        (self.shared / ".hidden").write_text("not for the phone")
        (self.private / "diary.txt").write_text("private thoughts")

        self._real_get_setting = files.get_setting
        files.get_setting = self._setting

    def tearDown(self):
        files.get_setting = self._real_get_setting
        shutil.rmtree(self.root, ignore_errors=True)

    def _setting(self, key, default=None):
        if key == "file_shares":
            return [str(self.shared)]
        return self._real_get_setting(key, default)


class ShareTests(FileTestCase):
    def test_only_the_shared_folder_is_offered(self):
        listed = files.describe_shares()

        self.assertEqual([str(self.shared)], [item["path"] for item in listed])

    def test_nothing_is_shared_until_a_folder_is_listed(self):
        files.get_setting = lambda key, default=None: ([] if key == "file_shares"
                                                        else default)

        with self.assertRaises(FileAccessError) as caught:
            files.list_dir("")
        self.assertIn("Nothing is shared yet", str(caught.exception))

    def test_a_share_that_does_not_exist_is_ignored(self):
        files.get_setting = lambda key, default=None: (
            [str(self.shared), "/nowhere/at/all"] if key == "file_shares" else default)

        self.assertEqual(1, len(files.shares()))


class BrowsingTests(FileTestCase):
    def test_a_folder_lists_its_contents_with_folders_first(self):
        listing = files.list_dir(str(self.shared))

        self.assertEqual(["reports", "invoice.pdf"],
                         [item["name"] for item in listing["entries"]])

    def test_an_empty_path_lists_the_shares_themselves(self):
        """The top level is the shares, so every one of them is reachable.

        Listing the first share instead would leave the second and third with
        no name a client could ever ask for.
        """
        listing = files.list_dir("")

        self.assertEqual("", listing["path"])
        self.assertEqual(["Documents"], [item["name"] for item in listing["entries"]])
        self.assertTrue(all(item["is_dir"] for item in listing["entries"]))

    def test_every_share_is_reachable_by_name(self):
        second = self.root / "Pictures"
        second.mkdir()
        (second / "beach.jpg").write_text("a photo")
        files.get_setting = lambda key, default=None: (
            [str(self.shared), str(second)] if key == "file_shares"
            else self._real_get_setting(key, default))

        names = [item["name"] for item in files.list_dir("")["entries"]]
        self.assertEqual(["Documents", "Pictures"], names)

        listing = files.list_dir("Pictures")
        self.assertEqual(["beach.jpg"], [item["name"] for item in listing["entries"]])

    def test_a_listing_names_the_folder_above_it(self):
        """A client walks back out using the parent the server gives it."""
        listing = files.list_dir("reports")

        self.assertEqual("Documents/reports", listing["path"])
        self.assertEqual("Documents", listing["parent"])
        self.assertEqual("", files.list_dir("Documents")["parent"])

    def test_a_listed_file_can_be_asked_for_again(self):
        """What a listing calls a file is what a client may send back."""
        entry = files.list_dir("Documents")["entries"][0]

        self.assertEqual("Documents/reports", entry["relative"])
        self.assertEqual(files.resolve(entry["relative"]), self.shared / "reports")

    def test_a_relative_path_is_taken_from_the_share(self):
        listing = files.list_dir("reports")

        self.assertEqual(["q3.txt"], [item["name"] for item in listing["entries"]])

    def test_hidden_files_are_not_listed(self):
        names = [item["name"] for item in files.list_dir("Documents")["entries"]]

        self.assertNotIn(".hidden", names)

    def test_a_file_reports_its_size_and_kind(self):
        described = files.stat("invoice.pdf")

        self.assertEqual(6, described["size"])
        self.assertFalse(described["is_dir"])

    def test_asking_a_file_for_its_contents_as_a_folder_is_refused(self):
        with self.assertRaises(FileAccessError):
            files.list_dir("invoice.pdf")


class BoundaryTests(FileTestCase):
    def test_climbing_out_with_dot_dot_is_refused(self):
        with self.assertRaises(FileAccessError) as caught:
            files.list_dir(str(self.shared / ".." / "private"))

        self.assertIn("outside the folders you shared", str(caught.exception))

    def test_an_absolute_path_elsewhere_is_refused(self):
        with self.assertRaises(FileAccessError):
            files.stat(str(self.private / "diary.txt"))

    def test_a_symlink_pointing_out_of_the_share_is_refused(self):
        link = self.shared / "escape"
        try:
            os.symlink(self.private, link)
        except OSError:
            self.skipTest("symlinks are not available here")

        with self.assertRaises(FileAccessError):
            files.list_dir(str(link))

    def test_the_usual_secret_folders_are_never_reachable(self):
        (self.shared / ".ssh").mkdir()

        with self.assertRaises(FileAccessError):
            files.list_dir(str(self.shared / ".ssh"))

    def test_a_missing_file_is_reported_not_invented(self):
        with self.assertRaises(FileAccessError):
            files.open_for_download("nothing-here.txt")


class SearchTests(FileTestCase):
    def test_a_file_is_found_by_part_of_its_name(self):
        found = files.search("invo")

        self.assertEqual(["invoice.pdf"], [item["name"] for item in found])

    def test_search_looks_inside_subfolders(self):
        self.assertEqual(["q3.txt"], [item["name"] for item in files.search("q3")])

    def test_search_never_leaves_the_shares(self):
        self.assertEqual([], files.search("diary"))

    def test_an_empty_query_finds_nothing_rather_than_everything(self):
        self.assertEqual([], files.search("  "))

    def test_results_are_capped(self):
        for index in range(20):
            (self.shared / f"note-{index}.txt").write_text("x")

        self.assertEqual(5, len(files.search("note", limit=5)))


class TransferTests(FileTestCase):
    def test_a_file_from_the_phone_lands_in_the_shared_folder(self):
        saved = files.save_upload("", "photo.jpg", BytesIO(b"image bytes"))

        self.assertEqual("photo.jpg", saved["name"])
        self.assertEqual(b"image bytes", (self.shared / "photo.jpg").read_bytes())

    def test_an_upload_never_overwrites_by_accident(self):
        saved = files.save_upload("", "invoice.pdf", BytesIO(b"new bill"))

        self.assertNotEqual("invoice.pdf", saved["name"])
        self.assertEqual("a bill", (self.shared / "invoice.pdf").read_text())

    def test_overwriting_is_possible_when_asked_for(self):
        files.save_upload("", "invoice.pdf", BytesIO(b"new bill"), overwrite=True)

        self.assertEqual(b"new bill", (self.shared / "invoice.pdf").read_bytes())

    def test_a_sneaky_filename_cannot_escape_the_folder(self):
        saved = files.save_upload("", "../../escaped.txt", BytesIO(b"nope"))

        self.assertEqual("escaped.txt", saved["name"])
        self.assertTrue((self.shared / "escaped.txt").exists())

    def test_a_download_names_the_file_and_its_type(self):
        target, media_type = files.open_for_download("invoice.pdf")

        self.assertEqual("invoice.pdf", target.name)
        self.assertEqual("application/pdf", media_type)

    def test_a_folder_cannot_be_downloaded_as_a_file(self):
        with self.assertRaises(FileAccessError):
            files.open_for_download("reports")


class ChangeTests(FileTestCase):
    def test_a_folder_can_be_made(self):
        created = files.make_folder("holiday")

        self.assertTrue((self.shared / "holiday").is_dir())
        self.assertTrue(created["is_dir"])

    def test_a_file_can_be_renamed(self):
        files.move("invoice.pdf", str(self.shared / "bill.pdf"))

        self.assertTrue((self.shared / "bill.pdf").exists())
        self.assertFalse((self.shared / "invoice.pdf").exists())

    def test_a_move_cannot_land_outside_the_shares(self):
        with self.assertRaises(FileAccessError):
            files.move("invoice.pdf", str(self.private / "stolen.pdf"))

    def test_a_move_will_not_quietly_replace_something(self):
        with self.assertRaises(FileAccessError):
            files.move("invoice.pdf", str(self.shared / "reports"))

    def test_a_file_can_be_deleted(self):
        files.delete("invoice.pdf")

        self.assertFalse((self.shared / "invoice.pdf").exists())

    def test_a_folder_with_things_in_it_is_left_alone(self):
        with self.assertRaises(FileAccessError) as caught:
            files.delete("reports")

        self.assertIn("not empty", str(caught.exception))
        self.assertTrue((self.shared / "reports").exists())

    def test_a_share_itself_cannot_be_deleted(self):
        with self.assertRaises(FileAccessError):
            files.delete(str(self.shared))


class ToolTests(FileTestCase):
    def test_the_agent_sees_the_same_folders_as_the_phone(self):
        self.assertIn(str(self.shared), files.shared_folders())

    def test_listing_reads_as_a_person_would_write_it(self):
        result = files.list_shared_files("Documents")

        self.assertIn("[dir] reports", result)
        self.assertIn("invoice.pdf", result)

    def test_finding_says_where_the_file_is(self):
        self.assertIn("q3.txt", files.find_shared_file("q3"))

    def test_finding_nothing_says_so_plainly(self):
        self.assertIn("Nothing shared is called", files.find_shared_file("zzz"))

    def test_a_refused_path_comes_back_as_words_not_an_exception(self):
        self.assertIn("outside the folders",
                      files.list_shared_files(str(self.private)))


if __name__ == "__main__":
    unittest.main()

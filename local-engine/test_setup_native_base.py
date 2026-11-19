"""The optional Base model shares a verified local inode or publishes a complete, pinned download."""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import setup_native_base as setup


class NativeBaseSetupTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "local-model"
        self.source.write_bytes(b"verified fixture")
        self.models = self.root / "models"
        self.enterContext(patch.object(setup, "SIZE", self.source.stat().st_size))
        self.enterContext(patch.object(setup, "SHA256", hashlib.sha256(self.source.read_bytes()).hexdigest()))

    def test_verified_local_file_is_hard_linked_and_setup_is_repeatable(self):
        with patch.object(setup.subprocess, "run", side_effect=AssertionError("unexpected download")):
            target = setup.install(self.models, "unused", self.source)
            self.assertTrue(target.samefile(self.source))
            self.assertEqual(target, setup.install(self.models, "unused"))
            self.assertEqual(2, target.stat().st_nlink)

    def test_existing_or_corrupt_files_are_preserved_and_never_published(self):
        self.source.write_bytes(b"incorrect bytes!")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            setup.install(self.models, "unused", self.source)
        target = self.models / setup.NAME
        self.assertFalse(target.exists())
        target.write_bytes(b"existing data")
        with self.assertRaises(ValueError):
            setup.install(self.models, "unused", self.source)
        self.assertEqual(b"existing data", target.read_bytes())

    def test_new_download_is_pinned_and_staged_on_install_volume(self):
        def download(args, **kwargs):
            self.assertEqual(
                args[1:6], ["download", setup.REPOSITORY, setup.NAME, "--revision", setup.REVISION]
            )
            stage = Path(args[-1])
            self.assertEqual(self.models, stage.parent)
            self.assertFalse((self.models / setup.NAME).exists())
            (stage / setup.NAME).write_bytes(self.source.read_bytes())

        with patch.object(setup.subprocess, "run", side_effect=download):
            target = setup.install(self.models, "hf")
        self.assertEqual([target], list(self.models.iterdir()))

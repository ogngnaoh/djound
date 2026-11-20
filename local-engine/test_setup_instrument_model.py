import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import rendition_instrument_runtime as runtime
import setup_assets as setup


class InstrumentInstallationTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.cache = root / 'cache'
        self.cache.mkdir()
        home = root / 'models'
        self.target = home / 'instrument-transcriber'
        self.receipt = self.target / '.djound-verified.json'
        self.contents = {'encoder.onnx': b'one', 'decoder.onnx': b'two', 'vocab.json': b'abc'}
        for filename, data in self.contents.items():
            (self.cache / filename).write_bytes(data)
        manifest = {'model': 'example/model', 'revision': 'pinned', 'assets': [
            {'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
            for name, data in self.contents.items()]}
        (root / 'instrument-transcriber-assets.json').write_text(json.dumps(manifest))
        for module in (runtime, setup):
            self.enterContext(patch.object(module, 'HERE', root))
            self.enterContext(patch.object(module, 'HOME', home))
        setup.install_recording_model('instrument-transcriber', self.cache)

    def damage(self):
        # Replacing the installed inode leaves the independent test cache valid.
        file = self.target / 'encoder.onnx'
        file.unlink()
        file.write_bytes(b'bad')
        (self.target / 'decoder.onnx').unlink()
        self.receipt.write_text('not json')
        with self.assertRaisesRegex(ValueError, 'Restore'):
            runtime.installed_model()

    def test_rerun_repairs_corrupt_and_missing_files_and_receipt(self):
        self.damage()
        healthy = self.target / 'vocab.json'
        inode = healthy.stat().st_ino
        (self.target / 'other.txt').write_bytes(b'unrelated')
        setup.install_recording_model('instrument-transcriber', self.cache)
        self.assertEqual(self.target, runtime.installed_model())
        self.assertEqual(inode, healthy.stat().st_ino)
        self.assertEqual(b'unrelated', (self.target / 'other.txt').read_bytes())
        with patch.object(setup.subprocess, 'run', side_effect=AssertionError('unnecessary download')):
            setup.install_recording_model('instrument-transcriber', None)

    def test_failed_hash_or_download_preserves_the_previous_installation(self):
        self.damage()
        prior = {f.name: f.read_bytes() for f in self.target.iterdir()}
        def fetch(args, check):
            output = Path(args[-1])
            if output.name == 'decoder.onnx' and broken_network:
                raise subprocess.CalledProcessError(22, args)
            output.write_bytes(b'bad' if output.name == 'decoder.onnx' else self.contents[output.name])
        for broken_network, error in ((True, subprocess.CalledProcessError), (False, ValueError)):
            with self.subTest(broken_network=broken_network), patch.object(setup.subprocess, 'run', side_effect=fetch):
                with self.assertRaises(error):
                    setup.install_recording_model('instrument-transcriber', None)
            self.assertEqual(prior, {f.name: f.read_bytes() for f in self.target.iterdir()})
            self.assertEqual([self.target], list(self.target.parent.iterdir()))
        setup.install_recording_model('instrument-transcriber', self.cache)
        self.assertEqual(self.target, runtime.installed_model())

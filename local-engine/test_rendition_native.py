"""Check the pinned CLI boundary without loading any model."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

import rendition_native as native
import rendition_audio as adapter


class NativeRenditionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.enterContext(patch.dict(os.environ, {"DJOUND_ACE_NATIVE_HOME": str(self.home)}))
        self.binary = self.home / "build" / "ace-synth"
        self.binary.parent.mkdir()
        self.binary.touch(mode=0o700)
        models = self.home / "models"
        models.mkdir()
        for filename in (*native.MODELS, "unrelated-encoder.gguf"):
            (models / filename).touch()
        self.basis = self.home / "basis.wav"
        self.basis.touch()
        self.target = self.home / "raw.wav"

    def render(self, **scope):
        native.generate_native(self.basis, self.target, "Instrumental trio", duration=1.,
                               deadline=time.monotonic() + 3, **scope)

    def test_reads_source_conditions_cover_or_repaint_and_uses_only_pinned_models(self):
        timbre = self.home / "folk-parent-clip.wav"
        timbre.touch()
        for scope in ({}, {"cover_strength": .8, "cover_noise_strength": .625, "timbre_reference": timbre},
                      {"start": .25, "end": .5}):
            with self.subTest(scope=scope):
                def fake_cli(args, **kwargs):
                    self.assertEqual(str(self.binary), args[0])
                    self.assertEqual(str(self.basis), args[args.index("--src-audio") + 1])
                    if "timbre_reference" in scope:
                        self.assertEqual(str(timbre), args[args.index("--ref-audio") + 1])
                    else:
                        self.assertNotIn("--ref-audio", args)
                    registry = Path(args[args.index("--models") + 1])
                    self.assertEqual(set(native.MODELS), {f.name for f in registry.iterdir()})
                    request = Path(args[args.index("--request") + 1])
                    settings = json.loads(request.read_text())
                    if "cover_noise_strength" in scope:
                        self.assertEqual(.625, settings["cover_noise_strength"])
                    else:
                        self.assertNotIn("cover_noise_strength", settings)
                    self.assertEqual("[Instrumental]", settings["lyrics"])
                    self.assertEqual("wav16", settings["output_format"])
                    self.assertEqual("Instrumental trio", settings["caption"])
                    if "start" in scope:
                        self.assertNotIn("audio_cover_strength", settings)
                        self.assertEqual("repaint", settings["task_type"])
                        self.assertEqual((.25, .5), (settings["repainting_start"], settings["repainting_end"]))
                    else:
                        self.assertEqual("cover", settings["task_type"])
                        self.assertEqual(scope.get("cover_strength", .55), settings["audio_cover_strength"])
                    with wave.open(str(request.with_name(request.stem+"0.wav")), "wb") as wav:
                        wav.setparams((2, 2, 48000, 0, "NONE", ""))
                        wav.writeframes(b"\0" * 4)
                    return subprocess.CompletedProcess(args, 0)

                with patch.object(native.subprocess, "run", side_effect=fake_cli):
                    self.render(**scope)
                self.assertTrue(self.target.is_file())
                self.target.unlink()

    def test_invalid_cover_strength_does_not_start_synthesis(self):
        with patch.object(native.subprocess, "run") as execute:
            for strength in (True, "0.8", -0.1, 1.1, float("inf"), float("nan")):
                with self.subTest(strength=strength), self.assertRaisesRegex(ValueError, "cover strength"):
                    self.render(cover_strength=strength)
                with self.subTest(strength=strength), self.assertRaisesRegex(ValueError, "cover noise strength"):
                    self.render(cover_noise_strength=strength)
            with self.assertRaisesRegex(ValueError, "requires a cover"):
                self.render(start=.1, end=.5, cover_noise_strength=.4)
            execute.assert_not_called()

    def test_part_uses_only_base_encoder_and_vae_with_its_own_track_instructions(self):
        base = self.home / "models" / native.BASE_MODEL
        checkpoint = b"pinned Base checkpoint fixture"
        self.enterContext(patch.object(native, "BASE_BYTES", len(checkpoint)))
        self.enterContext(patch.object(native, "BASE_SHA256", hashlib.sha256(checkpoint).hexdigest()))
        self.enterContext(patch.dict(os.environ, {"DJOUND_RENDITION_BACKEND": "native", "DJOUND_ACE_BASE_MODEL": ""}))
        self.assertFalse(adapter.part_available())
        base.write_bytes(b"!" * len(checkpoint))
        self.assertFalse(adapter.part_available())
        base.write_bytes(checkpoint)
        self.assertTrue(adapter.part_available())
        with patch.dict(os.environ, {"DJOUND_RENDITION_BACKEND": "http"}):
            self.assertFalse(adapter.part_available())
            with patch.dict(os.environ, {"DJOUND_ACE_BASE_MODEL": str(base)}):
                self.assertTrue(adapter.part_available())

        def fake_cli(args, **kwargs):
            self.assertEqual(str(self.basis), args[args.index("--src-audio") + 1])
            self.assertNotIn("--ref-audio", args)
            registry = Path(args[args.index("--models") + 1])
            self.assertEqual({native.BASE_MODEL, *native.MODELS[1:]}, {f.name for f in registry.iterdir()})
            self.assertTrue((registry / native.BASE_MODEL).samefile(base))
            request = Path(args[args.index("--request") + 1])
            settings = json.loads(request.read_text())
            self.assertEqual(
                ("lego", "guitar", native.BASE_MODEL, 50, 1., 1., "Fingerpicked steel strings"),
                tuple(settings[k] for k in ("task_type", "track", "synth_model", "inference_steps", "guidance_scale",
                                            "shift", "caption")),
            )
            self.assertEqual("[Instrumental]", settings["lyrics"])
            self.assertNotIn("audio_cover_strength", settings)
            with wave.open(str(request.with_name("take0.wav")), "wb") as wav:
                wav.setparams((2, 2, 48000, 0, "NONE", ""))
                wav.writeframes(b"\x01\x00" * 2 * 3840)
            return subprocess.CompletedProcess(args, 0)

        with patch.object(native.subprocess, "run", side_effect=fake_cli):
            native.generate_native_part(self.basis, self.target, lane="guitar", caption="Fingerpicked steel strings",
                                        duration=.08, deadline=time.monotonic() + 4)
        with wave.open(str(self.target), "rb") as wav:
            self.assertEqual(3840, wav.getnframes())
        base.write_bytes(b"?" * len(checkpoint))
        self.assertFalse(adapter.part_available())

    def test_unsupported_part_lanes_and_wrong_raw_grid_never_publish(self):
        with patch.object(native.subprocess, "run") as call:
            for name in ("vocals", "backing_vocals", "trombone", "guitar | bass", ""):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "instrumental accompaniment lane"):
                    native.generate_native_part(self.basis, self.target, lane=name, caption="Instrumental",
                                                deadline=time.monotonic()+5, duration=.08)
            call.assert_not_called()
        def short(args, **kwargs):
            request = Path(args[args.index("--request") + 1])
            with wave.open(str(request.with_name("take0.wav")), "wb") as wav:
                wav.setparams((2, 2, 48000, 0, "NONE", ""))
                wav.writeframes(b"\0" * 4 * 3839)
            return subprocess.CompletedProcess(args, 0)
        with patch.object(native, "_base_checkpoint", return_value=self.basis), patch.object(native.subprocess, "run", side_effect=short):
            with self.assertRaisesRegex(RuntimeError, "usable rendered WAV"):
                native.generate_native_part(self.basis, self.target, lane="woodwinds", caption="Reed part",
                                            duration=.08, deadline=time.monotonic()+5)
        self.assertFalse(self.target.exists())

    def test_an_orphaned_child_holds_the_engine_slot_until_it_finishes(self):
        # A harmless child stands in for ace-synth. It keeps the inherited open file description
        # even after the worker closes its copy; a second process must not start another model.
        with native._synth_lease(self.binary, time.monotonic()+2) as lease:
            child=subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                                   stdin=subprocess.PIPE,pass_fds=(lease,))
        try:
            with self.assertRaisesRegex(RuntimeError, "waiting for the rendition engine"):
                with native._synth_lease(self.binary,time.monotonic()+.08):
                    self.fail("A second renderer acquired the occupied slot")
        finally:
            child.communicate(input=b"",timeout=5)
        with native._synth_lease(self.binary,time.monotonic()+2):
            pass

    def test_zero_exit_without_render_and_timeout_never_publish(self):
        with patch.object(native.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            with self.assertRaisesRegex(RuntimeError, "usable rendered WAV"):
                self.render()
        with patch.object(native.subprocess, "run", side_effect=subprocess.TimeoutExpired("synth", 1)):
            with self.assertRaisesRegex(RuntimeError, "time limit"):
                self.render()
        self.assertFalse(self.target.exists())

    def test_missing_assets_and_existing_outputs_are_rejected_before_execution(self):
        self.target.write_bytes(b"keep")
        with patch.object(native.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "fresh output"):
                self.render()
            self.assertEqual(b"keep", self.target.read_bytes())
            (self.home / "models" / native.MODELS[0]).unlink()
            self.assertFalse(native.native_available())
            with self.assertRaisesRegex(RuntimeError, "Set up"):
                self.render()
            execute.assert_not_called()

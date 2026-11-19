"""Offline ACE HTTP contract and lossless selection-boundary regressions."""
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import wave

import rendition_audio as render


def wav_bytes(rate, channels, samples):
    data = io.BytesIO()
    with wave.open(data, "wb") as writer:
        writer.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        writer.writeframes(samples)
    return data.getvalue()


def timed_samples(frames, base):
    """Give each second its own stereo values to expose timeline mistakes."""
    rate = render.RATE
    return b"".join(struct.pack("<hh", base + sec, -base - sec) * min(rate, frames - sec * rate)
                    for sec in range((frames + rate - 1) // rate))


class TestService(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, value, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(value)))
        self.end_headers()
        self.wfile.write(value)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/release_task":
            headers = f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode()
            form = BytesParser(policy=default).parsebytes(headers + body)
            self.server.fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                                  for part in form.iter_parts()}
            data = {"task_id": "job-1", "status": "queued"}
        elif self.path == "/query_result":
            self.server.query = json.loads(body)
            self.server.polls += 1
            status = 0 if self.server.polls == 1 else self.server.status
            item = {"file": self.server.file_url, "status": status, "error": "generation rejected"}
            data = [{"task_id": "job-1", "status": status, "result": json.dumps([item])}]
        else:
            self.send_error(404)
            return
        self.send(json.dumps({"data": data, "code": 200, "error": None}).encode(), "application/json")

    def do_GET(self):
        if self.path == "/health":
            self.send(json.dumps({"code": 200, "data": self.server.health}).encode(), "application/json")
        elif self.path == "/v1/audio?path=take.wav":
            self.send(self.server.audio, "audio/wav")
        else:
            self.send_error(404)


class RenditionAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = HTTPServer(("127.0.0.1", 0), TestService)
        thread = threading.Thread(target=cls.service.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join)
        cls.addClassCleanup(cls.service.server_close)
        cls.addClassCleanup(cls.service.shutdown)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "basis.wav"
        self.target = self.source.with_name("take.wav")
        self.pcm = struct.pack("<hh", 7000, -4000) * render.RATE
        self.source.write_bytes(wav_bytes(render.RATE, 2, self.pcm))
        self.service.polls = 0
        self.service.status = 1
        self.service.file_url = "/v1/audio?path=take.wav"
        self.service.audio = wav_bytes(48000, 2, struct.pack("<hh", -1000, 1800) * 48000)
        self.enterContext(patch.dict(os.environ, {"DJOUND_ACE_URL": f"http://127.0.0.1:{self.service.server_port}"}))
        self.enterContext(patch.object(render, "POLL_SECONDS", 0))

    def run_take(self, **selection):
        render.render_take(self.source, self.target, "Instrumental jazz trio", duration=1, **selection)

    def samples(self):
        with wave.open(str(self.target), "rb") as wav:
            self.assertEqual((2, 2, 44100, 44100), wav.getparams()[:4])
            return wav.readframes(wav.getnframes())

    def assert_generated(self, sample):
        left, right = struct.unpack("<hh", sample)
        self.assertAlmostEqual(left, -1000, delta=1)
        self.assertAlmostEqual(right, 1800, delta=1)

    def test_full_cover_uploads_original_and_receives_new_pcm(self):
        self.run_take(cover_strength=0.8)
        self.assertEqual(self.source.read_bytes(), self.service.fields["src_audio"])
        self.assertNotIn("ref_audio", self.service.fields)
        self.assertEqual(b"cover", self.service.fields["task_type"])
        self.assertEqual(b"[Instrumental]", self.service.fields["lyrics"])
        self.assertEqual(b"1", self.service.fields["batch_size"])
        self.assertEqual(b"0.8", self.service.fields["audio_cover_strength"])
        self.assertEqual({"task_id_list": ["job-1"]}, self.service.query)
        self.assertEqual(2, self.service.polls)
        self.assert_generated(self.samples()[40000:40004])

    def test_readiness_requires_an_initialized_ace_audio_model(self):
        self.service.health = {"status": "ok", "service": "ACE-Step API", "models_initialized": True,
                               "loaded_model": "acestep-v15-turbo"}
        self.assertEqual(render.check_service(), "acestep-v15-turbo")
        self.service.health["models_initialized"] = False
        with self.assertRaisesRegex(RuntimeError, "has not loaded"):
            render.check_service()
        self.service.health = {"status": "ok", "service": "unrelated"}
        with self.assertRaisesRegex(RuntimeError, "not a ready"):
            render.check_service()
        self.assertEqual(self.service.polls, 0)

    def test_repaint_preserves_all_samples_outside_interval(self):
        self.run_take(start=0.25, end=0.75)
        self.assertEqual(b"repaint", self.service.fields["task_type"])
        self.assertNotIn("ref_audio", self.service.fields)
        self.assertEqual(b"explicit", self.service.fields["chunk_mask_mode"])
        self.assertEqual(b"0.25", self.service.fields["repainting_start"])
        self.assertEqual(b"0.75", self.service.fields["repainting_end"])
        data = self.samples()
        first, last = 11025 * 4, 33075 * 4
        self.assertEqual(self.pcm[:first], data[:first])
        self.assertEqual(self.pcm[last:], data[last:])
        self.assert_generated(data[first + 2000:first + 2004])
        self.assertEqual(self.pcm[first:first + 4], data[first:first + 4])

    def test_reference_passage_uploads_matching_original_window_as_cover(self):
        rate = render.RATE
        parent = timed_samples(60 * rate, 2000)
        original = timed_samples(60 * rate, 4000)
        wet = timed_samples(26 * rate, 8000)
        self.source.write_bytes(wav_bytes(rate, 2, parent))
        reference = self.source.with_name("original.wav")
        reference.write_bytes(wav_bytes(rate, 2, original))
        self.service.audio = wav_bytes(rate, 2, wet)
        render.render_take(self.source, self.target, "Piano", duration=60, start=30, end=32, reference=reference)
        fields = self.service.fields
        self.assertEqual(b"cover", fields["task_type"])
        self.assertEqual(b"0.8", fields["audio_cover_strength"])
        self.assertEqual(26, float(fields["audio_duration"]))
        self.assertTrue({"repainting_start", "repainting_end", "chunk_mask_mode"}.isdisjoint(fields))
        with wave.open(io.BytesIO(fields["src_audio"]), "rb") as wav:
            self.assertEqual(26 * rate, wav.getnframes())
            self.assertEqual(original[4 * 18 * rate:4 * 44 * rate], wav.readframes(wav.getnframes()))
        with wave.open(io.BytesIO(fields["ref_audio"]), "rb") as wav:
            self.assertEqual(26 * rate, wav.getnframes())
            self.assertEqual(parent[4 * 18 * rate:4 * 44 * rate], wav.readframes(wav.getnframes()))
        with wave.open(str(self.target), "rb") as wav:
            self.assertEqual(60 * rate, wav.getnframes())
            result = wav.readframes(wav.getnframes())
        self.assertEqual(parent[:4 * 30 * rate], result[:4 * 30 * rate])
        self.assertEqual(parent[4 * 32 * rate:], result[4 * 32 * rate:])
        start, end = 30 * rate + 441, 32 * rate - 441
        self.assertEqual(wet[(start - 18 * rate) * 4:(end - 18 * rate) * 4], result[start * 4:end * 4])

    def test_upload_selected_as_its_own_melody_reference_has_no_separate_timbre_track(self):
        self.run_take(start=.2, end=.8, reference=self.source)
        self.assertEqual(b"cover", self.service.fields["task_type"])
        self.assertNotIn("ref_audio", self.service.fields)

    def test_reference_cover_rejects_short_context_even_after_selected_phrase(self):
        reference = self.source.with_name("original.wav")
        reference.write_bytes(wav_bytes(render.RATE, 2, timed_samples(render.RATE, 9000)))
        self.target.write_bytes(b"previous take")
        # Only the last real context frame is missing; the requested middle phrase is complete.
        self.service.audio = wav_bytes(render.RATE, 2, self.pcm[:-4])
        with self.assertRaisesRegex(RuntimeError, "requested timeline"):
            self.run_take(start=.25, end=.75, reference=reference)
        self.assertEqual(b"previous take", self.target.read_bytes())

    def test_foreign_file_and_failed_job_cannot_replace_previous_take(self):
        self.target.write_bytes(b"previous")
        self.service.file_url = "https://example.invalid/v1/audio?path=take.wav"
        with self.assertRaisesRegex(RuntimeError, "outside"):
            self.run_take()
        self.service.status = 2
        with self.assertRaisesRegex(RuntimeError, "generation rejected"):
            self.run_take()
        self.assertEqual(b"previous", self.target.read_bytes())

    def test_invalid_or_short_audio_does_not_publish(self):
        for value in (b"not a WAV", wav_bytes(44100, 2, self.pcm[:4000])):
            with self.subTest(size=len(value)):
                self.service.audio = value
                with self.assertRaises(RuntimeError):
                    self.run_take()
                self.assertFalse(self.target.exists())


class NativeContextTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "source.wav"
        self.output = self.source.with_name("out.wav")
        self.enterContext(patch.dict(os.environ, {"DJOUND_RENDITION_BACKEND": "native"}))

    def exercise(self, frames, first, last, left, right, mask_start, mask_end, *, timbre_span=None):
        rate = render.RATE
        source = timed_samples(frames, 4000)
        self.source.write_bytes(wav_bytes(rate, 2, source))
        reference = None
        conditioning = source
        if timbre_span is not None:
            reference = self.source.with_name("original.wav")
            conditioning = timed_samples(frames, 10000)
            reference.write_bytes(wav_bytes(rate, 2, conditioning))
        size = right - left
        padded = (size + 3527) // 3528 * 3528
        generated = timed_samples(padded, -9000)

        def synth(model_input, download, caption, *, duration, start, end, deadline, cover_strength, timbre_reference):
            expected_strength = 0.9 if reference else (0.8 if first is None else 0.55)
            self.assertEqual(cover_strength, expected_strength)
            self.assertEqual((start, end), (mask_start, mask_end))
            self.assertEqual(padded, round(duration * rate))
            with wave.open(str(model_input), "rb") as wav:
                self.assertEqual(wav.getnframes(), padded)
                self.assertEqual(wav.readframes(size), conditioning[4 * left:4 * right])
                self.assertEqual(wav.readframes(padded - size), bytes((padded - size) * 4))
            if timbre_span is None:
                self.assertIsNone(timbre_reference)
            else:
                a, b = timbre_span
                with wave.open(str(timbre_reference), "rb") as wav:
                    self.assertEqual((2, 2, rate, b - a), wav.getparams()[:4])
                    self.assertEqual(source[4 * a:4 * b], wav.readframes(b - a))
            download.write_bytes(wav_bytes(rate, 2, generated))

        with patch.object(render, "generate_native", autospec=True, side_effect=synth) as synth_call:
            render.render_take(self.source, self.output, "Instrumental piano", duration=frames / rate,
                               start=None if first is None else first / rate, end=None if last is None else last / rate,
                               cover_strength=0.9 if reference else (0.8 if first is None else 0.55),
                               reference=reference)
        synth_call.assert_called_once()
        with wave.open(str(self.output), "rb") as wav:
            self.assertEqual(wav.getnframes(), frames)
            output = wav.readframes(frames)
        if first is None:
            self.assertEqual(output, generated[:4 * frames])
            return
        self.assertEqual(output[:4 * first], source[:4 * first])
        self.assertEqual(output[4 * last:], source[4 * last:])
        a, b = first + (441 if first else 0), last - (441 if last < frames else 0)
        self.assertEqual(output[4 * a:4 * b], generated[4 * (a - left):4 * (b - left)])

    def test_beginning_middle_and_ending_on_a_sixty_second_parent(self):
        rate = render.RATE
        for start, end, left, right, relative_start, relative_end in (
                (0, 2, 0, 26, 0, 2),
                (30.501, 32.756, 18.48, 44.8, 12, 14.28),
                (58, 60, 34, 60, 24, 26)):
            with self.subTest(start=start):
                self.exercise(60 * rate, round(start * rate), round(end * rate),
                              round(left * rate), round(right * rate), relative_start, relative_end)

    def test_wide_selection_uses_whole_source_and_keeps_full_selected_span(self):
        rate = render.RATE
        self.exercise(60 * rate, 13 * rate, 48 * rate, 0, 60 * rate, 13, 48)

    def test_reference_uses_cover_for_the_corresponding_middle_ending_and_full_timeline(self):
        rate = render.RATE
        self.exercise(60 * rate, 30 * rate, 32 * rate, 18 * rate, 44 * rate, None, None,
                      timbre_span=(18 * rate, 44 * rate))
        self.exercise(60 * rate + 17, 59 * rate, 60 * rate + 17, round(34.96 * rate), 60 * rate + 17,
                      None, None, timbre_span=(round(34.96 * rate), 60 * rate + 17))
        self.exercise(rate + 17, None, None, 0, rate + 17, None, None, timbre_span=(0, rate + 17))

    def test_wide_reference_covers_limit_parent_timbre_to_thirty_seconds_around_the_edit(self):
        rate = render.RATE
        self.exercise(90 * rate, 54 * rate, 70 * rate, 42 * rate, 82 * rate, None, None,
                      timbre_span=(47 * rate, 77 * rate))
        self.exercise(60 * rate, None, None, 0, 60 * rate, None, None, timbre_span=(15 * rate, 45 * rate))

    def test_missing_misaligned_or_overwritten_reference_is_rejected_before_generation(self):
        rate = render.RATE
        self.source.write_bytes(wav_bytes(rate, 2, timed_samples(rate, 1000)))
        original = self.source.with_name("original.wav")
        self.output.write_bytes(b"previous take")
        with patch.object(render, "generate_native") as synth:
            def take(target):
                render.render_take(self.source, target, "Jazz", duration=1, start=.2, end=.8, reference=original)
            with self.assertRaisesRegex(ValueError, "reference recording is missing"):
                take(self.output)
            original_bytes = wav_bytes(rate, 2, timed_samples(rate + 1, 3000))
            original.write_bytes(original_bytes)
            with self.assertRaisesRegex(ValueError, "matching timelines"):
                take(self.output)
            with self.assertRaisesRegex(ValueError, "separately"):
                take(original)
            alias = self.source.with_name("reference-link.wav")
            alias.hardlink_to(original)
            with self.assertRaisesRegex(ValueError, "separately"):
                take(alias)
            self.assertEqual(original_bytes, original.read_bytes())
            self.assertEqual(b"previous take", self.output.read_bytes())
            synth.assert_not_called()

    def test_fractional_last_patch_is_padded_only_on_input(self):
        rate = render.RATE
        self.exercise(rate + 17, None, None, 0, rate + 17, None, None)
        frames = 60 * rate + 17
        self.exercise(frames, 59 * rate, frames, round(34.96 * rate), frames, 24.04, 25.08)

    def test_ten_minute_cover_needs_no_crop_or_padding(self):
        basis, scratch = Mock(), Mock()
        frames = 600 * render.RATE
        self.assertEqual((basis, 0, frames, frames), render._model_input(basis, frames, None, None, scratch))

    def test_invalid_strength_is_rejected_before_synthesis(self):
        for strength in (float("nan"), float("inf"), -0.1, 1.1, "0.8", True):
            with self.subTest(strength=strength), self.assertRaisesRegex(ValueError, "Cover strength"):
                render.render_take(self.source, self.output, "Piano", duration=60, cover_strength=strength)


if __name__ == "__main__":
    unittest.main()

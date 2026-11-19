"""Generate instrumental covers and repaints with native or HTTP ACE-Step 1.5."""
import array
from contextlib import closing
import http.client
import io
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
import wave

from rendition_native import generate_native, generate_native_part, native_available, native_part_available


RATE = 44100
VAE_FRAME = RATE // 25
NATIVE_PATCH = 2 * VAE_FRAME
REPAINT_CONTEXT = 12 * RATE
TIMBRE_FRAMES = 30 * RATE  # ACE-Step's reference encoder uses 30 seconds of acoustic context.
MELODY_COVER_STRENGTH = 0.8
MAX_SECONDS = 600  # Published upper bound for the ACE-Step 1.5 API.
MAX_DOWNLOAD = 256 * 1024 * 1024  # 600s of 48 kHz stereo float32 WAV plus its header.
JSON_LIMIT = 1024 * 1024
BLOCK = 64 * 1024
POLL_SECONDS = 2


def _remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise RuntimeError("ACE-Step exceeded its configured time limit.")
    return left


def rendition_deadline(shared: float | None = None) -> float:
    """Return an absolute monotonic cutoff shared by the steps of one rendition."""
    timeout = float(os.environ.get("DJOUND_ACE_TIMEOUT_SECONDS", "1200"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("DJOUND_ACE_TIMEOUT_SECONDS must be a positive number.")
    cutoff = time.monotonic() + timeout
    if shared is not None:
        if type(shared) not in (float, int) or not math.isfinite(shared):
            raise ValueError("Choose a finite monotonic deadline.")
        cutoff = min(cutoff, shared)
    _remaining(cutoff)
    return cutoff


def _origin(url):
    if url.scheme not in ("http", "https") or not url.hostname or url.username is not None or url.password is not None:
        raise ValueError("Configure DJOUND_ACE_URL with the HTTP(S) origin of your ACE-Step server.")
    return url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80)


class _AceClient:
    def __init__(self, deadline):
        url = urlsplit(os.environ.get("DJOUND_ACE_URL", "http://127.0.0.1:8001"))
        self.origin = _origin(url)
        if url.path not in ("", "/") or url.query or url.fragment:
            raise ValueError("DJOUND_ACE_URL must be an origin without a path or query.")
        self.deadline = deadline
        self.key = os.environ.get("DJOUND_ACE_API_KEY")

    def request(self, method, target, *, body=None, headers=None, download=None):
        # Use a direct connection, never a system proxy; do not follow redirects.
        scheme, host, port = self.origin
        connect = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        connection = connect(host, port, timeout=min(30, _remaining(self.deadline)))
        request_headers = dict(headers or {})
        if self.key:
            request_headers["Authorization"] = "Bearer " + self.key
        limit = MAX_DOWNLOAD if download else JSON_LIMIT
        try:
            connection.request(method, target, body=body, headers=request_headers)
            with connection.getresponse() as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"ACE-Step returned HTTP {response.status} for {target.split('?')[0]}.")
                expected = response.getheader("Content-Length")
                if expected is not None and not 0 <= int(expected) <= limit:
                    raise RuntimeError("ACE-Step returned an oversized response.")
                with (download.open("wb") if download else io.BytesIO()) as output:
                    total = 0
                    while True:
                        _remaining(self.deadline)
                        chunk = response.read1(BLOCK)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > limit:
                            raise RuntimeError("ACE-Step returned an oversized response.")
                        output.write(chunk)
                    if not total or (expected is not None and int(expected) != total):
                        raise RuntimeError("ACE-Step returned an incomplete response.")
                    if download is not None:
                        return
                    payload = json.loads(output.getvalue())
        except (OSError, http.client.HTTPException) as exc:
            raise RuntimeError("Could not reach the configured ACE-Step service. Check its API server and connection.") from exc
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("ACE-Step returned an invalid API response.") from exc
        finally:
            connection.close()
        if not isinstance(payload, dict) or payload.get("code") != 200 or payload.get("error"):
            reason = payload.get("error") if isinstance(payload, dict) else None
            raise RuntimeError(f"ACE-Step rejected the request: {str(reason or 'invalid API response')[:240]}")
        return payload.get("data")

    def submit(self, audio, fields, *, timbre_reference=None):
        boundary = secrets.token_hex(24)
        fields_body = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
                              for key, value in fields.items()).encode()
        files = [("src_audio", "source.wav", audio)]
        if timbre_reference is not None:
            files.append(("ref_audio", "timbre.wav", timbre_reference))
        uploads = []
        for name, filename, path in files:
            header = (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                      'Content-Type: audio/wav\r\n\r\n').encode()
            uploads.append((header, path))
        suffix = f"--{boundary}--\r\n".encode()
        length = len(fields_body) + sum(len(header) + path.stat().st_size + 2 for header, path in uploads) + len(suffix)
        headers = {"Content-Type": "multipart/form-data; boundary=" + boundary,
                   "Content-Length": str(length)}

        def upload():
            yield fields_body
            for header, path in uploads:
                yield header
                with path.open("rb") as stream:
                    while chunk := stream.read(BLOCK):
                        _remaining(self.deadline)
                        yield chunk
                yield b"\r\n"
            yield suffix

        with closing(upload()) as chunks:
            data = self.request("POST", "/release_task", body=chunks, headers=headers)
        task = data.get("task_id") if isinstance(data, dict) else None
        if not isinstance(task, str) or not 0 < len(task) <= 256:
            raise RuntimeError("ACE-Step did not return a generation task ID.")
        return task

    def download_take(self, task, destination):
        query = json.dumps({"task_id_list": [task]}).encode()
        while True:
            rows = self.request("POST", "/query_result", body=query, headers={"Content-Type": "application/json"})
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("task_id") != task:
                raise RuntimeError("ACE-Step returned an unexpected generation task.")
            row = rows[0]
            if row.get("status") != 0:
                break
            time.sleep(min(POLL_SECONDS, _remaining(self.deadline)))
        try:
            results = json.loads(row["result"]) if isinstance(row.get("result"), str) else row.get("result")
        except ValueError as exc:
            raise RuntimeError("ACE-Step returned an invalid generation result.") from exc
        item = results[0] if isinstance(results, list) and len(results) == 1 and isinstance(results[0], dict) else {}
        if row.get("status") != 1 or item.get("status", 1) != 1:
            reason = item.get("error") or row.get("error") or "check the ACE-Step server log"
            raise RuntimeError(f"ACE-Step generation failed: {str(reason)[:240]}")
        if not isinstance(item.get("file"), str):
            raise RuntimeError("ACE-Step completed without a downloadable audio file.")
        url = urlsplit(item["file"])
        if (url.scheme or url.netloc) and _origin(url) != self.origin:
            raise RuntimeError("ACE-Step returned an audio link outside the configured service.")
        if url.path != "/v1/audio" or not url.query or url.fragment:
            raise RuntimeError("ACE-Step returned an invalid audio download link.")
        self.request("GET", url.path + "?" + url.query, download=destination)


def _use_native():
    mode = os.environ.get("DJOUND_RENDITION_BACKEND", "auto").strip().lower()
    if mode not in ("auto", "native", "http"):
        raise ValueError("Set DJOUND_RENDITION_BACKEND to auto, native, or http.")
    return mode == "native" or (mode == "auto" and not os.environ.get("DJOUND_ACE_URL") and native_available())


def part_available() -> bool:
    """HTTP Turbo uses its ordinary cover unless a separate native Base model is configured."""
    return (_use_native() or bool(os.environ.get("DJOUND_ACE_BASE_MODEL"))) and native_part_available()


def check_service() -> str:
    if _use_native():
        if not native_available():
            raise RuntimeError("Set up the native ACE-Step runtime and model files first.")
        return "acestep-v15-turbo GGUF"
    data = _AceClient(time.monotonic() + 3).request("GET", "/health")
    if not isinstance(data, dict) or data.get("service") != "ACE-Step API" or data.get("status") != "ok":
        raise RuntimeError("The configured service is not a ready ACE-Step API.")
    model = data.get("loaded_model")
    if data.get("models_initialized") is not True or not isinstance(model, str) or not model.startswith("acestep-v15-"):
        raise RuntimeError("ACE-Step has not loaded its 1.5 audio model. Check the service log.")
    return model


def _canonical(source, temp_path, deadline, *, generated=False):
    """Convert audio format only. Preserve original samples for canonical input."""
    if not generated:
        if not source.is_file() or not 0 < source.stat().st_size <= MAX_DOWNLOAD:
            raise ValueError("Choose an audio recording of up to 256 MiB.")
        try:
            with wave.open(str(source), "rb") as wav:
                if wav.getparams()[:3] == (2, 2, RATE):
                    frames = wav.getnframes()
                    if 0 < frames <= MAX_SECONDS * RATE:
                        wav.setpos(frames - 1)
                        if len(wav.readframes(1)) == 4:
                            return source, frames
        except (EOFError, wave.Error):
            pass
    command = ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-protocol_whitelist", "file,pipe",
               "-format_whitelist", "wav,mp3,flac,aiff,ogg,mov,aac"]
    if generated:
        command += ["-f", "wav"]  # Accept only the requested audio format from the server.
    command += ["-i", str(source.resolve()), "-map", "0:a:0", "-t", str(MAX_SECONDS + 2),
                "-map_metadata", "-1", "-ar", str(RATE), "-ac", "2", "-c:a", "pcm_s16le",
                "-fflags", "+bitexact", "-flags:a", "+bitexact", str(temp_path)]
    try:
        subprocess.run(command, check=True, timeout=min(90, _remaining(deadline)),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Could not decode the recording as audio. Check ffmpeg and the WAV returned by ACE-Step.") from exc
    with wave.open(str(temp_path), "rb") as wav:
        return temp_path, wav.getnframes()


def _fade(original, generated, direction):
    """Crossfade inside a selected boundary; the surrounding PCM is copied verbatim."""
    dry, wet = array.array("h"), array.array("h")
    dry.frombytes(original)
    wet.frombytes(generated)
    if sys.byteorder != "little":
        dry.byteswap()
        wet.byteswap()
    frames = len(wet) // 2
    for i in range(frames):
        gain = i / max(1, frames - 1)
        if direction == "out":
            gain = 1 - gain
        for j in (2 * i, 2 * i + 1):
            wet[j] = round(dry[j] + gain * (wet[j] - dry[j]))
    if sys.byteorder != "little":
        wet.byteswap()
    return wet.tobytes()


def _copy_pcm(reader, begin, stop, writer, *, require_sound=False):
    reader.setpos(begin)
    heard = False
    while begin < stop:
        count = min(BLOCK, stop - begin)
        chunk = reader.readframes(count)
        if len(chunk) != 4 * count:
            raise RuntimeError("The take ended before the requested selection.")
        if require_sound and not heard:
            heard = any(chunk)
        writer.writeframesraw(chunk)
        begin += count
    if require_sound and not heard:
        raise RuntimeError("The generated instrumental part contains only silence.")


def _round_up(frames, grid):
    return ((frames + grid - 1) // grid) * grid


def _context_bounds(frames, start, end):
    left, right = 0, frames
    if start is not None:
        width = min(frames, end - start + 2 * REPAINT_CONTEXT)
        # Carry unused head/tail context to the other side at the song boundaries.
        begin = min(max(0, start - REPAINT_CONTEXT), frames - width)
        bound_left = begin // NATIVE_PATCH * NATIVE_PATCH
        bound_right = min(frames, _round_up(begin + width, NATIVE_PATCH))
        if (bound_right - bound_left) * 4 < frames * 3:
            left, right = bound_left, bound_right
    return left, right


def _selected_frames(frames, start, end):
    if not (0 <= start < end <= frames / RATE):
        raise ValueError("Choose a valid passage within the source recording.")
    first, last = round(start * RATE), round(end * RATE)
    if first == last:
        raise ValueError("The selected passage contains no audio samples.")
    return first, last


def _pcm_frames(reader):
    if reader.getparams()[:3] != (2, 2, RATE) or not 0 < reader.getnframes() <= MAX_SECONDS * RATE:
        raise ValueError("Choose a canonical 44.1 kHz stereo PCM16 recording.")
    return reader.getnframes()


def context_window(parent: Path, *, start: float, end: float) -> tuple[int, int]:
    """Find the unpadded model context on a canonical parent's timeline, in frames."""
    with wave.open(str(parent), "rb") as reader:
        frames = _pcm_frames(reader)
    return _context_bounds(frames, *_selected_frames(frames, start, end))


def crop_context(source: Path, target: Path, left_frame: int, right_frame: int) -> None:
    """Copy a canonical context into a fresh WAV, excluding the right endpoint."""
    with wave.open(str(source), "rb") as reader:
        frames = _pcm_frames(reader)
        if type(left_frame) is not int or type(right_frame) is not int or not 0 <= left_frame < right_frame <= frames:
            raise ValueError("Choose frame bounds inside the context recording.")
        with tempfile.TemporaryDirectory(prefix=".crop-", dir=target.parent) as directory:
            chunk = Path(directory) / "context.wav"
            with wave.open(str(chunk), "wb") as writer:
                writer.setparams((2, 2, RATE, right_frame - left_frame, "NONE", "not compressed"))
                _copy_pcm(reader, left_frame, right_frame, writer)
            os.link(chunk, target)


def _model_input(basis, frames, start, end, target):
    """Return the model input, its origin on the full timeline, and the real and padded sizes."""
    left, right = _context_bounds(frames, start, end)
    size = right - left
    padded = _round_up(size, NATIVE_PATCH)
    if left == 0 and size == frames == padded:
        return basis, left, size, padded
    with wave.open(str(basis), "rb") as reader, wave.open(str(target), "wb") as writer:
        writer.setparams((2, 2, RATE, padded, "NONE", "not compressed"))
        _copy_pcm(reader, left, right, writer)
        # The VAE can drop fractional latent frames. Give it a complete last
        # patch so the decoder generates every sample of the requested recording.
        writer.writeframesraw(bytes((padded - size) * 4))
    return target, left, size, padded


def _timbre_excerpt(parent, offset, size, start, end, target):
    """Sample the selected parent's sound inside the melody window, centered on the edit."""
    center = offset + size // 2 if start is None else (start + end) // 2
    length = min(TIMBRE_FRAMES, size)
    begin = max(offset, min(center - length // 2, offset + size - length))
    with wave.open(str(parent), "rb") as reader, wave.open(str(target), "wb") as writer:
        writer.setparams((2, 2, RATE, length, "NONE", "not compressed"))
        _copy_pcm(reader, begin, begin + length, writer)
    return target


def _publish(source, generated, frames, start, end, target, *, wet_offset=0):
    with wave.open(str(source), "rb") as dry, wave.open(str(generated), "rb") as wet, wave.open(str(target), "wb") as out:
        out.setparams((2, 2, RATE, frames, "NONE", "not compressed"))

        def crossfade(pos, count, direction):
            if count:
                dry.setpos(pos)
                wet.setpos(pos - wet_offset)
                out.writeframesraw(_fade(dry.readframes(count), wet.readframes(count), direction))

        if start is None:
            _copy_pcm(wet, 0, frames, out)
            return
        fade = min(RATE // 100, (end - start) // 2)
        left, right = (fade if start else 0), (fade if end < frames else 0)
        _copy_pcm(dry, 0, start, out)
        crossfade(start, left, "in")
        _copy_pcm(wet, start + left - wet_offset, end - right - wet_offset, out)
        crossfade(end - right, right, "out")
        _copy_pcm(dry, end, frames, out)


def _separate_output(destination, *recordings):
    for recording in recordings:
        if recording is not None and (recording.resolve() == destination.resolve() or
                                      (destination.exists() and recording.exists() and destination.samefile(recording))):
            raise ValueError("Save a new take separately from its input recordings.")


def generate_part(context: Path, output: Path, *, lane: str, caption: str, deadline: float) -> None:
    """Generate the requested instrumental part on exactly this canonical recording's timeline."""
    deadline = rendition_deadline(deadline)
    _separate_output(output, context)
    if not _use_native() and not os.environ.get("DJOUND_ACE_BASE_MODEL"):
        raise RuntimeError("Configure a separate native Base model to generate parts alongside HTTP covers.")
    with wave.open(str(context), "rb") as wav:
        frames = _pcm_frames(wav)
        wav.setpos(frames - 1)
        if len(wav.readframes(1)) != 4:
            raise ValueError("The instrumental part context is truncated.")
    with tempfile.TemporaryDirectory(prefix=".part-", dir=output.parent) as directory:
        work = Path(directory)
        source, _, _, padded = _model_input(context, frames, None, None, work / "context.wav")
        raw = work / "generated.wav"
        generate_native_part(source, raw, lane=lane, caption=caption, duration=padded / RATE, deadline=deadline)
        generated, length = _canonical(raw, work / "decoded.wav", deadline, generated=True)
        if length != padded:
            raise RuntimeError("The generated instrumental part does not match the context timeline.")
        ready = work / "ready.wav"
        with wave.open(str(generated), "rb") as wav, wave.open(str(ready), "wb") as writer:
            writer.setparams((2, 2, RATE, frames, "NONE", "not compressed"))
            _copy_pcm(wav, 0, frames, writer, require_sound=True)
        _remaining(deadline)
        os.replace(ready, output)


def publish_scoped_take(parent: Path, refined: Path, destination: Path, *, start: float, end: float,
                        wet_offset_frames: int = 0, deadline: float | None = None) -> None:
    """Place a full refinement or a timeline-aligned excerpt into the unchanged parent."""
    deadline = rendition_deadline(deadline)
    _separate_output(destination, parent, refined)
    with tempfile.TemporaryDirectory(prefix=".rendition-", dir=destination.parent) as directory:
        work = Path(directory)
        dry, frames = _canonical(parent, work / "parent.wav", deadline)
        first, last = _selected_frames(frames, start, end)
        wet, length = _canonical(refined, work / "refined.wav", deadline)
        if type(wet_offset_frames) is not int or not 0 <= wet_offset_frames <= first or wet_offset_frames + length < last:
            raise ValueError("The refinement must cover the entire selected passage on the parent timeline.")
        output = work / "ready.wav"
        _publish(dry, wet, frames, first, last, output, wet_offset=wet_offset_frames)
        _remaining(deadline)
        os.replace(output, destination)


def render_take(source: Path, destination: Path, caption: str, *, start: float | None = None,
                end: float | None = None, duration: float, cover_strength: float = 0.55,
                reference: Path | None = None, cover_noise_strength: float = 0.0,
                timbre_reference: Path | None = None, deadline: float | None = None) -> None:
    """Publish on the parent's timeline. A separate reference supplies melody while the parent supplies timbre."""
    if not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
        raise ValueError("Choose a positive duration of up to ten minutes.")
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 4096:
        raise ValueError("Provide a short description of the instrumental arrangement.")
    if type(cover_strength) not in (int, float) or not math.isfinite(cover_strength) or not 0 <= cover_strength <= 1:
        raise ValueError("Cover strength must be a finite number from 0 to 1.")
    if type(cover_noise_strength) not in (int, float) or not 0 <= cover_noise_strength <= 1:
        raise ValueError("Cover noise strength must be a finite number from 0 to 1.")
    if (start is None) != (end is None) or (start is not None and not 0 <= start < end <= duration):
        raise ValueError("Choose a valid passage within the source recording.")
    repaint = start is not None and reference is None
    if repaint and cover_noise_strength:
        raise ValueError("Source latent initialization requires a cover.")
    _separate_output(destination, source, reference, timbre_reference)
    deadline = rendition_deadline(deadline)
    with tempfile.TemporaryDirectory(prefix=".rendition-", dir=destination.parent) as directory:
        work = Path(directory)
        basis, frames = _canonical(source, work / "basis.wav", deadline)
        if not 0 < frames <= MAX_SECONDS * RATE or abs(frames - round(duration * RATE)) > RATE // 10:
            raise ValueError("The provided duration does not match the source recording.")
        first = last = None
        if start is not None:
            first, last = round(start * RATE), min(round(end * RATE), frames)
            if not 0 <= first < last <= frames:
                raise ValueError("The selected passage contains no audio samples.")
        model_input = basis
        if reference is not None:
            if not reference.is_file():
                raise ValueError("The original melody reference recording is missing.")
            model_input, reference_frames = _canonical(reference, work / "reference.wav", deadline)
            if reference_frames != frames:
                raise ValueError("The original melody reference and parent must have matching timelines.")
            cover_strength = max(cover_strength, MELODY_COVER_STRENGTH)

        native = _use_native()
        offset, real_size, padded = 0, frames, frames
        if native or reference is not None:
            model_input, offset, real_size, padded = _model_input(model_input, frames, first, last, work / "context.wav")
        timbre = None
        if timbre_reference is not None:
            clip, clip_frames = _canonical(timbre_reference, work / "timbre-source.wav", deadline)
            if clip_frames == frames:
                timbre = _timbre_excerpt(clip, offset, real_size, first, last, work / "timbre.wav")
            else:
                timbre = _timbre_excerpt(clip, 0, clip_frames, None, None, work / "timbre.wav")
        elif reference is not None and not reference.samefile(source):
            timbre = _timbre_excerpt(basis, offset, real_size, first, last, work / "timbre.wav")
        # Repaint masks the requested music. Cover keeps the original phrase available to the model.
        download = work / "generated.wav"
        if native:
            # The native mask uses whole 40ms VAE frames. Publish only the
            # requested sample interval even when it begins or ends between frames.
            mask_start = ((first - offset) // VAE_FRAME * VAE_FRAME) / RATE if repaint else None
            mask_end = _round_up(last - offset, VAE_FRAME) / RATE if repaint else None
            generate_native(model_input, download, caption.strip(), duration=padded / RATE,
                            start=mask_start, end=mask_end, deadline=deadline, cover_strength=cover_strength,
                            timbre_reference=timbre, cover_noise_strength=cover_noise_strength)
        else:
            fields = {"task_type": "repaint" if repaint else "cover", "prompt": caption.strip(),
                      "lyrics": "[Instrumental]", "vocal_language": "unknown", "thinking": "false",
                      "use_cot_caption": "false", "use_cot_language": "false", "use_cot_metas": "false",
                      "audio_duration": str(padded / RATE), "audio_format": "wav", "batch_size": "1"}
            if repaint:
                fields.update(repainting_start=str(first / RATE), repainting_end=str(last / RATE), chunk_mask_mode="explicit")
            else:
                fields["audio_cover_strength"] = str(cover_strength)
                if cover_noise_strength:
                    fields["cover_noise_strength"] = str(cover_noise_strength)
            client = _AceClient(deadline)
            task = client.submit(model_input, fields, timbre_reference=timbre)
            client.download_take(task, download)
        generated, length = _canonical(download, work / "decoded.wav", deadline, generated=True)
        # ACE's audio token grid may add a partial trailing cell. Never stretch, loop,
        # or fill a missing requested passage with the input.
        required = last - offset if repaint else real_size
        if abs(length - real_size) > RATE // 4 or length < required:
            raise RuntimeError("ACE-Step returned audio that does not cover the requested timeline.")
        output = work / "ready.wav"
        _publish(basis, generated, frames, first, last, output, wet_offset=offset)
        _remaining(deadline)
        os.replace(output, destination)

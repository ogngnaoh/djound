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

from rendition_native import generate_native, native_available


RATE = 44100
VAE_FRAME = RATE // 25
NATIVE_PATCH = 2 * VAE_FRAME
REPAINT_CONTEXT = 12 * RATE
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

    def submit(self, audio, fields):
        boundary = secrets.token_hex(24)
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
                 for key, value in fields.items()]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="src_audio"; filename="source.wav"\r\n'
                     'Content-Type: audio/wav\r\n\r\n')
        prefix = "".join(parts).encode()
        suffix = f"\r\n--{boundary}--\r\n".encode()
        headers = {"Content-Type": "multipart/form-data; boundary=" + boundary,
                   "Content-Length": str(len(prefix) + audio.stat().st_size + len(suffix))}

        def upload():
            yield prefix
            with audio.open("rb") as stream:
                while chunk := stream.read(BLOCK):
                    _remaining(self.deadline)
                    yield chunk
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


def _copy_pcm(reader, begin, stop, writer):
    reader.setpos(begin)
    while begin < stop:
        count = min(BLOCK, stop - begin)
        chunk = reader.readframes(count)
        if len(chunk) != 4 * count:
            raise RuntimeError("The take ended before the requested selection.")
        writer.writeframesraw(chunk)
        begin += count


def _round_up(frames, grid):
    return ((frames + grid - 1) // grid) * grid


def _native_input(basis, frames, start, end, target):
    """Return the model input, its origin on the full timeline, and the real and padded sizes."""
    left, right = 0, frames
    if start is not None:
        width = min(frames, end - start + 2 * REPAINT_CONTEXT)
        # Carry unused head/tail context to the other side at the song boundaries.
        begin = min(max(0, start - REPAINT_CONTEXT), frames - width)
        bound_left = begin // NATIVE_PATCH * NATIVE_PATCH
        bound_right = min(frames, _round_up(begin + width, NATIVE_PATCH))
        if (bound_right - bound_left) * 4 < frames * 3:
            left, right = bound_left, bound_right
    size = right - left
    padded = _round_up(size, NATIVE_PATCH)
    if left == 0 and size == frames == padded:
        return basis, left, size, padded
    with wave.open(str(basis), "rb") as reader, wave.open(str(target), "wb") as writer:
        writer.setparams((2, 2, RATE, padded, "NONE", "not compressed"))
        _copy_pcm(reader, left, right, writer)
        # The native VAE drops fractional latent frames. Give it a complete last
        # patch so the decoder generates every sample of the requested recording.
        writer.writeframesraw(bytes((padded - size) * 4))
    return target, left, size, padded


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


def render_take(source: Path, destination: Path, caption: str, *, start: float | None = None,
                end: float | None = None, duration: float, cover_strength: float = 0.55) -> None:
    """Write a complete stereo PCM16 take atomically after an actual ACE-Step generation."""
    if not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
        raise ValueError("Choose a positive duration of up to ten minutes.")
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 4096:
        raise ValueError("Provide a short description of the instrumental arrangement.")
    if type(cover_strength) not in (int, float) or not math.isfinite(cover_strength) or not 0 <= cover_strength <= 1:
        raise ValueError("Cover strength must be a finite number from 0 to 1.")
    if (start is None) != (end is None) or (start is not None and not 0 <= start < end <= duration):
        raise ValueError("Choose a valid passage within the source recording.")
    if source.resolve() == destination.resolve():
        raise ValueError("Save a new take separately from its source.")
    timeout = float(os.environ.get("DJOUND_ACE_TIMEOUT_SECONDS", "1200"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("DJOUND_ACE_TIMEOUT_SECONDS must be a positive number.")
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryDirectory(prefix=".rendition-", dir=destination.parent) as directory:
        work = Path(directory)
        basis, frames = _canonical(source, work / "basis.wav", deadline)
        if not 0 < frames <= MAX_SECONDS * RATE or abs(frames - round(duration * RATE)) > RATE // 10:
            raise ValueError("The provided duration does not match the source recording.")
        fields = {"task_type": "cover" if start is None else "repaint", "prompt": caption.strip(),
                  "lyrics": "[Instrumental]", "vocal_language": "unknown", "thinking": "false",
                  "use_cot_caption": "false", "use_cot_language": "false", "use_cot_metas": "false",
                  "audio_duration": str(frames / RATE), "audio_format": "wav", "batch_size": "1"}
        first = last = None
        if start is None:
            # Balance the source contour against the caption's request for new instrumentation.
            fields["audio_cover_strength"] = str(cover_strength)
        else:
            first, last = round(start * RATE), min(round(end * RATE), frames)
            if not 0 <= first < last <= frames:
                raise ValueError("The selected passage contains no audio samples.")
            fields.update(repainting_start=str(first / RATE), repainting_end=str(last / RATE), chunk_mask_mode="explicit")
        download = work / "generated.wav"
        offset, real_size = 0, frames
        if _use_native():
            model_input, offset, real_size, padded = _native_input(basis, frames, first, last, work / "context.wav")
            # The native mask uses whole 40ms VAE frames. Publish only the
            # requested sample interval even when it begins or ends between frames.
            mask_start = None if first is None else ((first - offset) // VAE_FRAME * VAE_FRAME) / RATE
            mask_end = None if last is None else _round_up(last - offset, VAE_FRAME) / RATE
            generate_native(model_input, download, caption.strip(), duration=padded / RATE,
                            start=mask_start, end=mask_end, deadline=deadline, cover_strength=cover_strength)
        else:
            client = _AceClient(deadline)
            task = client.submit(basis, fields)
            client.download_take(task, download)
        generated, length = _canonical(download, work / "decoded.wav", deadline, generated=True)
        # ACE's audio token grid may add a partial trailing cell. Never stretch, loop,
        # or fill a missing requested passage with the input.
        if abs(length - real_size) > RATE // 4 or length < (last - offset if last is not None else frames):
            raise RuntimeError("ACE-Step returned audio that does not cover the requested timeline.")
        output = work / "ready.wav"
        _publish(basis, generated, frames, first, last, output, wet_offset=offset)
        os.replace(output, destination)

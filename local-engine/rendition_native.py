"""Run the installed, source-conditioned ACE-Step GGUF synthesizer for one instrumental take."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave


DEFAULT_HOME = Path(__file__).resolve().parents[1] / ".runtime" / "ace-native"
MODELS = ("acestep-v15-turbo-Q4_K_M.gguf", "Qwen3-Embedding-0.6B-Q8_0.gguf", "vae-BF16.gguf")
BASE_MODEL = "acestep-v15-base-Q4_K_M.gguf"
BASE_BYTES = 1445710208
BASE_SHA256 = "942ec6a7bc11a40809af5f337063f2af2acc63e4fcd4a92b0cb42dc2bc7c41e8"
PART_LANES = frozenset(("drums", "bass", "guitar", "keyboard", "percussion", "strings", "synth", "fx", "brass", "woodwinds"))
_verified_base = None


def _installation():
    home = Path(os.environ.get("DJOUND_ACE_NATIVE_HOME") or DEFAULT_HOME).expanduser().resolve()
    return home / "build" / "ace-synth", home / "models"


def native_available() -> bool:
    executable, models = _installation()
    return executable.is_file() and os.access(executable, os.X_OK) and all((models / name).is_file() for name in MODELS)


def _base_checkpoint(deadline=None):
    global _verified_base
    executable, models = _installation()
    if not executable.is_file() or not os.access(executable, os.X_OK) or not all((models / n).is_file() for n in MODELS[1:]):
        return None
    path = Path(os.environ.get("DJOUND_ACE_BASE_MODEL") or models / BASE_MODEL).expanduser()
    try:
        if not path.is_file():
            return None
        with path.open("rb") as model:
            stat = os.fstat(model.fileno())
            if stat.st_size != BASE_BYTES:
                return None
            identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if identity != _verified_base:
                digest = hashlib.sha256()
                while chunk := model.read(4 * 1024 * 1024):
                    if deadline is not None and time.monotonic() >= deadline:
                        raise RuntimeError("Native ACE-Step exceeded its time limit verifying the Base model.")
                    digest.update(chunk)
                if digest.hexdigest() != BASE_SHA256:
                    return None
                _verified_base = identity
        return path.resolve(strict=True)
    except OSError:
        return None


def native_part_available() -> bool:
    return _base_checkpoint() is not None


@contextmanager
def _synth_lease(executable, deadline):
    # Lock the installed executable's inode, including on read-only installations. Processes
    # using the same native engine share one slot. The child inherits this open descriptor:
    # if the Java app or Python worker dies, the next run still waits until the actual synth exits.
    with executable.open("rb") as lease:
        while True:
            remaining = deadline - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                raise RuntimeError("Native ACE-Step exceeded its time limit waiting for the rendition engine.")
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(.25, remaining))
        yield lease.fileno()


def generate_native(source: Path, destination: Path, caption: str, *, duration: float,
                    start: float | None = None, end: float | None = None, deadline: float,
                    cover_strength: float = 0.55, timbre_reference: Path | None = None,
                    cover_noise_strength: float = 0.0) -> None:
    """Produce a full-timeline raw 48 kHz WAV. The caller canonicalizes it and splices the selected range."""
    if not native_available():
        raise RuntimeError("Set up the native ACE-Step rendition engine with bash local-engine/renditions.sh setup.")
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 4096:
        raise ValueError("Provide a short description of the instrumental arrangement.")
    if type(cover_strength) not in (float, int) or not 0 <= cover_strength <= 1:
        raise ValueError("Choose a cover strength between 0 and 1.")
    if type(cover_noise_strength) not in (float, int) or not 0 <= cover_noise_strength <= 1:
        raise ValueError("Choose a cover noise strength between 0 and 1.")
    if start is not None and cover_noise_strength:
        raise ValueError("Source latent initialization requires a cover.")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Choose a source with a valid duration.")
    if (start is None) != (end is None) or (start is not None and not 0 <= start < end <= duration):
        raise ValueError("Choose a passage inside the selected recording.")
    _, models = _installation()
    params = {
        "task_type": "cover" if start is None else "repaint",
        "caption": caption.strip(), "lyrics": "[Instrumental]", "output_format": "wav16",
        "synth_batch_size": 1, "duration": duration, "inference_steps": 8, "guidance_scale": 1.0, "shift": 3.0,
    }
    if start is None:
        params["audio_cover_strength"] = float(cover_strength)
        if cover_noise_strength:
            params["cover_noise_strength"] = float(cover_noise_strength)
    else:
        params.update(repainting_start=start, repainting_end=end)
    _synthesize(source, destination, params, models / MODELS[0], deadline, timbre_reference=timbre_reference)


def generate_native_part(source: Path, destination: Path, *, lane: str, caption: str, duration: float,
                         deadline: float) -> None:
    """Generate one instrumental LEGO lane in context with the pinned Base model."""
    if not isinstance(lane, str) or lane not in PART_LANES:
        raise ValueError("Choose a supported instrumental accompaniment lane.")
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 4096:
        raise ValueError("Describe the instrumental part in up to 4096 characters.")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Choose a part context with a valid duration.")
    checkpoint = _base_checkpoint(deadline)
    if checkpoint is None:
        raise RuntimeError("Install the pinned native ACE-Step Base model to generate separate instrumental parts.")
    params = {
        "synth_model": BASE_MODEL, "task_type": "lego", "track": lane, "caption": caption.strip(),
        "lyrics": "[Instrumental]", "output_format": "wav16", "synth_batch_size": 1, "duration": duration,
        "inference_steps": 50, "guidance_scale": 1.0, "shift": 1.0,
    }
    _synthesize(source, destination, params, checkpoint, deadline)


def _synthesize(source, destination, params, checkpoint, deadline, *, timbre_reference=None):
    source = source.resolve(strict=True)
    if destination.exists() or destination.is_symlink():
        raise ValueError("The raw rendition needs a fresh output path.")
    executable, models = _installation()
    with tempfile.TemporaryDirectory(prefix=".ace-native-", dir=destination.parent) as tmp:
        work = Path(tmp).resolve()
        # The pinned CLI selects its text encoder from a directory scan. Expose only the
        # three verified setup assets even when the user has other GGUFs in their installation.
        registry = work / "models"
        registry.mkdir()
        (registry / params.get("synth_model", MODELS[0])).symlink_to(checkpoint)
        for name in MODELS[1:]:
            (registry / name).symlink_to(models / name)
        request = work / "take.json"
        request.write_text(json.dumps(params, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        # The one-shot CLI evicts each model before the next stage. Its smaller VAE tiles
        # bound transient activation memory on the 16 GiB target.
        args = [str(executable), "--models", str(registry), "--src-audio", str(source),
                "--vae-chunk", "256", "--vae-overlap", "64", "--request", str(request)]
        if timbre_reference is not None:
            args.extend(["--ref-audio", str(timbre_reference.resolve(strict=True))])
        with _synth_lease(executable, deadline) as lease:
            try:
                result = subprocess.run(args, cwd=work, stdin=subprocess.DEVNULL, stdout=sys.stderr,
                                        stderr=sys.stderr, timeout=max(.001, deadline - time.monotonic()),
                                        pass_fds=(lease,), check=False)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Native ACE-Step exceeded its configured time limit.") from exc
            except OSError as exc:
                raise RuntimeError("The native ACE-Step synthesizer could not start. Check its installation.") from exc
        if result.returncode:
            raise RuntimeError(f"Native ACE-Step stopped before completing the rendition (exit {result.returncode}).")
        # ace-synth appends the batch index (zero) to the request basename. It can exit 0
        # even when audio_write fails; require a real decoded output before publishing it.
        generated = work / "take0.wav"
        try:
            with wave.open(str(generated), "rb") as audio:
                if (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) != (2, 48000, 2) or not audio.getnframes():
                    raise ValueError("Expected a stereo 48 kHz PCM16 rendition.")
                if params["task_type"] == "lego" and audio.getnframes() != round(params["duration"] * 48000):
                    raise ValueError("The part length differs from its source context.")
                audio.setpos(audio.getnframes() - 1)
                if len(audio.readframes(1)) != 4:
                    raise ValueError("The rendition is truncated.")
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            raise RuntimeError("Native ACE-Step did not produce a usable rendered WAV.") from exc
        os.link(generated, destination)

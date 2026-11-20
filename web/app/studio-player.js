const audio = document.querySelector("#audio");
const canvas = document.querySelector("#wave");
const scrub = document.querySelector("#scrub");
const play = document.querySelector("#play");
const elapsed = document.querySelector("#elapsed");
const durationLabel = document.querySelector("#duration");
const ruler = document.querySelector("#ruler");
let peaks,
  generation = 0,
  pendingAt = null,
  shouldPlay = false,
  frame;
const context = canvas.getContext("2d");
const fmt = (value) => {
  const t = Math.max(0, Math.floor(Number(value) || 0));
  return Math.floor(t / 60) + ":" + String(t % 60).padStart(2, "0");
};
function paint() {
  const w = canvas.clientWidth,
    h = canvas.clientHeight;
  if (!w || !h || !context) return;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  if (
    canvas.width !== Math.round(w * dpr) ||
    canvas.height !== Math.round(h * dpr)
  ) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, w, h);
  context.fillStyle = "#393a32";
  for (let i = 0; i <= 24; i++)
    context.fillRect(((w - 1) * i) / 24, h - (i % 4 ? 3 : 7), 1, i % 4 ? 3 : 7);
  context.fillStyle = "#48463b";
  context.fillRect(0, h / 2, w, 1);
  const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
  const fraction = duration ? currentTime() / duration : 0;
  if (peaks) {
    const count = Math.max(1, Math.floor(w / 3)),
      pitch = w / count;
    for (let x = 0; x < count; x++) {
      const value =
        peaks[
          Math.min(peaks.length - 1, Math.floor((x / count) * peaks.length))
        ];
      const bar = Math.max(2, value * (h - 17));
      context.fillStyle = x / count <= fraction ? "#efc59c" : "#c29d77";
      context.fillRect(
        x * pitch,
        Math.round((h - bar) / 2),
        Math.max(1, pitch - 1),
        bar,
      );
    }
  }
  const seekX = Math.min(w - 2, Math.max(1, w * fraction));
  context.fillStyle = "#f4e2cc";
  context.fillRect(seekX, 0, 1, h - 3);
  context.beginPath();
  context.moveTo(seekX - 3, 0);
  context.lineTo(seekX + 4, 0);
  context.lineTo(seekX + 0.5, 6);
  context.closePath();
  context.fill();
}
function refresh() {
  const d = Number.isFinite(audio.duration) ? audio.duration : 0,
    t = currentTime();
  elapsed.textContent = fmt(t);
  durationLabel.textContent = fmt(d);
  scrub.max = String(d || 1);
  scrub.value = String(t);
  scrub.disabled = !d;
  scrub.setAttribute("aria-valuetext", fmt(t) + " of " + fmt(d));
  paint();
}
function tick() {
  refresh();
  frame = audio.paused ? undefined : requestAnimationFrame(tick);
}
function startFrames() {
  cancelAnimationFrame(frame);
  tick();
}
new ResizeObserver(paint).observe(canvas);
export function currentTime() {
  return pendingAt ?? (audio.currentTime || 0);
}
function seek(t) {
  if (!Number.isFinite(audio.duration)) return;
  audio.currentTime = Math.min(audio.duration, Math.max(0, t));
  refresh();
}
export function createPlayer({ getWaveform, icon, report }) {
  async function audition(audioUrl, at, autoPlay) {
    const token = ++generation;
    pendingAt = at;
    shouldPlay = autoPlay;
    audio.pause();
    peaks = null;
    refresh();
    const url = new URL(audioUrl, location.href).href;
    if (audio.src !== url || audio.error) {
      audio.src = url;
      audio.load();
    }
    const finish = async () => {
      if (token !== generation) return;
      const target = Number.isFinite(at) && at >= 0 ? at : 0;
      pendingAt = null;
      seek(autoPlay && target >= audio.duration - 0.06 ? 0 : target);
      if (shouldPlay) {
        try {
          await audio.play();
        } catch (e) {
          if (token === generation && e.name !== "AbortError")
            report("Press Play to hear this performance.");
        }
      }
    };
    if (audio.readyState >= 1) await finish();
    else audio.addEventListener("loadedmetadata", finish, { once: true });
    getWaveform(url)
      .then((values) => {
        if (generation === token) {
          peaks = values;
          paint();
        }
      })
      .catch(() => {
        if (generation === token)
          report(
            "The waveform could not be read. Playback controls are still available.",
          );
      });
  }
  function updatePlay() {
    const active = !audio.paused;
    play.innerHTML = icon(active ? "pause" : "play");
    play.setAttribute("aria-label", active ? "Pause" : "Play");
    startFrames();
  }
  audio.addEventListener("loadedmetadata", () => {
    ruler.replaceChildren(
      ...Array.from({ length: 7 }, (_, i) => {
        const tick = document.createElement("span");
        tick.textContent = fmt((audio.duration * i) / 6);
        return tick;
      }),
    );
    refresh();
  });
  audio.addEventListener("play", updatePlay);
  audio.addEventListener("pause", updatePlay);
  audio.addEventListener("ended", () => {
    shouldPlay = false;
    updatePlay();
  });
  audio.addEventListener("error", () =>
    report(
      "This performance could not be loaded. Select another edit or return to the Library.",
    ),
  );
  audio.addEventListener("timeupdate", refresh);
  play.addEventListener("click", async () => {
    if (!audio.paused) {
      shouldPlay = false;
      audio.pause();
    } else {
      shouldPlay = true;
      try {
        await audio.play();
      } catch {
        report("This performance could not be played.");
      }
    }
  });
  document.querySelector("#rewind").addEventListener("click", () => seek(0));
  scrub.addEventListener("input", () => seek(scrub.valueAsNumber));
  scrub.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      seek(currentTime() + (e.key === "ArrowRight" ? 1 : -1));
    }
  });
  return {
    audition,
    isPlaying: () => !audio.paused || (pendingAt !== null && shouldPlay),
  };
}

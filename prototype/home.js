const byId = (id) => document.getElementById(id);
const player = byId("player"),
  play = byId("play"),
  seek = byId("seek");
const elapsed = byId("elapsed"),
  length = byId("length"),
  error = byId("error");
const buttons = { original: byId("original"), edit: byId("edit") };

const AudioContextClass = window.AudioContext || window.webkitAudioContext;
const OfflineContextClass =
  window.OfflineAudioContext || window.webkitOfflineAudioContext;
const buffers = new Map();
const loading = new Map();
const voices = new Map();
const crossfadeTime = 0.032;
let selected =
  buttons.edit.getAttribute("aria-pressed") === "true" ? "edit" : "original";
let heard = selected,
  duration = 0,
  offset = 0,
  startedAt = 0;
let playing = false,
  wantsPlay = false,
  command = 0,
  frame = 0,
  lastFrame = 0;
let decoder, context, analyser, samples;
let fromAngle = 0,
  toAngle = 0,
  fadeStart = 0;

function showError(message = "") {
  error.textContent = message;
  error.hidden = !message;
}

function clock(t) {
  return Math.floor(t / 60) + ":" + String(Math.floor(t % 60)).padStart(2, "0");
}

function position(time = context?.currentTime ?? 0) {
  return Math.max(
    0,
    Math.min(duration, offset + (playing ? time - startedAt : 0)),
  );
}

const studioLink = document.querySelector("[data-studio]");
function updateStudioLink(t) {
  const edit = selected === "edit" ? "jazz" : "original";
  studioLink.href =
    "studio.html?project=zanarkand&edit=" + edit + "&at=" + t.toFixed(2);
}
function showTime() {
  const t = position();
  updateStudioLink(t);
  seek.style.setProperty(
    "--progress",
    (duration ? (100 * t) / duration : 0) + "%",
  );
  const stamp = clock(t),
    total = duration ? clock(duration) : "–:––";
  if (elapsed.textContent !== stamp) elapsed.textContent = stamp;
  if (length.textContent !== total) length.textContent = total;
  seek.disabled = !duration;
  seek.max = String(duration);
  seek.value = String(t);
  seek.setAttribute("aria-valuetext", stamp + (duration ? " of " + total : ""));
}

function publish(level = 0) {
  window.dispatchEvent(
    new CustomEvent("musicstate", {
      detail: { playing, source: playing ? heard : selected, level },
    }),
  );
}

function reflectState() {
  player.dataset.playing = String(playing);
  player.dataset.source = selected;
  play.dataset.playing = String(playing);
  play.setAttribute(
    "aria-label",
    playing
      ? "Pause playback"
      : wantsPlay
        ? "Cancel playback"
        : "Play To Zanarkand",
  );
  for (const [name, button] of Object.entries(buttons))
    button.setAttribute("aria-pressed", String(name === selected));
  showTime();
  publish();
}

function animate(time) {
  frame = 0;
  if (!playing) return;
  if (time - lastFrame >= 1000 / 30) {
    lastFrame = time;
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (const value of samples) sum += value * value;
    showTime();
    publish(Math.min(1, Math.sqrt(sum / samples.length)));
  }
  frame = requestAnimationFrame(animate);
}

async function load(name) {
  if (buffers.has(name)) return buffers.get(name);
  if (loading.has(name)) return loading.get(name);
  const pending = (async () => {
    const response = await fetch("media/" + name + ".wav");
    if (!response.ok) throw new Error("Audio unavailable");
    // These two aligned, stereo 44.1 kHz excerpts stay on the same sample clock.
    decoder ??= new OfflineContextClass(2, 1, 44100);
    const buffer = await decoder.decodeAudioData(await response.arrayBuffer());
    buffers.set(name, buffer);
    if (selected === name) {
      duration = buffer.duration;
      showTime();
    }
    return buffer;
  })();
  loading.set(name, pending);
  try {
    return await pending;
  } finally {
    loading.delete(name);
  }
}

function angleAt(now) {
  const blend = Math.max(0, Math.min(1, (now - fadeStart) / crossfadeTime));
  return fromAngle + (toAngle - fromAngle) * blend;
}

function gainAt(name, angle) {
  if (name === "original") return angle >= Math.PI / 2 ? 0 : Math.cos(angle);
  return angle === 0 ? 0 : Math.sin(angle);
}

function retire() {
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
  if (!context) return;
  const now = context.currentTime;
  for (const [name, { node, gain }] of voices) {
    gain.gain.cancelScheduledValues(now);
    gain.gain.setValueAtTime(gainAt(name, angleAt(now)), now);
    gain.gain.linearRampToValueAtTime(0, now + 0.006);
    node.onended = () => {
      node.disconnect();
      gain.disconnect();
    };
    node.stop(now + 0.006);
  }
  voices.clear();
}

function pause(at = position()) {
  command++;
  wantsPlay = false;
  offset = at;
  playing = false;
  retire();
  reflectState();
}

function addVoice(name, at, time, gainValue) {
  const node = context.createBufferSource();
  const gain = context.createGain();
  node.buffer = buffers.get(name);
  node.connect(gain).connect(analyser);
  gain.gain.setValueAtTime(gainValue, time);
  node.onended = () => {
    if (
      voices.get(name)?.node === node &&
      playing &&
      position() >= duration - 0.015
    )
      pause(duration);
  };
  voices.set(name, { node, gain });
  node.start(time, at);
}

function begin(name, at) {
  retire();
  const now = context.currentTime;
  heard = name;
  duration = buffers.get(name).duration;
  offset = Math.max(0, Math.min(at, duration));
  startedAt = now;
  fromAngle = toAngle = name === "original" ? 0 : Math.PI / 2;
  fadeStart = now;
  playing = true;
  addVoice(name, offset, now, 0);
  voices.get(name).gain.gain.linearRampToValueAtTime(1, now + 0.006);
  reflectState();
  frame = requestAnimationFrame(animate);
}

function crossfade(name) {
  const now = context.currentTime;
  const angle = angleAt(now);
  if (!voices.has(name))
    addVoice(name, position(now), now, gainAt(name, angle));
  fromAngle = angle;
  toAngle = name === "original" ? 0 : Math.PI / 2;
  fadeStart = now;
  // The active and incoming sources share the AudioContext clock. A rapid
  // reversal starts at the actual intermediate mix, instead of snapping a gain.
  for (const [source, { gain }] of voices) {
    gain.gain.cancelScheduledValues(now);
    gain.gain.setValueAtTime(gainAt(source, angle), now);
    for (let i = 1; i <= 16; i++) {
      const fraction = i / 16;
      gain.gain.linearRampToValueAtTime(
        gainAt(source, angle + (toAngle - angle) * fraction),
        now + crossfadeTime * fraction,
      );
    }
  }
  heard = name;
  reflectState();
}

async function requestPlay(restart = false) {
  const id = ++command,
    name = selected;
  wantsPlay = true;
  showError();
  reflectState();
  try {
    if (!AudioContextClass || !OfflineContextClass)
      throw new Error("Web Audio unavailable");
    if (!context) {
      context = new AudioContextClass();
      context.addEventListener("statechange", () => {
        if (playing && context.state !== "running") pause();
      });
      analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      analyser.connect(context.destination);
      samples = new Float32Array(analyser.fftSize);
    }
    // Resume within this actual click, before awaiting a network request.
    const unlock = context.state === "running" ? undefined : context.resume();
    await Promise.all([unlock, load(name)]);
    if (id !== command || !wantsPlay) return;
    if (!playing || restart)
      begin(name, restart || offset >= duration ? 0 : offset);
    else if (heard !== name) crossfade(name);
  } catch (ex) {
    if (id !== command) return;
    pause();
    showError(
      ex?.name === "NotAllowedError"
        ? "Press Play to allow audio and try again."
        : "This audio could not play. Press Play to retry.",
    );
  }
}

function choose(name) {
  if (name === selected) return;
  selected = name;
  const id = ++command;
  showError();
  if (wantsPlay) {
    void requestPlay();
    return;
  }
  reflectState();
  load(name).catch(() => {
    if (id === command)
      showError("This audio could not load. Press Play to retry.");
  });
}

function seekTo(t) {
  if (!duration || !Number.isFinite(t)) return;
  const target = Math.max(0, Math.min(duration, t));
  if (target >= duration) {
    pause(duration);
    return;
  }
  if (playing) begin(heard, target);
  else {
    offset = target;
    showTime();
  }
}

for (const [name, button] of Object.entries(buttons))
  button.addEventListener("click", () => choose(name));
play.addEventListener("click", () => {
  if (wantsPlay) pause();
  else void requestPlay();
});
seek.min = "0";
seek.step = "any";
seek.addEventListener("input", () => seekTo(seek.valueAsNumber));
seek.addEventListener("keydown", (event) => {
  const t = position(),
    delta = Math.min(5, duration / 10);
  const targets = {
    ArrowLeft: t - delta,
    ArrowDown: t - delta,
    ArrowRight: t + delta,
    ArrowUp: t + delta,
    PageDown: t - duration / 10,
    PageUp: t + duration / 10,
    Home: 0,
    End: duration,
  };
  if (Object.hasOwn(targets, event.key)) {
    event.preventDefault();
    seekTo(targets[event.key]);
  }
});
window.addEventListener("pagehide", () => pause());
showError();
reflectState();
for (const name of Object.keys(buttons)) {
  load(name).catch(() => {
    if (selected === name && !wantsPlay)
      showError("This audio could not load. Press Play to retry.");
  });
}

byId("hear-request").addEventListener("click", () => {
  choose("edit");
  void requestPlay(true);
  player.scrollIntoView({
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "instant"
      : "smooth",
    block: "center",
  });
  play.focus({ preventScroll: true });
});
const roomCanvas = byId("turntable");
let hitLiveRecord = null;
function isOnRecord(event) {
  if (roomCanvas.dataset.ready && hitLiveRecord)
    return hitLiveRecord(event.clientX, event.clientY);
  // Keep the rendered sleeves, furniture and controls out of the static hit area.
  const mobilePose = matchMedia(
    "(max-width:720px),(max-width:850px) and (max-aspect-ratio:1/1)",
  ).matches;
  const [w, h, cx, cy, rx, ry] = mobilePose
    ? [390, 586, 164, 293, 103, 47]
    : [1440, 845, 890, 429, 274, 114];
  const rect = roomCanvas.getBoundingClientRect();
  const scale = Math.max(rect.width / w, rect.height / h);
  const x = (event.clientX - rect.left - (rect.width - w * scale) / 2) / scale;
  const y = (event.clientY - rect.top - (rect.height - h * scale) / 2) / scale;
  return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1;
}
let recordTap;
roomCanvas.addEventListener("pointerdown", (event) => {
  if (event.isPrimary && event.button === 0 && isOnRecord(event))
    recordTap = { id: event.pointerId, x: event.clientX, y: event.clientY };
});
roomCanvas.addEventListener("pointermove", (event) => {
  if (
    recordTap &&
    Math.hypot(event.clientX - recordTap.x, event.clientY - recordTap.y) > 8
  )
    recordTap = null;
  if (event.pointerType === "mouse")
    roomCanvas.style.cursor = isOnRecord(event) ? "pointer" : "";
});
roomCanvas.addEventListener("pointerup", (event) => {
  if (recordTap?.id === event.pointerId && isOnRecord(event)) play.click();
  recordTap = null;
});
roomCanvas.addEventListener("pointercancel", () => {
  recordTap = null;
});
roomCanvas.addEventListener("pointerleave", () => {
  recordTap = null;
});
import("./turntable.js")
  .then(({ mountRoom }) =>
    mountRoom(roomCanvas, (test) => {
      hitLiveRecord = test;
    }),
  )
  .catch(() => {
    // The photographed pose remains visible when WebGL is unavailable.
  });

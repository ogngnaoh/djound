import { live } from "./environment.js";
const library = live
  ? await import("./live-library.js")
  : await import("./data.js");

const byId = (id) => document.getElementById(id);
const player = byId("record-player");
const dialog = byId("import-dialog");
const candidate = byId("candidate-player");
const seek = byId("seek");
let file,
  candidateUrl,
  importing = false,
  loadRevision = 0;

function latestSession() {
  try {
    return JSON.parse(sessionStorage.getItem("djound:resume"));
  } catch {
    return null;
  }
}
function studioUrl(id, fromOriginal = false, resume) {
  const url = new URL("./studio.html", location.href);
  url.searchParams.set("project", id);
  if (fromOriginal) url.searchParams.set("edit", "original");
  else if (resume) {
    url.searchParams.set("resume", "1");
    url.searchParams.set("edit", resume.editId);
    if (Number.isFinite(resume.at) && resume.at >= 0)
      url.searchParams.set("at", resume.at);
  }
  return url.href;
}

function stamp(time) {
  const value = Number.isFinite(time) ? Math.floor(time) : 0;
  return Math.floor(value / 60) + ":" + String(value % 60).padStart(2, "0");
}

function refreshTransport() {
  const hasDuration = Number.isFinite(player.duration) && player.duration > 0;
  seek.disabled = !hasDuration;
  seek.max = hasDuration ? player.duration : 1;
  seek.value = player.currentTime || 0;
  byId("time-label").textContent =
    stamp(player.currentTime) +
    " / " +
    (hasDuration ? stamp(player.duration) : "–:––");
  seek.setAttribute(
    "aria-valuetext",
    stamp(player.currentTime) + " of " + stamp(player.duration),
  );
  byId("preview").setAttribute(
    "aria-label",
    player.paused ? "Play original" : "Pause original",
  );
  byId("preview").classList.toggle("playing", !player.paused);
}
for (const name of [
  "loadedmetadata",
  "timeupdate",
  "play",
  "pause",
  "ended",
  "durationchange",
]) {
  player.addEventListener(name, refreshTransport);
}
player.addEventListener("error", () => {
  byId("preview-error").textContent = "The original audio could not be opened.";
});
seek.addEventListener("input", () => {
  if (!seek.disabled) player.currentTime = seek.valueAsNumber;
});
byId("preview").addEventListener("click", async () => {
  if (!player.paused) {
    player.pause();
    return;
  }
  try {
    await player.play();
    byId("preview-error").textContent = "";
  } catch {
    byId("preview-error").textContent =
      "Couldn’t play this recording. Try opening it in the Studio.";
  }
});

// A small two-color sleeve for each recording, printed from its stable identifier.
function sleeveArt(project) {
  let seed = [...project.id].reduce(
    (value, c) => (Math.imul(31, value) + c.charCodeAt(0)) >>> 0,
    7,
  );
  function random() {
    seed = (Math.imul(1664525, seed) + 1013904223) >>> 0;
    return seed / 4294967296;
  }
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 560;
  const c = canvas.getContext("2d");
  const theme =
    project.id === "zanarkand"
      ? ["#ded4b5", "#264347", "#b76140"]
      : [
          ["#ede1c2", "#74504b", "#c68755"],
          ["#d3d4c0", "#414e66", "#b2664c"],
          ["#e6d5be", "#334c41", "#b17c4e"],
        ][Math.floor(random() * 3)];
  const [cream, ink, warm] = theme;
  c.fillStyle = cream;
  c.fillRect(0, 0, 560, 560);
  c.save();
  c.beginPath();
  c.rect(23, 23, 514, 486);
  c.clip();
  c.fillStyle = warm;
  c.beginPath();
  c.arc(366 + random() * 20, 152, 69, 0, Math.PI * 2);
  c.fill();
  for (let k = 0; k < 5; k++) {
    const peaks = Array.from({ length: 5 }, () => [
      random() * 7,
      4 + random() * 8,
    ]);
    const base = 171 + k * 50;
    const points = [];
    for (let x = -4; x <= 565; x += 3) {
      let y = base + 33 * Math.sin(x / 132 + k * 0.86);
      peaks.forEach(([phase, amp], i) => {
        y += amp * Math.sin(x / (8 + i * 9) + phase);
      });
      points.push([x, y]);
    }
    c.beginPath();
    c.moveTo(...points[0]);
    points.forEach(([x, y]) => c.lineTo(x, y));
    c.lineTo(570, 550);
    c.lineTo(0, 550);
    c.closePath();
    c.fillStyle = cream;
    c.fill();
    for (let depth = 0; depth < 210; depth += 3.4) {
      c.beginPath();
      points.forEach(([x, y], i) => {
        const yy =
          y +
          depth +
          Math.sin(x * 0.023 + depth * 0.03 + k) * Math.min(depth / 20, 3);
        if (i) c.lineTo(x, yy);
        else c.moveTo(x, yy);
      });
      c.strokeStyle = ink;
      c.lineWidth = k === 4 ? 1.55 : 1.0;
      c.stroke();
    }
  }
  c.restore();
  c.strokeStyle = ink;
  c.lineWidth = 1;
  c.strokeRect(22.5, 22.5, 515, 487);
  c.fillStyle = ink;
  c.font = "14px monospace";
  c.fillText("DJOUND", 24, 537);
  c.textAlign = "right";
  c.fillText("33 ⅓", 536, 537);
  for (let i = 0; i < 5200; i++) {
    c.fillStyle = i % 2 ? "rgba(20,18,12,.055)" : "rgba(255,250,224,.12)";
    c.fillRect(random() * 560, random() * 560, random() * 1.5 + 0.5, 1);
  }
  return canvas;
}

function row(project, art, resume) {
  const li = document.createElement("li");
  const link = document.createElement("a");
  link.className = "columns record-link";
  link.href = studioUrl(project.id, false, resume);
  const title = document.createElement("span");
  title.className = "rec-name";
  const image = new Image();
  image.className = "thumb";
  image.src = art;
  image.alt = "";
  const strong = document.createElement("strong");
  strong.textContent = project.title;
  title.append(image, strong);
  const count = document.createElement("span");
  const total =
    project.editCount ??
    project.edits.filter((edit) => edit.id !== "original").length;
  count.textContent = String(total);
  count.setAttribute("aria-label", total + (total === 1 ? " edit" : " edits"));
  const when = document.createElement("span");
  const date = new Date(project.updatedAt || project.createdAt);
  when.textContent = Number.isNaN(+date)
    ? "—"
    : date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
  const arrow = document.createElement("span");
  arrow.className = "row-arrow";
  arrow.textContent = "↗";
  arrow.setAttribute("aria-hidden", "true");
  link.append(title, count, when, arrow);
  li.append(link);
  return li;
}

async function loadLibrary() {
  const revision = ++loadRevision;
  try {
    const projects = (await library.listProjects()).sort(
      (a, b) =>
        new Date(b.updatedAt || b.createdAt) -
        new Date(a.updatedAt || a.createdAt),
    );
    if (revision !== loadRevision) return;
    if (!projects.length) {
      byId("load-message").textContent =
        "Bring your first recording to open it in the Studio.";
      byId("load-message").hidden = false;
      byId("feature").hidden = true;
      document.querySelector(".recordings").hidden = true;
      return;
    }
    const recent = latestSession();
    const featured =
      projects.find((project) => project.id === recent?.projectId) ||
      projects[0];
    const position =
      featured.id === recent?.projectId &&
      (live || featured.edits.some((edit) => edit.id === recent.editId))
        ? recent
        : null;
    byId("record-list").replaceChildren();
    for (const project of projects) {
      const art = sleeveArt(project);
      if (project === featured) {
        const cover = document.querySelector(".sleeve");
        cover.width = cover.height = art.width;
        cover.getContext("2d").drawImage(art, 0, 0);
      }
      const thumbnail = document.createElement("canvas");
      thumbnail.width = thumbnail.height = 64;
      thumbnail.getContext("2d").drawImage(art, 0, 0, 64, 64);
      byId("record-list").append(
        row(
          project,
          thumbnail.toDataURL(),
          project === featured ? position : null,
        ),
      );
    }
    byId("record-count").textContent = projects.length;
    byId("feature-title").textContent = featured.title;
    byId("feature-meta").textContent =
      featured.id === "zanarkand"
        ? "Final Fantasy X · Original recording"
        : "Original recording";
    byId("feature-open").href = studioUrl(featured.id, false, position);
    byId("load-message").hidden = true;
    document.querySelector(".recordings").hidden = false;
    byId("feature").hidden = false;
    byId("preview-error").textContent = "";
    try {
      let url = featured.originalAudioUrl;
      if (!url) {
        const original =
          featured.edits.find((edit) => edit.id === "original") ||
          featured.edits[0];
        url = await library.audioUrl(original.audioKey);
      }
      if (revision !== loadRevision) return;
      player.src = url;
    } catch {
      byId("preview-error").textContent = "The original audio is unavailable.";
    }
  } catch {
    byId("load-message").hidden = false;
    byId("load-message").textContent =
      "Couldn’t open your library. Reload this page to try again.";
  }
}
function clearFile() {
  candidate.pause();
  candidate.removeAttribute("src");
  candidate.load();
  if (candidateUrl) URL.revokeObjectURL(candidateUrl);
  candidateUrl = null;
  file = null;
  byId("file-input").value = "";
  byId("file-preview").hidden = true;
  byId("drop-zone").hidden = false;
  byId("import-error").textContent = "";
  byId("import-status").textContent = "";
  byId("confirm-import").disabled = true;
}
function showDialog() {
  if (dialog.open) return;
  player.pause();
  clearFile();
  dialog.showModal();
  byId("choose").focus();
}
function setFile(files) {
  if (importing || !files.length) return;
  const count = files.length;
  const next = files[0];
  clearFile();
  if (count !== 1) {
    byId("import-error").textContent = "Choose one recording at a time.";
    return;
  }
  if (live && next.size > 64 * 1024 * 1024) {
    byId("import-error").textContent =
      "Choose an audio file no larger than 64 MiB.";
    return;
  }
  const supportedName =
    /\.(wav|mp3|m4a|ogg|aac|aiff?|alac|flac|opus|oga|webm|caf|mp4)$/i.test(
      next.name,
    );
  if (!next.size || (!next.type.startsWith("audio/") && !supportedName)) {
    byId("import-error").textContent = next.size
      ? "Choose an audio file, such as MP3, WAV or M4A."
      : "This file is empty. Choose a recording with audio.";
    return;
  }
  file = next;
  candidateUrl = URL.createObjectURL(file);
  candidate.src = candidateUrl;
  byId("filename").textContent = file.name;
  byId("filesize").textContent =
    file.size < 1048576
      ? Math.ceil(file.size / 1024) + " KB"
      : (file.size / 1048576).toFixed(1) + " MB";
  byId("file-preview").hidden = false;
  byId("drop-zone").hidden = true;
  byId("confirm-import").disabled = false;
  byId("confirm-import").focus();
}
function busy(value) {
  importing = value;
  byId("confirm-import").disabled = value || !file;
  byId("close-dialog").disabled = value;
  byId("change-file").disabled = value;
  byId("import-status").textContent = value ? "Opening recording…" : "";
}
byId("confirm-import").addEventListener("click", async () => {
  if (importing || !file) return;
  busy(true);
  candidate.pause();
  byId("import-error").textContent = "";
  try {
    const project = await library.importRecording(file);
    // Returning from the Studio should show the updated Library.
    if (location.hash === "#import")
      history.replaceState(null, "", location.pathname + location.search);
    clearFile();
    dialog.close();
    location.href = studioUrl(project.id, true);
  } catch (error) {
    byId("import-error").textContent =
      error instanceof Error
        ? error.message
        : "Couldn’t open this file. Try another recording.";
  } finally {
    busy(false);
  }
});
for (const id of ["choose", "change-file"])
  byId(id).addEventListener("click", () => byId("file-input").click());
byId("file-input").addEventListener("change", (event) =>
  setFile(event.currentTarget.files),
);
const zone = byId("drop-zone");
for (const type of ["dragover", "dragenter"])
  zone.addEventListener(type, (event) => {
    event.preventDefault();
    zone.classList.add("is-over");
  });
zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
zone.addEventListener("drop", (event) => {
  event.preventDefault();
  zone.classList.remove("is-over");
  setFile(event.dataTransfer.files);
});
byId("bring").addEventListener("click", showDialog);
function closeImport() {
  if (importing) return;
  if (location.hash === "#import")
    history.replaceState(null, "", location.pathname + location.search);
  dialog.close();
}
byId("close-dialog").addEventListener("click", closeImport);
dialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeImport();
});
dialog.addEventListener("close", () => {
  if (dialog.open) return;
  clearFile();
  byId("bring").focus();
});
window.addEventListener("hashchange", () => {
  if (location.hash === "#import") showDialog();
  else if (dialog.open && !importing) dialog.close();
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) void loadLibrary();
});
window.addEventListener("pagehide", () => {
  player.pause();
  candidate.pause();
});
if (live) {
  byId("file-input").accept = ".wav,.mp3,.m4a,audio/wav,audio/mpeg,audio/mp4";
  byId("import-description").textContent =
    "Choose an MP3, WAV, or M4A recording, up to 5 minutes and 64 MiB.";
}
if (location.hash === "#import") showDialog();
void loadLibrary();

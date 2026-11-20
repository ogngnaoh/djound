import * as engine from "./live-studio.js";
import { createPlayer, currentTime } from "./studio-player.js";

const $ = (id) => document.getElementById(id);
const paths = {
  chevron: '<path d="m9 5 7 7-7 7"/>',
  export:
    '<path d="M12 3v12m-4-8 4-4 4 4M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>',
  play: '<path d="m7 4 13 8-13 8z" fill="currentColor" stroke="none"/>',
  pause:
    '<path d="M6 4h4v16H6zm8 0h4v16h-4z" fill="currentColor" stroke="none"/>',
  rewind: '<path d="M6 5v14m12-14-10 7 10 7z"/>',
  headphones:
    '<path d="M4 15v-3a8 8 0 0 1 16 0v3m-16 0a2 2 0 0 1 2-2h2v7H6a2 2 0 0 1-2-2zm16 0a2 2 0 0 0-2-2h-2v7h2a2 2 0 0 0 2-2z"/>',
  star: '<path d="m12 3 2.77 5.8 6.4.94-4.6 4.48 1.1 6.35L12 17.58l-5.67 2.99 1.08-6.35-4.6-4.48 6.4-.94z"/>',
  branch:
    '<circle cx="7" cy="5" r="2"/><circle cx="7" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M7 7v10m0-3c6 0 10-1 11-4"/>',
  sliders:
    '<path d="M4 7h7m4 0h5M4 17h2m4 0h10"/><circle cx="13" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
  vinyl:
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/><path d="M6 12a6 6 0 0 1 6-6m6 6a6 6 0 0 1-6 6"/>',
  wave: '<path d="M3 10v4m4-9v14m5-17v20m5-16v12m4-9v6"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  sidebar:
    '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  rename: '<path d="m14 6 4 4M4 20l4-1 12-12a2.8 2.8 0 0 0-4-4L4 15z"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/>',
  check: '<path d="m4 13 5 5L20 6"/>',
  quote: '<path d="M3 7h7v7H6l-2 4m10-11h7v7h-4l-2 4"/>',
};
const icon = (name) =>
  '<svg viewBox="0 0 24 24" aria-hidden="true">' + paths[name] + "</svg>";
for (const el of document.querySelectorAll("[data-icon]"))
  el.outerHTML = icon(el.dataset.icon);
const textElement = (tag, text) => {
  const el = document.createElement(tag);
  el.textContent = text;
  return el;
};
const params = new URLSearchParams(location.search);
const positions = new Map();
let connection,
  trackedJob,
  ranges = {},
  project,
  heard,
  parent,
  compareTo,
  busy = false,
  ready = null,
  undoAction,
  renameId,
  session,
  drafts = {};
const item = (id) => project?.edits.find((e) => e.id === id);
const original = () =>
  project.edits.find((e) => e.id === "original") || project.edits[0];
const isOriginal = (id) => id === original().id;
const tell = (msg) => {
  $("message").textContent = msg;
};
const player = createPlayer({
  getWaveform: engine.waveform,
  icon,
  report: tell,
});
const sessionKey = () => "djound.studio." + project.id;
function storeSession() {
  try {
    sessionStorage.setItem(
      sessionKey(),
      JSON.stringify({
        heard,
        parent,
        compareTo,
        drafts,
        ranges,
        trackedJob,
        at: currentTime(),
      }),
    );
  } catch {}
}
function comparisonChild() {
  const preferred = item(compareTo);
  if (preferred && !isOriginal(preferred.id)) return preferred;
  const listening = item(heard);
  if (listening && !isOriginal(listening.id)) return listening;
  return (
    project.edits.find((e) => e.parentId === original().id) ||
    project.edits.find((e) => !isOriginal(e.id))
  );
}
function repairSelections() {
  const fallback = original().id;
  if (!item(parent)) {
    parent = fallback;
    $("request").value = drafts[parent] || "";
  }
  if (!item(heard)) {
    const at = currentTime(),
      play = player.isPlaying();
    heard = fallback;
    void player
      .audition(original().audioKey, at, play)
      .catch(() => tell("The original recording could not be loaded."));
  }
  if (!item(compareTo)) compareTo = comparisonChild()?.id;
  if (ready && !item(ready)) {
    ready = null;
    $("job").hidden = true;
  }
}
function syncUI() {
  const focused = document.activeElement;
  repairSelections();
  $("project-title").textContent = project.title;
  document.title = project.title + " · Djound Studio";
  $("heard-name").textContent = item(heard).name;
  $("export-top").disabled = false;
  const favorite = $("favorite");
  favorite.hidden = isOriginal(heard);
  favorite.setAttribute("aria-pressed", String(!!item(heard).favorite));
  favorite.setAttribute(
    "aria-label",
    item(heard).favorite
      ? "Remove from favorites"
      : "Favorite this performance",
  );
  const list = $("edit-list");
  list.replaceChildren();
  const entries = [
    original(),
    ...project.edits.filter((e) => !isOriginal(e.id)).reverse(),
  ];
  $("edit-count").textContent = entries.length - 1;
  for (const edit of entries) {
    const li = document.createElement("li");
    li.className = "entry" + (edit.id === heard ? " current" : "");
    const choose = document.createElement("button");
    choose.type = "button";
    choose.id = "listen-" + edit.id;
    choose.className = "choose";
    choose.setAttribute("aria-label", "Listen to " + edit.name);
    choose.setAttribute("aria-pressed", String(edit.id === heard));
    const glyph = document.createElement("span");
    glyph.className = "entry-icon";
    glyph.innerHTML = icon(isOriginal(edit.id) ? "vinyl" : "wave");
    const copy = document.createElement("span");
    copy.className = "entry-copy";
    const name = textElement("b", edit.name);
    if (edit.favorite) {
      const star = textElement("span", " ★");
      star.className = "row-star";
      name.append(star);
    }
    const source = isOriginal(edit.id)
      ? "Source recording"
      : "From " +
        (item(edit.parentId)?.name || edit.parentName || "removed edit");
    copy.append(name, textElement("small", source));
    choose.append(glyph, copy);
    choose.addEventListener("click", () => {
      closeRail();
      void listen(edit.id, true);
    });
    const more = document.createElement("button");
    more.type = "button";
    more.id = "actions-" + edit.id;
    more.className = "icon more";
    more.innerHTML = icon("more");
    more.setAttribute("aria-label", "Actions for " + edit.name);
    more.setAttribute("aria-haspopup", "true");
    more.addEventListener("click", () => openMenu(more, edit.id));
    li.append(choose, more);
    list.append(li);
  }
  const selector = $("parent");
  selector.replaceChildren(
    ...entries.map((e) => {
      const opt = textElement("option", e.name);
      opt.value = e.id;
      return opt;
    }),
  );
  selector.value = parent;
  const compare = $("compare");
  compare.replaceChildren();
  const alt = comparisonChild();
  compare.hidden = !alt;
  if (alt)
    for (const edit of [item(alt.parentId) || original(), alt]) {
      const b = textElement("button", edit.name);
      b.type = "button";
      b.id = "compare-" + edit.id;
      b.title = edit.name;
      b.setAttribute("aria-pressed", String(heard === edit.id));
      b.setAttribute("aria-label", "Compare " + edit.name);
      b.addEventListener(
        "click",
        () => void listen(edit.id, player.isPlaying(), true),
      );
      compare.append(b);
    }
  const lineage = $("lineage");
  lineage.replaceChildren();
  lineage.hidden = !item(heard).prompt;
  if (item(heard).prompt) {
    lineage.innerHTML = icon("quote");
    lineage.append(textElement("span", item(heard).prompt));
  }
  const proposals = isOriginal(parent)
    ? [
        "Play it as a jazz trio",
        "Make a study soundtrack",
        "Turn it into synth-pop",
      ]
    : [
        "Keep the groove and give the melody to a cello",
        "Make the ending quieter",
        "Give it a warmer feel",
      ];
  $("suggestions").replaceChildren(
    ...proposals.map((p) => {
      const b = textElement("button", p);
      b.type = "button";
      b.addEventListener("click", () => {
        setDraft(p);
        $("request").focus();
      });
      return b;
    }),
  );
  syncSubmit();
  if (focused?.id && !focused.isConnected) {
    const replacement = document.getElementById(focused.id) || $("play");
    replacement.focus({ preventScroll: true });
  }
}
async function listen(id, autoPlay, keepPair = false, startAt) {
  if (!item(id)) return;
  positions.set(heard, currentTime());
  const at =
    startAt ??
    engine.correspondingPosition(
      currentTime(),
      item(heard)?.songTimeMap,
      item(id).songTimeMap,
      positions.get(id),
    );
  if (!keepPair) {
    if (!isOriginal(id)) compareTo = id;
    else if (item(comparisonChild()?.parentId)?.id !== original().id) {
      compareTo = project.edits.find((e) => e.parentId === original().id)?.id;
    }
  }
  const switched = heard !== id;
  heard = id;
  if (switched || autoPlay !== player.isPlaying()) {
    try {
      await player.audition(item(id).audioKey, at, autoPlay);
    } catch {
      tell("This performance could not be loaded.");
    }
  }
  syncUI();
  const url = new URL(location.href);
  url.searchParams.set("edit", heard);
  url.searchParams.delete("resume");
  url.searchParams.delete("at");
  history.replaceState({ ...history.state, studioOpened: true }, "", url);
  storeSession();
}
function setDraft(value) {
  drafts[parent] = value;
  $("request").value = value;
  syncSubmit();
  storeSession();
}
function syncSubmit() {
  $("generate").disabled = busy || !$("request").value.trim();
}
function setParent(id) {
  if (!item(id)) return;
  parent = id;
  $("request").value = drafts[id] || "";
  restoreRange();
  syncUI();
  storeSession();
}
function updateProject(next) {
  const previous = project;
  project = next;
  if (!previous || !heard) return;
  const changed =
    previous.edits.length !== next.edits.length ||
    next.edits.some((edit, i) => {
      const old = previous.edits[i];
      return (
        old?.id !== edit.id ||
        old.name !== edit.name ||
        old.favorite !== edit.favorite
      );
    });
  if (!changed) return;
  if (!item(parent)) {
    parent =
      item(previous.edits.find((e) => e.id === parent)?.parentId)?.id ||
      original().id;
    $("request").value = drafts[parent] || "";
    restoreRange();
  }
  if (!item(heard)) {
    const removed = previous.edits.find((e) => e.id === heard);
    const fallback = item(removed?.parentId) || original();
    const at = engine.correspondingPosition(
      currentTime(),
      removed?.songTimeMap,
      fallback.songTimeMap,
    );
    heard = fallback.id;
    void player
      .audition(fallback.audioKey, at, player.isPlaying())
      .catch(() => tell("The recording could not be loaded."));
  }
  syncUI();
  storeSession();
}

function restoreRange() {
  const range = ranges[parent];
  $("passage").open = !!range?.open;
  $("range-start").value = range?.start ?? "";
  $("range-end").value = range?.end ?? "";
  for (const id of ["range-start", "range-end"]) {
    $(id).max = item(parent).durationSeconds;
    $(id).disabled = !$("passage").open;
    $(id).setCustomValidity("");
  }
}
function readRange() {
  if (!$("passage").open) return {};
  const start = $("range-start");
  const end = $("range-end");
  if (!start.value && !end.value) return {};
  if (!start.value || !end.value || start.valueAsNumber >= end.valueAsNumber) {
    const field = !start.value ? start : end;
    field.setCustomValidity(
      "Enter a start and a later end within this performance.",
    );
    field.reportValidity();
    return;
  }
  return { startSeconds: start.valueAsNumber, endSeconds: end.valueAsNumber };
}
function storeRange() {
  if (!parent) return;
  $("range-start").setCustomValidity("");
  $("range-end").setCustomValidity("");
  for (const id of ["range-start", "range-end"])
    $(id).disabled = !$("passage").open;
  ranges[parent] = {
    open: $("passage").open,
    start: $("range-start").value,
    end: $("range-end").value,
  };
  storeSession();
}
$("passage").addEventListener("toggle", storeRange);
for (const id of ["range-start", "range-end"])
  $(id).addEventListener("input", storeRange);

const stages = [
  "Planning your take…",
  "Learning the tune…",
  "Arranging the instruments…",
  "Rendering the performance…",
  "Finishing the take…",
];
function showJob(take) {
  const state = take?.state;
  $("job").dataset.state = state || "";
  busy = [
    "submitting",
    "unconfirmed",
    "queued",
    "running",
    "reconnecting",
  ].includes(state);
  syncSubmit();
  ready = state === "ready" ? take.id : null;
  if (take?.id) trackedJob = take.id;
  $("job").hidden = !take;
  $("hear-result").hidden = !ready;
  $("job-actions").replaceChildren();
  $("progress").hidden = !["queued", "running", "submitting"].includes(state);
  $("progress").removeAttribute("value");
  $("progress").removeAttribute("aria-valuetext");
  $("progress").textContent = "";
  $("percentage").hidden = true;
  if (!take) {
    trackedJob = null;
    storeSession();
    return;
  }
  const action = (label, perform) => {
    const button = textElement("button", label);
    button.type = "button";
    button.className = "quiet outlined";
    button.addEventListener("click", () =>
      Promise.resolve(perform()).catch((err) => tell(err.message)),
    );
    $("job-actions").append(button);
  };
  const parentName =
    item(take.parentId)?.name || take.parentLabel || "removed edit";
  let title,
    detail,
    glyph = "sliders";
  switch (state) {
    case "submitting":
      title = "Sending your request";
      detail = "";
      break;
    case "queued":
    case "running": {
      title = state === "queued" ? "Edit queued" : "Creating your edit";
      detail =
        take.progress ||
        (state === "queued"
          ? "Waiting for the local engine"
          : "Starting the local engine");
      const index = stages.indexOf(take.progress);
      if (index >= 0) {
        $("progress").max = stages.length;
        $("progress").value = index + 1;
        $("progress").setAttribute("aria-valuetext", detail);
        $("percentage").hidden = false;
        $("percentage").textContent =
          "Stage " + (index + 1) + " of " + stages.length;
      }
      break;
    }
    case "reconnecting":
      title = "Reconnecting to the engine";
      detail = "Checking the status of your edit…";
      break;
    case "unconfirmed":
      title = "Check your request";
      detail = take.error;
      action("Reconnect and check", take.retry);
      break;
    case "ready":
      glyph = "check";
      title = item(ready).name + " is ready";
      detail = "Saved to Edits · from " + parentName;
      if (drafts[take.parentId] === take.request) {
        delete drafts[take.parentId];
        if (parent === take.parentId) $("request").value = "";
      }
      break;
    case "clarify":
    case "failed":
    case "rejected":
      glyph = state === "clarify" ? "quote" : "close";
      title =
        state === "clarify" ? "One more detail" : "Couldn’t finish this edit";
      detail = take.error || take.caption || "Try another request.";
      if (take.clarificationKind === "retained_passage") {
        action("Keep the edited passage", () =>
          connection.answer(take.id, "keep"),
        );
        action("Change the passage too", () =>
          connection.answer(take.id, "develop"),
        );
      } else if (item(take.parentId)) {
        action("Revise request", () => {
          setParent(take.parentId);
          setDraft(take.request);
          if (take.startSeconds != null && take.endSeconds != null) {
            ranges[parent] = {
              open: true,
              start: take.startSeconds,
              end: take.endSeconds,
            };
            restoreRange();
          }
          $("request").focus();
        });
      }
      break;
  }
  $("job-glyph").innerHTML = icon(glyph);
  $("job-title").textContent = title;
  $("job-detail").textContent = detail;
  syncSubmit();
  storeSession();
}

async function generate(event) {
  event.preventDefault();
  if (busy) return;
  const prompt = $("request").value.trim();
  if (!prompt) return;
  const bounds = readRange();
  if (!bounds) return;
  await connection.create({ parentId: parent, prompt, ...bounds });
}
function toast(message, undo) {
  $("toast-text").textContent = message;
  $("toast").hidden = false;
  $("undo").hidden = !undo;
  undoAction = undo;
}
async function favorite(id) {
  const edit = item(id);
  if (!edit) return;
  if (!isOriginal(id)) await connection.favorite(edit.favorite ? null : id);
}
async function exportEdit(id) {
  const edit = item(id);
  if (!edit) return;
  const a = document.createElement("a");
  // Let the browser stream the WAV and use the server's filename, which
  // includes the edit number and honors any later rename.
  const response = await fetch(edit.exportUrl, { method: "HEAD" });
  if (!response.ok) throw new Error("The audio could not be exported.");
  a.href = edit.exportUrl;
  a.download = "";
  document.body.append(a);
  a.click();
  a.remove();
  toast("Downloading " + edit.name);
}
async function remove(id) {
  if (isOriginal(id)) return;
  const deleted = item(id);
  if (!deleted) return;
  await connection.trash(id);
  toast("Deleted " + deleted.name, async () => {
    await connection.restore(id);
    toast("Restored " + deleted.name);
  });
}
function openMenu(anchor, id) {
  const menu = $("edit-menu");
  if (menu.matches(":popover-open")) menu.hidePopover();
  const edit = item(id);
  const actions = [
    [
      "branch",
      "Edit from here",
      () => {
        setParent(id);
        closeRail();
        $("request").focus();
      },
    ],
    [
      "star",
      edit.favorite ? "Remove favorite" : "Favorite",
      () => favorite(id),
    ],
    ["export", "Export audio", () => exportEdit(id)],
  ];
  if (!isOriginal(id))
    actions.push(
      [
        "rename",
        "Rename",
        () => {
          renameId = id;
          $("new-name").value = edit.name;
          $("rename").showModal();
          $("new-name").select();
        },
      ],
      ["trash", "Delete edit", () => remove(id)],
    );
  menu.replaceChildren();
  for (const [glyph, label, action] of actions) {
    if (isOriginal(id) && glyph === "star") continue;
    const b = document.createElement("button");
    b.type = "button";
    b.innerHTML = icon(glyph);
    b.append(document.createTextNode(label));
    if (glyph === "trash") b.className = "delete";
    b.addEventListener("click", () => {
      menu.hidePopover();
      Promise.resolve(action()).catch((e) => tell(e.message));
    });
    menu.append(b);
  }
  menu.showPopover();
  const rect = anchor.getBoundingClientRect();
  menu.style.left =
    Math.max(8, Math.min(rect.left + 8, innerWidth - menu.offsetWidth - 8)) +
    "px";
  menu.style.top =
    Math.max(
      8,
      Math.min(rect.bottom + 3, innerHeight - menu.offsetHeight - 8),
    ) + "px";
  menu.firstElementChild.focus();
}
function shieldWorkspace(open) {
  document.querySelector(".desk").inert = open;
  document.querySelector(".topbar").inert = open;
}
function closeRail() {
  const was = $("rail").classList.contains("open");
  $("rail").classList.remove("open");
  $("open-edits").setAttribute("aria-expanded", "false");
  shieldWorkspace(false);
  if (was && $("open-edits").offsetParent) $("open-edits").focus();
}
$("open-edits").addEventListener("click", () => {
  $("rail").classList.add("open");
  $("open-edits").setAttribute("aria-expanded", "true");
  shieldWorkspace(true);
  $("close-edits").focus();
});
$("close-edits").addEventListener("click", closeRail);
matchMedia("(max-width: 660px)").addEventListener("change", () => {
  if ($("rail").classList.contains("open")) closeRail();
});
document.addEventListener("click", (event) => {
  if (
    $("rail").classList.contains("open") &&
    !event.target.closest("#rail,#open-edits,#edit-menu,#rename")
  )
    closeRail();
});
document.addEventListener("keydown", (event) => {
  if (
    event.key === "Escape" &&
    !$("rename").open &&
    !$("edit-menu").matches(":popover-open")
  )
    closeRail();
});
$("request").addEventListener("input", () => {
  drafts[parent] = $("request").value;
  tell("");
  syncSubmit();
  storeSession();
});
$("composer").addEventListener("submit", generate);
$("request").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});
$("parent").addEventListener("change", (event) =>
  setParent(event.target.value),
);
$("hear-result").addEventListener("click", () => {
  if (item(ready)) {
    setParent(ready);
    void listen(
      ready,
      true,
      false,
      engine.passageAuditionStart(item(ready).appliedStartSeconds),
    );
  }
});
$("favorite").addEventListener("click", () =>
  favorite(heard).catch((e) => tell(e.message)),
);
$("export-top").addEventListener("click", () =>
  exportEdit(heard).catch((e) => tell(e.message)),
);
$("cancel-rename").addEventListener("click", () => $("rename").close());
$("rename-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("new-name").value.trim();
  if (!name) {
    $("new-name").setCustomValidity("Enter an edit name.");
    $("new-name").reportValidity();
    return;
  }
  const e = item(renameId);
  if (!e) return;
  try {
    await connection.rename(e.id, name);
    $("rename").close();
    syncUI();
    if (ready === e.id) $("job-title").textContent = name + " is ready";
    toast("Renamed edit");
  } catch (err) {
    tell(err.message);
  }
});
$("new-name").addEventListener("input", () =>
  $("new-name").setCustomValidity(""),
);
$("undo").addEventListener("click", () => {
  const undo = undoAction;
  undoAction = null;
  $("undo").hidden = true;
  if (undo) undo().catch((err) => tell(err.message));
});
$("dismiss-toast").addEventListener("click", () => {
  $("toast").hidden = true;
  undoAction = null;
});
window.addEventListener("pagehide", () => {
  if (project) {
    storeSession();
    try {
      sessionStorage.setItem(
        "djound:resume",
        JSON.stringify({
          projectId: project.id,
          editId: heard,
          at: currentTime(),
        }),
      );
    } catch {}
  }
});
try {
  if (params.has("demo")) await engine.resolveDemo(params);
  if (!params.get("project"))
    throw new Error("Choose a recording to open in Studio.");
  connection = engine.connect(params.get("project"), updateProject, showJob);
  await connection.load();
  if (!project?.edits.length)
    throw new Error("This recording isn’t in the Library.");
  try {
    session = JSON.parse(sessionStorage.getItem(sessionKey()) || "{}") || {};
  } catch {
    session = {};
  }
  drafts = session.drafts || {};
  ranges = session.ranges || {};
  // A direct share or homepage handoff selects the named performance. Returning
  // to a session restores the separately chosen editing parent and its draft.
  const resumeSession =
    session.heard &&
    (params.get("resume") === "1" || history.state?.studioOpened);
  const requested = resumeSession ? null : params.get("edit");
  heard =
    item(requested)?.id ||
    (requested ? original().id : item(session.heard)?.id) ||
    project.edits.find((edit) => edit.favorite)?.id ||
    original().id;
  parent = requested ? heard : item(session.parent)?.id || heard;
  restoreRange();
  compareTo = requested ? heard : item(session.compareTo)?.id || heard;
  $("request").value = drafts[parent] || "";
  $("loading").hidden = true;
  $("workspace").hidden = false;
  syncUI();
  const pos =
    Number(
      resumeSession
        ? session.at
        : (params.get("at") ?? (requested ? 0 : session.at)),
    ) || 0;
  history.replaceState({ ...history.state, studioOpened: true }, "");
  await player.audition(item(heard).audioKey, Math.max(0, pos), false);
  try {
    await connection.resume(session.trackedJob);
  } catch (err) {
    tell(err.message || "The previous request could not be recovered.");
  }
  if (requested && !item(requested))
    tell(
      "That edit was removed. Listening to Original. You can create another edit from it.",
    );
} catch (err) {
  $("workspace").hidden = true;
  $("loading").hidden = false;
  $("loading").textContent = (err?.message || "Studio could not open.") + " ";
  const back = textElement("a", "Open Library");
  back.href = "library.html";
  $("loading").append(back);
}

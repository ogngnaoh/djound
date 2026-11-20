import { getProject, saveProject, audioUrl, renderPreview } from "./data.js";
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
let project,
  heard,
  parent,
  compareTo,
  busy = false,
  ready = null,
  undoAction,
  renameId,
  session,
  drafts = {},
  saving = Promise.resolve();
const item = (id) => project?.edits.find((e) => e.id === id);
const original = () =>
  project.edits.find((e) => e.id === "original") || project.edits[0];
const isOriginal = (id) => id === original().id;
const tell = (msg) => {
  $("message").textContent = msg;
};
const player = createPlayer({ audioUrl, icon, report: tell });
const sessionKey = () => "djound.studio." + project.id;
function storeSession() {
  try {
    sessionStorage.setItem(
      sessionKey(),
      JSON.stringify({ heard, parent, compareTo, drafts, at: currentTime() }),
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
function persist() {
  saving = saving
    .then(async () => {
      await saveProject(project);
      repairSelections();
    })
    .catch((err) => {
      tell(err?.message || "This change could not be saved in the browser.");
      throw err;
    });
  const operation = saving;
  saving = saving.catch(() => {});
  return operation;
}
function syncUI() {
  repairSelections();
  $("project-title").textContent = project.title;
  document.title = project.title + " · Djound Studio";
  $("heard-name").textContent = item(heard).name;
  $("export-top").disabled = false;
  const favorite = $("favorite");
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
  const proposals = canJazz()
    ? [
        "Play it as a jazz trio",
        "Soften it for studying",
        "Give it a warmer tone",
      ]
    : [
        "Soften it for studying",
        "Give it a warmer tone",
        "Keep the groove, soften the highs",
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
}
async function listen(id, autoPlay, keepPair = false) {
  if (!item(id)) return;
  const at = currentTime();
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
  syncUI();
  storeSession();
}
function canJazz() {
  return project.id === "zanarkand" && item(parent)?.audioKey === "original";
}
function treatment(prompt) {
  const q = prompt.toLowerCase();
  if (/\bjazz(?:y)?\b|walking bass|brushed drums/.test(q) && canJazz())
    return { effect: "jazz", name: "Jazz trio" };
  if (/study|studying|focus|soften|softer|quiet|gentle|mellow|calm/.test(q))
    return { effect: "focus", name: "Study mix" };
  if (/warm|warmth|coz[yi]|deeper tone/.test(q))
    return { effect: "warm", name: "Warm mix" };
}
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function stage(value, detail) {
  $("progress").value = value;
  $("progress").textContent = value + "%";
  $("percentage").textContent = value + "%";
  $("job-detail").textContent = detail;
}
async function generate(event) {
  event.preventDefault();
  if (busy) return;
  const prompt = $("request").value.trim();
  if (!prompt) return;
  const chosen = treatment(prompt);
  if (!chosen) {
    tell(
      "This preview can play " +
        (canJazz() ? "a prepared jazz trio, " : "") +
        "a softer study mix, or a warmer mix. Choose a suggestion to hear an example.",
    );
    return;
  }
  const source = { ...item(parent) };
  busy = true;
  ready = null;
  tell("");
  syncSubmit();
  $("job").hidden = false;
  $("hear-result").hidden = true;
  $("progress").hidden = false;
  $("percentage").hidden = false;
  $("job-glyph").innerHTML = icon("sliders");
  $("job-title").textContent = "Generating edit · preview";
  stage(12, "Preparing " + source.name);
  try {
    await wait(440);
    stage(
      43,
      chosen.effect === "jazz"
        ? "Loading the prepared arrangement"
        : "Processing the selected performance",
    );
    const [audioKey] = await Promise.all([
      chosen.effect === "jazz"
        ? Promise.resolve("jazz")
        : renderPreview(source.audioKey, chosen.effect),
      wait(740),
    ]);
    stage(85, "Preparing playback and saving the edit");
    await wait(430);
    let name = chosen.name;
    let n = 2;
    while (project.edits.some((e) => e.name === name))
      name = chosen.name + " " + n++;
    const edit = {
      id: crypto.randomUUID(),
      name,
      parentId: source.id,
      parentName: source.name,
      prompt,
      favorite: false,
      audioKey,
      createdAt: new Date().toISOString(),
    };
    project.edits.push(edit);
    try {
      await persist();
    } catch (err) {
      project.edits = project.edits.filter((e) => e.id !== edit.id);
      throw err;
    }
    ready = edit.id;
    if (drafts[source.id] === prompt) {
      delete drafts[source.id];
      if (parent === source.id) $("request").value = "";
    }
    $("job-glyph").innerHTML = icon("check");
    $("job-title").textContent = name + " is ready";
    $("job-detail").textContent = "Saved to Edits · from " + source.name;
    $("progress").hidden = true;
    $("percentage").hidden = true;
    $("hear-result").hidden = false;
    syncUI();
    storeSession();
  } catch (err) {
    $("job-glyph").innerHTML = icon("close");
    $("job-title").textContent = "Couldn’t finish this edit";
    $("job-detail").textContent = err?.message || "Try the request again.";
    $("progress").hidden = true;
    $("percentage").hidden = true;
  } finally {
    busy = false;
    syncSubmit();
  }
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
  edit.favorite = !edit.favorite;
  await persist();
  syncUI();
}
async function exportEdit(id) {
  const edit = item(id);
  if (!edit) return;
  const response = await fetch(await audioUrl(edit.audioKey));
  if (!response.ok) throw new Error("The audio could not be exported.");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download =
    (project.title + " - " + edit.name).replace(/[\\/:*?"<>|]/g, "-") + ".wav";
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  toast("Exported " + edit.name);
}
async function remove(id) {
  if (isOriginal(id)) return;
  const index = project.edits.findIndex((e) => e.id === id),
    deleted = item(id);
  if (!deleted) return;
  const children = project.edits.filter((e) => e.parentId === id);
  for (const e of children) e.parentName = deleted.name;
  const fallback = item(deleted.parentId)?.id || original().id;
  const wasHeard = heard === id,
    wasParent = parent === id,
    at = currentTime(),
    wasPlaying = player.isPlaying();
  project.edits.splice(index, 1);
  if (wasParent) {
    parent = fallback;
    $("request").value = drafts[parent] || "";
  }
  if (wasHeard) heard = fallback;
  if (compareTo === id) compareTo = heard;
  await persist();
  if (wasHeard) await player.audition(item(heard).audioKey, at, wasPlaying);
  if (ready === id) {
    ready = null;
    $("job").hidden = true;
  }
  syncUI();
  storeSession();
  toast("Deleted " + deleted.name, async () => {
    project.edits.splice(Math.min(index, project.edits.length), 0, deleted);
    await persist();
    syncUI();
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
function closeRail() {
  const was = $("rail").classList.contains("open");
  $("rail").classList.remove("open");
  $("open-edits").setAttribute("aria-expanded", "false");
  if (was) $("open-edits").focus();
}
$("open-edits").addEventListener("click", () => {
  $("rail").classList.add("open");
  $("open-edits").setAttribute("aria-expanded", "true");
  $("close-edits").focus();
});
$("close-edits").addEventListener("click", closeRail);
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
    void listen(ready, true);
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
  e.name = name;
  try {
    await persist();
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
  project = await getProject(params.get("project") || "zanarkand");
  if (!project?.edits.length)
    throw new Error("This recording isn’t in this browser’s Library.");
  try {
    session = JSON.parse(sessionStorage.getItem(sessionKey()) || "{}") || {};
  } catch {
    session = {};
  }
  drafts = session.drafts || {};
  const requested = params.get("edit");
  const canonicalJazz =
    requested === "jazz" && project.id === "zanarkand" && !item(requested)
      ? project.edits.find((e) => e.audioKey === "jazz")?.id
      : null;
  heard =
    item(requested)?.id ||
    canonicalJazz ||
    (requested ? original().id : item(session.heard)?.id) ||
    original().id;
  parent = requested ? heard : item(session.parent)?.id || heard;
  compareTo = requested ? heard : item(session.compareTo)?.id || heard;
  $("request").value = drafts[parent] || "";
  $("loading").hidden = true;
  $("workspace").hidden = false;
  syncUI();
  const pos =
    Number(params.has("at") ? params.get("at") : requested ? 0 : session.at) ||
    0;
  await player.audition(item(heard).audioKey, Math.max(0, pos), false);
  if (requested && !item(requested) && !canonicalJazz)
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

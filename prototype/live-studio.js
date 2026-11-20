import { requestRendition } from "./live-api.js";
import { pendingCommand, PendingCommandConflict } from "../web/src/recovery.ts";
export {
  correspondingPosition,
  passageAuditionStart,
} from "../web/src/renditions/songTime.ts";

const segment = encodeURIComponent;
const json = (method, body) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export async function resolveDemo(params) {
  const { projectId, editId } = await requestRendition("/demo", {
    method: "POST",
  });
  params.set("project", projectId);
  params.set("edit", params.get("demo") === "edit" ? editId : "original");
  params.delete("demo");
  history.replaceState(null, "", location.pathname + "?" + params);
}

function definitiveError(error) {
  return (
    error.status >= 400 &&
    error.status < 500 &&
    ![408, 429].includes(error.status)
  );
}

function studioProject(recording) {
  const source = {
    id: "original",
    name: "Original",
    audioKey: recording.originalUrl,
    exportUrl: recording.originalUrl.replace(/\/audio$/, "/export"),
    durationSeconds: recording.durationSeconds,
  };
  return {
    id: recording.id,
    title: recording.title,
    edits: [
      source,
      ...recording.takes
        .filter((take) => take.state === "ready")
        .map((take) => ({
          id: take.id,
          parentId: take.parentId,
          parentName: take.parentLabel,
          name: take.label,
          prompt: take.request,
          favorite: recording.favoriteId === take.id,
          audioKey: take.audioUrl,
          exportUrl: take.exportUrl,
          songTimeMap: take.songTimeMap,
          durationSeconds: take.durationSeconds,
          appliedStartSeconds: take.appliedStartSeconds,
        })),
    ],
  };
}

const waveforms = new Map();
export async function waveform(url) {
  if (waveforms.has(url)) return waveforms.get(url);
  const response = await fetch(url.replace(/\/audio$/, "/waveform"));
  if (!response.ok) throw new Error("The waveform could not be loaded.");
  const values = await response.json();
  const peaks = values.map((value) => Math.sqrt(value / 32768));
  if (waveforms.size >= 8) waveforms.delete(waveforms.keys().next().value);
  waveforms.set(url, peaks);
  return peaks;
}

// Changes and status reads share an ordered queue. A slow poll must never
// replace a later rename, favorite, or trash response with an older snapshot.
export function connect(id, updateProject, showJob) {
  const base = "/" + segment(id);
  const slot = "studio-edit." + id;
  let recording,
    active = null,
    timer,
    queue = Promise.resolve(),
    submitting = false;

  function load(path = "", options) {
    const operation = queue.then(async () => {
      recording = await requestRendition(base + path, options);
      const result = studioProject(recording);
      updateProject(result);
      return result;
    });
    queue = operation.catch(() => {});
    return operation;
  }

  function display(take) {
    if (!take) {
      active = null;
      showJob(null);
      return;
    }
    active = take.id;
    showJob(take);
    if (take.state === "queued" || take.state === "running") {
      clearTimeout(timer);
      timer = setTimeout(poll, 1000);
    }
  }

  async function poll() {
    try {
      await load();
    } catch (error) {
      if (definitiveError(error)) {
        showJob({ state: "rejected", error: error.message });
        return;
      }
      showJob({ state: "reconnecting", error: error.message });
      timer = setTimeout(poll, 3000);
      return;
    }
    display(recording.takes.find((t) => t.id === active));
  }

  function handlePending(command, message) {
    showJob({
      state: "unconfirmed",
      error: message,
      retry: () => send(command),
    });
  }

  async function send(command) {
    if (submitting) return;
    submitting = true;
    clearTimeout(timer);
    showJob({ state: "submitting" });
    try {
      await pendingCommand(slot, command);
      await load(command.path, json("POST", command.body));
      const take = recording.takes.find((t) => t.requestId === command.key);
      if (!take) throw new Error("The engine has not confirmed this request.");
      await pendingCommand(slot, null, command.key);
      display(take);
    } catch (error) {
      if (error instanceof PendingCommandConflict) {
        handlePending(
          error.command,
          "An earlier request is awaiting confirmation.",
        );
      } else if (definitiveError(error)) {
        await pendingCommand(slot, null, command.key);
        showJob({ state: "rejected", error: error.message });
      } else {
        handlePending(command, error.message);
      }
    } finally {
      submitting = false;
    }
  }

  function submit(path, body) {
    const key = crypto.randomUUID();
    return send({ key, path, body: { ...body, requestId: key } });
  }

  async function resume(previous) {
    const pending = await pendingCommand(slot);
    if (pending) return send(pending);
    const latest = recording.takes.at(-1);
    const take =
      recording.takes.find((t) => ["queued", "running"].includes(t.state)) ||
      recording.takes.findLast((t) => previous && t.answerTo === previous) ||
      recording.takes.find((t) => t.id === previous) ||
      (["clarify", "failed"].includes(latest?.state) ? latest : null);
    if (take) display(take);
  }

  function changeEdit(takeId, suffix, options) {
    return load("/takes/" + segment(takeId) + suffix, options);
  }
  return {
    load,
    resume,
    create: (body) => submit("/takes", body),
    answer: (takeId, choice) =>
      submit("/takes/" + segment(takeId) + "/answer", { choice }),
    favorite: (takeId) =>
      load(
        "/favorite",
        takeId ? json("PUT", { takeId }) : { method: "DELETE" },
      ),
    rename: (takeId, label) =>
      changeEdit(takeId, "/label", json("PUT", { label })),
    trash: (takeId) => changeEdit(takeId, "/trash", { method: "PUT" }),
    restore: (takeId) => changeEdit(takeId, "/trash", { method: "DELETE" }),
  };
}

import {
  api,
  getExploration,
  getLibrary,
  type Exploration,
  type Recording,
} from "../api.ts";
import type { components } from "../api-schema";
import { pendingCommand, pendingUpload } from "../recovery.ts";
import { uploadWasRejected } from "../uploadPolicy.ts";
import { isDefiniteRejection } from "../remix/commandPolicy.ts";
import { noteIssue } from "../remix/localPolicy.ts";
import {
  DEFAULT_RECIPE,
  LOCAL_SOURCE_RECIPE,
  describeRecipe,
  type Mode,
  type Note,
  type Project,
  type Proposal,
  type Recipe,
  type Scenario,
  type StudioService,
  type Track,
} from "./types.ts";

const STORAGE = "djound.studio.prototype.v1";
const clone = <T>(value: T): T => structuredClone(value);
const abort = () =>
  new DOMException(
    "Operation cancelled. Your previous recordings are safe.",
    "AbortError",
  );
function check(signal?: AbortSignal) {
  if (signal?.aborted) throw abort();
}
function wait(ms: number, signal?: AbortSignal) {
  check(signal);
  return new Promise<void>((resolve, reject) => {
    const stop = () => {
      clearTimeout(timer);
      reject(abort());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", stop);
      resolve();
    }, ms);
    signal?.addEventListener("abort", stop, { once: true });
  });
}
function validateNotes(project: Project, notes: Note[]) {
  const issue = noteIssue(notes, project.tracks[0]?.duration ?? 12);
  if (issue) throw new Error(issue);
}
function sampleNotes(duration = 12): Note[] {
  return [64, 67, 69, 71, 72, 74, 72, 71, 69, 67].map((pitch, index) => ({
    pitch,
    start: (index * duration) / 11 + 0.12,
    end: Math.min(duration, (index * duration) / 11 + 0.72),
    velocity: 90,
  }));
}
function seedProject(id: string, title: string, versions = 3): Project {
  const recipes: Recipe[] = [
    { ...DEFAULT_RECIPE, instrument: "piano", bass: false, drums: false },
    { ...DEFAULT_RECIPE },
    { ...DEFAULT_RECIPE, drums: false, backingVolume: "soft" },
    {
      ...DEFAULT_RECIPE,
      instrument: "guitar",
      drums: false,
      backingVolume: "soft",
    },
  ];
  const tracks: Track[] = Array.from({ length: versions + 1 }, (_, n) => ({
    id: `${id}-${n}`,
    projectId: id,
    projectTitle: title,
    title: n ? `Version ${n}` : "Original",
    description: n ? describeRecipe(recipes[n % 4]) : "Uploaded original",
    url:
      n === 0
        ? "/audio/original.wav"
        : n % 2
          ? "/audio/pulse.wav"
          : "/audio/warm.wav",
    duration: 12,
    kept: n === 2,
    kind: n ? "version" : "original",
    recipe: recipes[n % 4],
  }));
  return {
    id,
    title,
    fileName:
      id === "evening"
        ? "piano-sketch.wav"
        : `${title.toLowerCase().replaceAll(" ", "-")}.wav`,
    updatedAt: new Date().toISOString(),
    tracks,
    notes: sampleNotes(),
    confirmed: versions > 0,
    noteRevision: 1,
    sourceRevision: 1,
    draftRevision: 1,
    draft: id === "evening" ? "Make the backing sparse." : "",
    basisId: tracks.at(-1)!.id,
    available: true,
    previewUrl: undefined,
  };
}
function initialProjects() {
  return [
    seedProject("evening", "Evening melody"),
    seedProject("morning", "Morning idea", 1),
    seedProject("window", "Window motif", 2),
  ];
}

async function blobStore(key: string, value?: Blob): Promise<Blob | undefined> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("djound-studio-audio-v1", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("audio");
    open.onerror = () =>
      reject(
        new Error(
          "Browser storage is unavailable. Enable storage to keep this upload.",
        ),
      );
    open.onsuccess = () => {
      const db = open.result;
      const tx = db.transaction("audio", value ? "readwrite" : "readonly");
      const request = value
        ? tx.objectStore("audio").put(value, key)
        : tx.objectStore("audio").get(key);
      tx.oncomplete = () => {
        db.close();
        resolve(value ?? request.result);
      };
      tx.onerror = () => {
        db.close();
        reject(tx.error);
      };
    };
  });
}
async function audioDuration(file: File) {
  if (!/\.(wav|mp3)$/i.test(file.name))
    throw new Error("Choose a WAV or MP3 recording.");
  if (file.size > 64 * 1024 * 1024)
    throw new Error("Choose a recording smaller than 64 MiB.");
  return new Promise<number>((resolve, reject) => {
    const audio = new Audio();
    const url = URL.createObjectURL(file);
    const finish = (value?: number) => {
      clearTimeout(timer);
      audio.src = "";
      URL.revokeObjectURL(url);
      if (value && value >= 1 && value <= 12.05) resolve(Math.min(value, 12));
      else
        reject(
          new Error(
            value
              ? "Choose a short melody between 1 and 12 seconds. No audio is trimmed automatically."
              : "This recording could not be read. Choose a valid WAV or MP3.",
          ),
        );
    };
    const timer = setTimeout(() => finish(), 10000);
    audio.onloadedmetadata = () => finish(audio.duration);
    audio.onerror = () => finish();
    audio.src = url;
  });
}
function renderNotePreview(notes: Note[], duration: number): Blob {
  const rate = 22050;
  const samples = new Float32Array(Math.ceil(duration * rate));
  for (const note of notes) {
    const frequency = 440 * 2 ** ((note.pitch - 69) / 12);
    const start = Math.floor(note.start * rate);
    const end = Math.min(samples.length, Math.ceil(note.end * rate));
    const length = (end - start) / rate;
    for (let i = start; i < end; i++) {
      const t = (i - start) / rate;
      const envelope =
        Math.min(1, t / 0.009) *
        Math.min(1, (length - t) / 0.045) *
        Math.exp(-t * 1.7);
      samples[i] +=
        (Math.sin(2 * Math.PI * frequency * t) +
          0.16 * Math.sin(4 * Math.PI * frequency * t)) *
        envelope *
        0.28 *
        (note.velocity / 127);
    }
  }
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const word = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++)
      view.setUint8(offset + i, text.charCodeAt(i));
  };
  word(0, "RIFF");
  view.setUint32(4, buffer.byteLength - 8, true);
  word(8, "WAVE");
  word(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  word(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++)
    view.setInt16(
      44 + i * 2,
      Math.max(-1, Math.min(1, samples[i])) * 32767,
      true,
    );
  return new Blob([buffer], { type: "audio/wav" });
}
function prototypeService(scenario: Scenario): StudioService {
  let projects: Project[];
  try {
    projects =
      JSON.parse(localStorage.getItem(STORAGE) || "null") ?? initialProjects();
    if (
      !Array.isArray(projects) ||
      !projects.every((p) => Array.isArray(p.tracks))
    )
      projects = initialProjects();
  } catch {
    projects = initialProjects();
  }
  if (scenario === "empty") projects = [];
  const snapshots = new Map<
    string,
    { proposal: Proposal; sourceRevision: number; draftRevision: number }
  >();
  const completed = new Map<string, string>();
  const creating = new Set<string>();
  const urls = new Map<string, string>();
  function persist() {
    localStorage.setItem(STORAGE, JSON.stringify(projects));
  }
  function find(id: string) {
    const p = projects.find((p) => p.id === id);
    if (!p)
      throw new Error("This project is unavailable. Return to your Library.");
    return p;
  }
  function ready() {
    if (scenario === "unavailable")
      throw new Error(
        "The local engine is unavailable. You can still listen to saved recordings.",
      );
  }
  async function present(p: Project) {
    const result = clone(p);
    result.available = scenario !== "unavailable";
    if (!result.available)
      result.unavailableReason =
        "The local engine is unavailable. Saved audio stays playable.";
    for (const track of result.tracks) {
      if (track.url.startsWith("stored:")) {
        const key = track.url.slice(7);
        if (!urls.has(key)) {
          const file = await blobStore(key);
          if (file) urls.set(key, URL.createObjectURL(file));
        }
        track.url = urls.get(key) ?? "/audio/missing-upload.wav";
      }
      if (scenario === "playback-failure") track.url = "/audio/unavailable.wav";
    }
    if (result.previewUrl?.startsWith("stored:")) {
      const key = result.previewUrl.slice(7);
      if (!urls.has(key)) {
        const audio = await blobStore(key);
        if (audio) urls.set(key, URL.createObjectURL(audio));
      }
      result.previewUrl = urls.get(key);
    }
    return result;
  }
  const service: StudioService = {
    async list() {
      return Promise.all(projects.map(present));
    },
    async get(id) {
      return present(find(id));
    },
    async sample() {
      const p = seedProject(crypto.randomUUID(), "New melody", 0);
      p.fileName = "sample-melody.wav";
      p.notes = [];
      p.previewUrl = undefined;
      projects.unshift(p);
      persist();
      return present(p);
    },
    async upload(file) {
      const duration = await audioDuration(file);
      const p = seedProject(
        crypto.randomUUID(),
        file.name.replace(/\.[^.]+$/, "").replaceAll(/[-_]/g, " "),
        0,
      );
      await blobStore(p.id, file);
      p.fileName = file.name;
      p.tracks[0].url = `stored:${p.id}`;
      p.tracks[0].duration = duration;
      p.notes = [];
      p.previewUrl = undefined;
      projects.unshift(p);
      persist();
      return present(p);
    },
    async keep(id, trackId, kept) {
      const p = find(id);
      const t = p.tracks.find((t) => t.id === trackId);
      if (!t || t.kind !== "version")
        throw new Error("Only created versions can be kept.");
      t.kept = kept;
      persist();
    },
    async saveDraft(project, text, basisId) {
      const p = find(project.id);
      if (!p.tracks.some((t) => t.id === basisId))
        throw new Error("Choose a recording from this project.");
      if (Array.from(text).length > 1000)
        throw new Error("Keep your request to 1,000 characters or fewer.");
      if (p.draft !== text || p.basisId !== basisId) {
        p.draft = text;
        p.basisId = basisId;
        p.draftRevision++;
        persist();
      }
      return present(p);
    },
    async detect(project, signal) {
      ready();
      await wait(700, signal);
      check(signal);
      const p = find(project.id);
      p.notes = sampleNotes(p.tracks[0].duration);
      p.confirmed = false;
      p.noteRevision++;
      p.previewUrl = undefined;
      persist();
      return present(p);
    },
    async saveNotes(project, notes, confirm) {
      validateNotes(project, notes);
      const p = find(project.id);
      if (project.noteRevision !== p.noteRevision)
        throw new Error(
          "The melody changed. Reload the saved notes before continuing.",
        );
      p.notes = clone(notes);
      p.confirmed = confirm;
      p.noteRevision++;
      p.previewUrl = undefined;
      persist();
      return present(p);
    },
    async preview(project, signal) {
      ready();
      await wait(300, signal);
      const p = find(project.id);
      validateNotes(p, p.notes);
      const key = `preview-${p.id}-${p.noteRevision}`;
      await blobStore(key, renderNotePreview(p.notes, p.tracks[0].duration));
      check(signal);
      p.previewUrl = `stored:${key}`;
      persist();
      return present(p);
    },
    async propose(project, text, basisId, manual, signal) {
      ready();
      check(signal);
      const p = find(project.id);
      if (!p.confirmed)
        throw new Error("Confirm your melody before reviewing an arrangement.");
      await service.saveDraft(project, text, basisId);
      const basis = p.tracks.find((t) => t.id === basisId)!;
      const recipe = { ...basis.recipe, ...manual };
      if (!manual) {
        const words = text.toLowerCase();
        if (
          !words.trim() ||
          scenario === "unclear" ||
          !/piano|guitar|bass|drums|backing|sparse|steady|soft|quiet|normal/.test(
            words,
          )
        )
          throw new Error(
            "I couldn't interpret that change. Edit your request or adjust the arrangement directly.",
          );
        if (/electric piano/.test(words)) recipe.instrument = "electric_piano";
        else if (/piano/.test(words)) recipe.instrument = "piano";
        if (/guitar/.test(words)) recipe.instrument = "guitar";
        if (/(remove|no|without|off).*drums/.test(words)) recipe.drums = false;
        else if (/(add|on|with).*drums/.test(words)) recipe.drums = true;
        if (/(remove|no|without|off).*bass/.test(words)) recipe.bass = false;
        else if (/(add|on|with).*bass/.test(words)) recipe.bass = true;
        if (/quiet|soft/.test(words)) recipe.backingVolume = "soft";
        if (/normal|louder/.test(words)) recipe.backingVolume = "normal";
        if (/sparse/.test(words)) recipe.density = "sparse";
        if (/steady/.test(words)) recipe.density = "steady";
      }
      await wait(450, signal);
      check(signal);
      const same = JSON.stringify(recipe) === JSON.stringify(basis.recipe);
      const proposal: Proposal = {
        id: crypto.randomUUID(),
        projectId: p.id,
        basisId,
        noteRevision: p.noteRevision,
        text,
        recipe,
        before: clone(basis.recipe),
        canGenerate: !same || basis.kind === "original",
        warnings:
          same && basis.kind !== "original"
            ? [
                "These settings match the selected version. Change a setting before creating another version.",
              ]
            : [],
      };
      snapshots.set(proposal.id, {
        proposal: clone(proposal),
        sourceRevision: p.sourceRevision,
        draftRevision: p.draftRevision,
      });
      return proposal;
    },
    async create(project, proposal, signal) {
      ready();
      const p = find(project.id);
      const snapshot = snapshots.get(proposal.id);
      if (completed.has(proposal.id)) return present(p);
      if (creating.has(p.id))
        throw new Error("A version is already being created for this project.");
      if (
        scenario === "stale" ||
        !snapshot ||
        JSON.stringify(snapshot.proposal) !== JSON.stringify(proposal) ||
        snapshot.sourceRevision !== p.sourceRevision ||
        snapshot.draftRevision !== p.draftRevision ||
        p.noteRevision !== proposal.noteRevision ||
        p.basisId !== proposal.basisId ||
        p.draft !== proposal.text
      )
        throw new Error(
          "This review is out of date. Review your changes again before creating.",
        );
      if (!proposal.canGenerate)
        throw new Error(
          "No musical settings have changed. Adjust your arrangement first.",
        );
      creating.add(p.id);
      try {
        await wait(1800, signal);
        check(signal);
        if (
          p.noteRevision !== proposal.noteRevision ||
          p.draftRevision !== snapshot.draftRevision ||
          p.basisId !== proposal.basisId
        )
          throw new Error(
            "The melody or request changed during creation. Review the current changes again.",
          );
        if (scenario === "creation-failure")
          throw new Error(
            "The simulated creation failed. Your request and previous versions are safe. Try again or edit your request.",
          );
        const n = p.tracks.filter((t) => t.kind === "version").length + 1;
        const track: Track = {
          id: crypto.randomUUID(),
          projectId: p.id,
          projectTitle: p.title,
          title: `Version ${n}`,
          description: describeRecipe(proposal.recipe),
          url: proposal.recipe.drums ? "/audio/pulse.wav" : "/audio/warm.wav",
          duration: 12,
          kept: false,
          kind: "version",
          recipe: clone(proposal.recipe),
        };
        p.tracks.push(track);
        p.updatedAt = new Date().toISOString();
        completed.set(proposal.id, track.id);
        persist();
        return present(p);
      } finally {
        creating.delete(p.id);
      }
    },
    reset() {
      const ids = new Set(
        [...projects, ...initialProjects()].map((project) => project.id),
      );
      // Empty-state scenarios temporarily hide the persisted prototype Library.
      // Include those project IDs so their unsent drafts are reset as well.
      try {
        const saved: unknown = JSON.parse(
          localStorage.getItem(STORAGE) || "null",
        );
        if (Array.isArray(saved))
          for (const project of saved) {
            if (project && typeof project.id === "string") ids.add(project.id);
          }
      } catch {
        /* A damaged prototype index should not block a clean reset. */
      }
      for (const id of ids)
        localStorage.removeItem(`djound.studio.draft.${id}`);
      const notePrefixes = [...ids].map(
        (id) => `djound.melody-edits.v1.prototype.${id}.`,
      );
      for (let index = localStorage.length - 1; index >= 0; index--) {
        const key = localStorage.key(index);
        if (key && notePrefixes.some((prefix) => key.startsWith(prefix)))
          localStorage.removeItem(key);
      }
      localStorage.setItem("djound.last.prototype", "evening");
      for (const url of urls.values()) URL.revokeObjectURL(url);
      urls.clear();
      projects = initialProjects();
      snapshots.clear();
      completed.clear();
      persist();
    },
  };
  return service;
}

type LocalState = components["schemas"]["LocalState"];
type LocalPlan = components["schemas"]["LocalPlan"];
type CreateBody = components["schemas"]["CreateRequestCommand"];
type PendingCreation = { key: string; explorationId: string; body: CreateBody };
/** Inspect the durable command without submitting or invoking a model. */
export async function pendingBackendCreation(): Promise<{
  projectId: string;
  text: string;
  basisId: string | null;
} | null> {
  const command = await pendingCommand<PendingCreation>("studio-creation");
  return command
    ? {
        projectId: command.explorationId,
        text: command.body.text,
        basisId: command.body.basisRecordingId,
      }
    : null;
}
/** Only invoke from a deliberate recovery action; replays the exact original key/body. */
export async function recoverBackendCreation(
  projectId: string,
  signal?: AbortSignal,
): Promise<Project> {
  check(signal);
  const command = await pendingCommand<PendingCreation>("studio-creation");
  if (!command || command.explorationId !== projectId)
    throw new Error(
      "No pending creation belongs to this project. Reload its latest state.",
    );
  return transmitCreation(command, signal);
}
async function transmitCreation(
  command: PendingCreation,
  signal?: AbortSignal,
): Promise<Project> {
  check(signal);
  const { data, error, response } = await api.POST(
    "/api/explorations/{id}/requests",
    {
      params: {
        path: { id: command.explorationId },
        header: { "Idempotency-Key": command.key },
      },
      body: command.body,
    },
  );
  if (!data) {
    if (isDefiniteRejection(response.status, error?.code))
      await pendingCommand("studio-creation", null, command.key);
    throw failure(
      error,
      "Creation response was interrupted. Retry to recover this exact request.",
    );
  }
  try {
    for (let i = 0; i < 600; i++) {
      check(signal);
      const result = await api.GET("/api/attempts/{id}", {
        params: { path: { id: data.attemptId } },
      });
      if (!result.data)
        throw failure(
          result.error,
          "Could not refresh creation status. Retry to recover the same request.",
        );
      if (result.data.state === "SUCCEEDED" || result.data.state === "FAILED") {
        await pendingCommand("studio-creation", null, command.key);
        if (result.data.state === "FAILED")
          throw new Error(
            result.data.outcome?.detail ||
              "Creation failed. Your previous recordings are safe.",
          );
        return backendService().get(command.explorationId);
      }
      await wait(1000, signal);
    }
    throw new Error(
      "Creation is still running. Reload its status before creating again.",
    );
  } catch (cause) {
    if (signal?.aborted) {
      const cancel = await api.POST("/api/attempts/{id}/cancel", {
        params: { path: { id: data.attemptId } },
      });
      if (!cancel.data)
        throw failure(
          cancel.error,
          "Cancellation could not be confirmed. Reload creation status.",
        );
      await pendingCommand("studio-creation", null, command.key);
      if (cancel.data.state === "SUCCEEDED")
        return backendService().get(command.explorationId);
    }
    throw cause;
  }
}
function failure(problem: { detail?: string } | undefined, fallback: string) {
  return new Error(problem?.detail || fallback);
}
function trackFromRecording(r: Recording): Track {
  return {
    id: r.id,
    projectId: r.explorationId,
    title: r.kind === "ORIGINAL" ? "Original" : r.title,
    description:
      r.kind === "ORIGINAL"
        ? "Uploaded original"
        : r.direction || "Saved arrangement",
    url: r.mediaUrl,
    duration: r.durationMs / 1000,
    kept: r.kept,
    kind: r.kind === "ORIGINAL" ? "original" : "version",
    recipe: r.localRecipe ?? { ...LOCAL_SOURCE_RECIPE },
    noteRevision: r.localNoteRevision ?? undefined,
    recipeKnown: r.kind === "ORIGINAL" || r.localRecipe != null,
  };
}
function backendProject(e: Exploration, local?: LocalState): Project {
  const tracks = [e.original, ...e.renditions].map((r) => ({
    ...trackFromRecording(r),
    projectTitle: e.title,
  }));
  return {
    id: e.id,
    title: e.title,
    fileName: e.original.fileName,
    updatedAt: e.updatedAt,
    tracks,
    notes: local?.notes?.notes ?? [],
    confirmed: local?.notes?.status === "CONFIRMED",
    noteRevision: local?.notes?.revision ?? 0,
    sourceRevision: e.sourceRevision,
    draftRevision: e.draft.revision,
    draft: e.draft.text,
    basisId: e.draft.basisRecordingId ?? e.original.id,
    previewUrl: local?.notes?.previewUrl ?? undefined,
    available: local?.available ?? false,
    freeformAvailable: local?.freeformAvailable ?? local?.available ?? false,
    freeformUnavailableReason: local?.freeformUnavailableReason ?? undefined,
    processing: Boolean(
      local?.activeJob ||
      (e.latestAttempt &&
        !["SUCCEEDED", "FAILED"].includes(e.latestAttempt.state)),
    ),
    unavailableReason:
      local?.unavailableReason ??
      "Connect to the local engine to detect notes and create arrangements.",
  };
}
function backendService(): StudioService {
  const proposals = new Map<string, LocalPlan>();
  async function local(id: string) {
    const { data, error } = await api.GET("/api/explorations/{id}/local", {
      params: { path: { id } },
    });
    if (!data) throw failure(error, "Could not load the local engine status.");
    return data;
  }
  async function get(id: string) {
    const [e, l] = await Promise.all([
      getExploration(id),
      local(id).catch(() => undefined),
    ]);
    return backendProject(e, l);
  }
  async function loadJob(jobId: string) {
    const { data, error } = await api.GET("/api/local/jobs/{id}", {
      params: { path: { id: jobId } },
    });
    if (!data) throw failure(error, "Could not read the local job.");
    return data;
  }
  async function pollJob(jobId: string, signal?: AbortSignal) {
    try {
      for (let i = 0; i < 300; i++) {
        check(signal);
        const state = await loadJob(jobId);
        const job = state.job;
        if (job.state === "SUCCEEDED") return state;
        if (job.state === "FAILED")
          throw new Error(
            job.detail ||
              "The local task did not complete. Your existing recordings are safe.",
          );
        await wait(1000, signal);
      }
      throw new Error(
        "The local task is still running. Reload its status before submitting another request.",
      );
    } catch (error) {
      if (signal?.aborted) {
        const result = await api.POST("/api/local/jobs/{id}/cancel", {
          params: { path: { id: jobId } },
        });
        if (!result.data)
          throw failure(
            result.error,
            "Cancellation could not be confirmed. Reload the local task status.",
          );
        if (result.data.state === "SUCCEEDED") return loadJob(jobId);
      }
      throw error;
    }
  }
  const service: StudioService = {
    async list() {
      const items = await getLibrary();
      return Promise.all(items.map((item) => get(item.id)));
    },
    get,
    async upload(file) {
      const existing = await pendingUpload();
      if (
        existing &&
        (existing.file.name !== file.name ||
          existing.file.size !== file.size ||
          existing.file.lastModified !== file.lastModified)
      )
        throw new Error(
          "A previous upload needs recovery. Choose that same file to recover it before starting a different upload.",
        );
      const command = existing ?? { key: crypto.randomUUID(), file, text: "" };
      await pendingUpload(command);
      const { data, error, response } = await api.POST("/api/explorations", {
        params: { header: { "Idempotency-Key": command.key } },
        body: {
          file: command.file as unknown as string,
          initialDraft: command.text,
        },
        bodySerializer: () => {
          const form = new FormData();
          form.append("file", command.file);
          form.append("initialDraft", command.text);
          return form;
        },
      });
      if (!data) {
        if (uploadWasRejected(response.status, error?.code, !!existing))
          await pendingUpload(null, command.key);
        throw failure(
          error,
          "Upload response was interrupted. Choose the same file to recover that exact upload.",
        );
      }
      await pendingUpload(null, command.key);
      return get(data.explorationId);
    },
    async sample() {
      const response = await fetch("/audio/original.wav");
      if (!response.ok)
        throw new Error("The bundled sample could not be loaded.");
      return service.upload(
        new File([await response.blob()], "sample-melody.wav", {
          type: "audio/wav",
          lastModified: 0,
        }),
      );
    },
    async keep(_id, trackId, kept) {
      const { data, error } = await api.PATCH("/api/recordings/{id}", {
        params: { path: { id: trackId } },
        body: { kept },
      });
      if (!data) throw failure(error, "Could not save Keep. Try again.");
    },
    async saveDraft(project, text, basisId) {
      if (text === project.draft && basisId === project.basisId) return project;
      const { data, error } = await api.PUT("/api/explorations/{id}/draft", {
        params: { path: { id: project.id } },
        body: {
          text,
          basisRecordingId: basisId,
          expectedDraftRevision: project.draftRevision,
        },
      });
      if (!data)
        throw failure(
          error,
          "Your draft changed elsewhere. Reload before saving; your unsent words are still in this form.",
        );
      return {
        ...project,
        draft: data.text,
        basisId: data.basisRecordingId ?? project.tracks[0].id,
        draftRevision: data.revision,
      };
    },
    async detect(project, signal) {
      check(signal);
      const { data, error } = await api.POST(
        "/api/explorations/{id}/local/transcriptions",
        {
          params: { path: { id: project.id } },
          body: { expectedSourceRevision: project.sourceRevision },
        },
      );
      if (!data) throw failure(error, "Could not start melody detection.");
      await pollJob(data.id, signal);
      return get(project.id);
    },
    async saveNotes(project, notes, confirm) {
      validateNotes(project, notes);
      const { data, error } = await api.PUT(
        "/api/explorations/{id}/local/notes",
        {
          params: { path: { id: project.id } },
          body: {
            expectedSourceRevision: project.sourceRevision,
            expectedNoteRevision: project.noteRevision,
            notes,
            confirm,
          },
        },
      );
      if (!data) throw failure(error, "Could not save your melody notes.");
      return get(project.id);
    },
    async preview(project, signal) {
      check(signal);
      const { data, error } = await api.POST(
        "/api/explorations/{id}/local/previews",
        {
          params: { path: { id: project.id } },
          body: {
            expectedSourceRevision: project.sourceRevision,
            expectedNoteRevision: project.noteRevision,
          },
        },
      );
      if (!data) throw failure(error, "Could not prepare the melody preview.");
      await pollJob(data.id, signal);
      return get(project.id);
    },
    async propose(project, text, basisId, recipe, signal, reviewedPlanId) {
      check(signal);
      const p = await service.saveDraft(project, text, basisId);
      const body = {
        expectedSourceRevision: p.sourceRevision,
        expectedNoteRevision: p.noteRevision,
        expectedDraftRevision: p.draftRevision,
        text,
        basisRecordingId: basisId,
      };
      let plan: LocalPlan | null;
      if (recipe) {
        if (!text.trim())
          throw new Error(
            "Describe these arrangement changes before reviewing them.",
          );
        let parentId = reviewedPlanId;
        if (!parentId) {
          const initial = await api.POST(
            "/api/explorations/{id}/local/manual-plans",
            {
              params: { path: { id: p.id } },
              body,
            },
          );
          if (!initial.data)
            throw failure(
              initial.error,
              initial.response.status === 404
                ? "This backend does not support direct arrangement reviews yet. Restart the latest backend build, or use the clickable prototype."
                : "Could not prepare direct arrangement controls. Your choices are preserved.",
            );
          parentId = initial.data.id;
        }
        check(signal);
        const { data, error } = await api.POST(
          "/api/local/plans/{id}/corrections",
          {
            params: { path: { id: parentId } },
            body: { ...body, recipe },
          },
        );
        if (!data)
          throw failure(error, "Could not review your arrangement controls.");
        plan = data;
      } else {
        const { data, error } = await api.POST(
          "/api/explorations/{id}/local/plans",
          { params: { path: { id: p.id } }, body },
        );
        if (!data)
          throw failure(
            error,
            "Could not interpret that change. Your request is preserved.",
          );
        plan = (await pollJob(data.id, signal)).plan;
      }
      if (!plan)
        throw new Error(
          "No reviewed proposal was returned. Your request is preserved.",
        );
      proposals.set(plan.id, plan);
      return {
        id: plan.id,
        projectId: p.id,
        basisId: plan.basisRecordingId,
        noteRevision: plan.noteRevision,
        text: plan.text,
        recipe: plan.recipe,
        before: plan.basisRecipe,
        canGenerate: plan.canGenerate,
        warnings: plan.warnings,
      };
    },
    async create(project, proposal, signal) {
      check(signal);
      const previous = await pendingCommand<PendingCreation>("studio-creation");
      if (
        previous &&
        (previous.explorationId !== project.id ||
          previous.body.localPlanId !== proposal.id)
      )
        throw new Error(
          "A previous creation needs recovery. Return to that project's reviewed request before creating another version.",
        );
      const plan = proposals.get(proposal.id);
      if (!plan && !previous)
        throw new Error("Review your changes again before creating a version.");
      if (
        plan &&
        (JSON.stringify(plan.recipe) !== JSON.stringify(proposal.recipe) ||
          plan.text !== proposal.text ||
          plan.basisRecordingId !== proposal.basisId ||
          plan.noteRevision !== proposal.noteRevision)
      )
        throw new Error(
          "The reviewed settings changed. Review again before creating.",
        );
      const command: PendingCreation = previous ?? {
        key: crypto.randomUUID(),
        explorationId: project.id,
        body: {
          localPlanId: proposal.id,
          text: plan!.text,
          basisRecordingId: plan!.basisRecordingId,
          expectedSourceRevision: plan!.sourceRevision,
          expectedDraftRevision: plan!.draftRevision,
        },
      };
      await pendingCommand("studio-creation", command);
      return transmitCreation(command, signal);
    },
  };
  return service;
}
export function createStudioService(
  mode: Mode,
  scenario: Scenario,
): StudioService {
  return mode === "prototype" ? prototypeService(scenario) : backendService();
}

import { requestRendition } from "./live-api.js";

export async function listProjects() {
  const rows = await requestRendition();
  return rows.map((project) => ({
    id: project.id,
    title: project.title,
    updatedAt: project.lastActivityAt,
    createdAt: project.createdAt,
    editCount: project.readyTakeCount,
    originalAudioUrl: `/api/renditions/${encodeURIComponent(project.id)}/takes/original/audio`,
  }));
}

// A retry after a dropped response should reopen the import that already landed.
// Clear the key on success so a deliberate second import creates a new project.
const pending = new Map();
export async function importRecording(file) {
  const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  const fingerprint = Array.from(new Uint8Array(hash), (n) =>
    n.toString(16).padStart(2, "0"),
  ).join("");
  const slot = "djound:import:" + fingerprint + ":" + file.name;
  let key = pending.get(slot);
  try {
    key ??= sessionStorage.getItem(slot);
  } catch {}
  key ||= crypto.randomUUID();
  pending.set(slot, key);
  try {
    sessionStorage.setItem(slot, key);
  } catch {}
  const body = new FormData();
  body.append("file", file);
  let clearKey = false;
  try {
    const project = await requestRendition("", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body,
    });
    clearKey = true;
    return project;
  } catch (err) {
    clearKey =
      err.status >= 400 && err.status < 500 && ![408, 429].includes(err.status);
    throw err;
  } finally {
    if (clearKey) {
      pending.delete(slot);
      try {
        sessionStorage.removeItem(slot);
      } catch {}
    }
  }
}

// Versioned local recovery is never authoritative server state.
export function readLocal<T>(key: string, fallback: T): T {
  try {
    const value = JSON.parse(
      localStorage.getItem("djound.v1." + key) || "null",
    );
    return value ?? fallback;
  } catch {
    return fallback;
  }
}
export function writeLocal(key: string, value: unknown) {
  try {
    localStorage.setItem("djound.v1." + key, JSON.stringify(value));
  } catch {
    /* Playback remains usable if local storage is full. */
  }
}
export type PendingUpload = { key: string; file: File; text: string };
function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open("djound-upload-v1", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("pending");
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
export async function pendingUpload(
  value?: PendingUpload | null,
): Promise<PendingUpload | undefined> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(
      "pending",
      value === undefined ? "readonly" : "readwrite",
    );
    const store = tx.objectStore("pending");
    const req =
      value === undefined
        ? store.get("new")
        : value === null
          ? store.delete("new")
          : store.put(value, "new");
    tx.oncomplete = () => {
      db.close();
      resolve(value === undefined ? req.result : undefined);
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error);
    };
  });
}

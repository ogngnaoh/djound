function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open("djound-upload-v1", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("pending");
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
export class PendingCommandConflict extends Error {
  command: unknown;
  constructor(command: unknown) {
    super(
      "Another tab has a pending edit. Recover it before starting another.",
    );
    this.name = "PendingCommandConflict";
    this.command = command;
  }
}
// Read/claim/clear all run in a single IndexedDB transaction. Readwrite
// transactions on this store are serialized even across separate browser tabs.
export async function pendingCommand<T extends { key: string }>(
  slot: string,
  value?: T | null,
  expectedKey?: string,
): Promise<T | undefined> {
  if (value === null && !expectedKey)
    throw new Error("Clearing a command requires its owner key.");
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(
      "pending",
      value === undefined ? "readonly" : "readwrite",
    );
    const store = tx.objectStore("pending");
    const read = store.get(slot);
    let result: T | undefined;
    let conflict: PendingCommandConflict | undefined;
    read.onsuccess = () => {
      const existing = read.result as T | undefined;
      if (value === undefined) {
        result = existing;
        return;
      }
      if (value === null) {
        if (existing?.key === expectedKey) store.delete(slot);
        return;
      }
      if (existing) {
        if (existing.key !== value.key)
          conflict = new PendingCommandConflict(existing);
        // An identical key retains its originally persisted body byte-for-byte.
        return;
      }
      store.put(value, slot);
    };
    tx.oncomplete = () => {
      db.close();
      if (conflict) reject(conflict);
      else resolve(result);
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error);
    };
    tx.onabort = () => {
      db.close();
      reject(tx.error || new Error("Edit recovery transaction aborted."));
    };
  });
}

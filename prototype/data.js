// Only this prototype's IndexedDB is touched. The local engine's projects live separately.
const DEMO = "zanarkand";
const assets = {original: "original", jazz: "edit"};
const databaseName = "djound-listening-room-preview";
const snapshots = new WeakMap();
const objectUrls = new Map();
let connection;

function originalEdit(audioKey, createdAt) {
  return {id: "original", name: "Original", parentId: null, prompt: "", favorite: false, createdAt, audioKey};
}

function openDatabase() {
  if (connection) return connection;
  connection = new Promise((resolve, reject) => {
    const opening = indexedDB.open(databaseName, 1);
    opening.onupgradeneeded = () => {
      const db = opening.result;
      db.createObjectStore("audio");
      const projects = db.createObjectStore("projects", {keyPath: "id"});
      const createdAt = new Date().toISOString();
      projects.add({
        id: DEMO, title: "To Zanarkand", createdAt, updatedAt: createdAt,
        edits: [originalEdit("original", createdAt), {
          id: "jazz", name: "Jazz trio", parentId: "original", favorite: false,
          prompt: "Play it as a jazz trio, with walking bass and brushed drums.",
          audioKey: "jazz", createdAt
        }]
      });
    };
    opening.onsuccess = () => {
      const db = opening.result;
      db.onversionchange = () => { db.close(); connection = undefined; };
      resolve(db);
    };
    opening.onerror = () => { connection = undefined; reject(opening.error); };
  });
  return connection;
}

async function read(store, key) {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const source = db.transaction(store).objectStore(store);
    const request = key === undefined ? source.getAll() : source.get(key);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function write(stores, perform) {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(stores, "readwrite");
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error || new Error("The browser could not save this change."));
    perform(tx);
  });
}

export async function listProjects() {
  const projects = await read("projects");
  return projects.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

export async function getProject(id = DEMO) {
  const project = await read("projects", id);
  if (project) snapshots.set(project, structuredClone(project));
  return project;
}

// Merge only locally edited fields so opening a second tab preserves both sets of edits.
export async function saveProject(project) {
  const before = snapshots.get(project);
  let saved;
  await write(["projects"], tx => {
    const store = tx.objectStore("projects");
    const lookup = store.get(project.id);
    lookup.onsuccess = () => {
      const current = lookup.result;
      saved = structuredClone(project);
      if (current && before) {
        const edits = new Map(current.edits.map(edit => [edit.id, edit]));
        const localIds = new Set(project.edits.map(edit => edit.id));
        for (const edit of before.edits) if (!localIds.has(edit.id)) edits.delete(edit.id);
        for (const edit of project.edits) {
          const old = before.edits.find(item => item.id === edit.id);
          if (!old) edits.set(edit.id, edit);
          else if (edits.has(edit.id)) {
            for (const field of Object.keys(edit)) {
              if (edit[field] !== old[field]) edits.get(edit.id)[field] = edit[field];
            }
          }
        }
        saved.edits = [...edits.values()];
        if (project.title === before.title) saved.title = current.title;
      }
      saved.updatedAt = new Date().toISOString();
      store.put(saved);
    };
  });
  // Keep the caller's baseline and view consistent with concurrent commits.
  Object.assign(project, saved);
  snapshots.set(project, structuredClone(saved));
  return project;
}

export async function audioUrl(key) {
  if (Object.hasOwn(assets, key)) return new URL("./media/" + assets[key] + ".wav", import.meta.url).href;
  if (objectUrls.has(key)) return objectUrls.get(key);
  const audio = await read("audio", key);
  if (!audio) throw new Error("The audio for this edit is unavailable in this browser.");
  const url = URL.createObjectURL(audio);
  objectUrls.set(key, url);
  return url;
}

async function decode(blob) {
  const decoding = new OfflineAudioContext(2, 1, 44100);
  return decoding.decodeAudioData(await blob.arrayBuffer());
}

function asWav(buffer) {
  const channels = Math.min(2, buffer.numberOfChannels);
  const samples = buffer.length * channels;
  const data = new ArrayBuffer(44 + samples * 2);
  const view = new DataView(data);
  const string = (offset, value) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };
  string(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true);
  string(8, "WAVE"); string(12, "fmt "); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, channels, true);
  view.setUint32(24, buffer.sampleRate, true); view.setUint32(28, buffer.sampleRate * channels * 2, true);
  view.setUint16(32, channels * 2, true); view.setUint16(34, 16, true);
  string(36, "data"); view.setUint32(40, samples * 2, true);
  const tracks = Array.from({length: channels}, (_, i) => buffer.getChannelData(i));
  let offset = 44;
  for (let i = 0; i < buffer.length; i++) {
    for (const track of tracks) {
      const sample = Math.max(-1, Math.min(1, track[i]));
      view.setInt16(offset, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
      offset += 2;
    }
  }
  return new Blob([data], {type: "audio/wav"});
}

export async function importRecording(file) {
  if (!file?.size) throw new Error("Choose an audio file that contains a recording.");
  let buffer;
  try { buffer = await decode(file); }
  catch { throw new Error("This file could not be played. Try a WAV, MP3, or M4A recording."); }
  const id = crypto.randomUUID(), key = "audio-" + id;
  const createdAt = new Date().toISOString();
  const project = {id, title: file.name.replace(/\.[^.]+$/, "") || file.name, createdAt,
    updatedAt: createdAt, edits: [originalEdit(key, createdAt)]};
  const wav = asWav(buffer);
  await write(["projects", "audio"], tx => {
    tx.objectStore("audio").add(wav, key);
    tx.objectStore("projects").add(project);
  });
  return project;
}

// The preview performs these two audible treatments on the selected parent in the browser.
export async function renderPreview(parentAudioKey, effect) {
  if (effect !== "focus" && effect !== "warm") throw new Error("Choose a prepared preview treatment.");
  const response = await fetch(await audioUrl(parentAudioKey));
  if (!response.ok) throw new Error("The selected recording could not be loaded.");
  const buffer = await decode(await response.blob());
  const ctx = new OfflineAudioContext(buffer.numberOfChannels, buffer.length, buffer.sampleRate);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  const tone = ctx.createBiquadFilter();
  tone.type = effect === "focus" ? "lowpass" : "lowshelf";
  tone.frequency.value = effect === "focus" ? 2100 : 360;
  tone.Q.value = 0.65;
  tone.gain.value = 5;
  const treble = ctx.createBiquadFilter();
  treble.type = "highshelf"; treble.frequency.value = 2400; treble.gain.value = -4;
  const level = ctx.createGain();
  level.gain.value = effect === "focus" ? 0.74 : 0.84;
  source.connect(tone).connect(treble).connect(level).connect(ctx.destination);
  source.start();
  const rendered = asWav(await ctx.startRendering());
  const key = "audio-" + crypto.randomUUID();
  await write(["audio"], tx => tx.objectStore("audio").add(rendered, key));
  return key;
}

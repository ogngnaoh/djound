export type MelodyNote = {
  start: number;
  end: number;
  pitch: number;
  velocity: number;
};

export function noteIssue(notes: MelodyNote[], duration: number) {
  if (!notes.length) return "Add at least one melody note.";
  if (notes.length > 256)
    return "Keep this short melody to 256 notes or fewer.";
  for (const [index, note] of notes.entries()) {
    if (
      ![note.start, note.end, note.pitch, note.velocity].every(Number.isFinite)
    )
      return `Note ${index + 1} needs a pitch and start/end time.`;
    if (note.start < 0 || note.end <= note.start || note.end > duration)
      return `Note ${index + 1} must start before it ends, within this ${duration.toFixed(2)}-second clip.`;
    if (Math.round(note.start * 44100) >= Math.round(note.end * 44100))
      return `Note ${index + 1} is too short to render. Increase its duration.`;
    if (!Number.isInteger(note.pitch) || note.pitch < 0 || note.pitch > 127)
      return `Note ${index + 1} needs a whole-number MIDI pitch from 0 to 127.`;
    if (
      !Number.isInteger(note.velocity) ||
      note.velocity < 1 ||
      note.velocity > 127
    )
      return `Note ${index + 1} needs a strength from 1 to 127.`;
  }
  const sorted = [...notes].sort((a, b) => a.start - b.start);
  const ends = new Map<number, number>();
  for (const note of sorted) {
    if (Math.round(note.start * 44100) < (ends.get(note.pitch) ?? -1))
      return "Two notes of the same pitch overlap. Adjust their timing or remove the extra note before previewing.";
    ends.set(note.pitch, Math.round(note.end * 44100));
  }
  return "";
}

export function pitchName(pitch: number) {
  if (!Number.isInteger(pitch) || pitch < 0 || pitch > 127)
    return "Invalid pitch";
  return `${["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"][pitch % 12]}${Math.floor(pitch / 12) - 1}`;
}

export function planMatches(
  plan:
    | {
        sourceRevision: number;
        noteRevision: number;
        draftRevision: number;
        text: string;
        basisRecordingId: string | null;
      }
    | null
    | undefined,
  current: {
    sourceRevision: number;
    noteRevision: number;
    draftRevision: number;
    text: string;
    basisRecordingId: string | null;
  },
) {
  return (
    !!plan &&
    plan.sourceRevision === current.sourceRevision &&
    plan.noteRevision === current.noteRevision &&
    plan.draftRevision === current.draftRevision &&
    plan.text === current.text &&
    plan.basisRecordingId === current.basisRecordingId
  );
}

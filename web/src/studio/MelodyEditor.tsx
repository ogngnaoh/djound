import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { createPortal } from "react-dom";
import { noteIssue, pitchName } from "../remix/localPolicy";
import type { Note } from "./types";
import "./melody.css";

const STEP = 0.05;
const MIN_LENGTH = 0.05;
const copy = (notes: Note[]) => notes.map((note) => ({ ...note }));
const clamp = (n: number, min: number, max: number) =>
  Math.max(min, Math.min(max, n));
const round = (n: number) => Math.round(n * 1000) / 1000;

type Drag = {
  index: number;
  kind: "move" | "resize";
  x: number;
  y: number;
  width: number;
  rowHeight: number;
  original: Note[];
  next: Note[];
};

export function MelodyEditor({
  notes,
  duration,
  onChange,
  detailsTargetId,
}: {
  notes: Note[];
  duration: number;
  onChange: (notes: Note[]) => void;
  detailsTargetId?: string;
}) {
  const [detailsTarget, setDetailsTarget] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setDetailsTarget(
      detailsTargetId ? document.getElementById(detailsTargetId) : null,
    );
  }, [detailsTargetId]);
  const [selected, setSelected] = useState<number | null>(
    notes.length ? 0 : null,
  );
  const [undo, setUndo] = useState<Note[][]>([]);
  const [redo, setRedo] = useState<Note[][]>([]);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<Note[] | null>(null);
  const drag = useRef<Drag | null>(null);
  const grid = useRef<HTMLDivElement>(null);
  const shown = preview ?? notes;
  const active = selected === null ? undefined : notes[selected];
  const low = Math.max(0, Math.min(64, ...notes.map((n) => n.pitch)));
  const high = Math.min(127, Math.max(low + 12, ...notes.map((n) => n.pitch)));
  const pitches = Array.from({ length: high - low + 1 }, (_, i) => high - i);
  const safeDuration =
    Number.isFinite(duration) && duration > 0 ? duration : 12;
  const stepCount = Math.ceil(safeDuration / 2);

  function commit(next: Note[], allowEmpty = false) {
    const issue =
      allowEmpty && next.length === 0 ? "" : noteIssue(next, safeDuration);
    if (issue) {
      setError(issue);
      return false;
    }
    if (JSON.stringify(notes) === JSON.stringify(next)) return true;
    setUndo((history) => [...history.slice(-49), copy(notes)]);
    setRedo([]);
    setError("");
    onChange(next);
    return true;
  }
  function restore(direction: "undo" | "redo") {
    const source = direction === "undo" ? undo : redo;
    const next = source.at(-1);
    if (!next) return;
    if (direction === "undo") {
      setUndo(source.slice(0, -1));
      setRedo((history) => [...history, copy(notes)]);
    } else {
      setRedo(source.slice(0, -1));
      setUndo((history) => [...history, copy(notes)]);
    }
    setSelected(next.length ? Math.min(selected ?? 0, next.length - 1) : null);
    setError("");
    onChange(copy(next));
  }
  function change(index: number, value: Partial<Note>) {
    const next = notes.map((note, i) =>
      i === index ? { ...note, ...value } : note,
    );
    commit(next);
  }
  function add() {
    if (notes.length >= 256) {
      setError("This melody already has 256 notes.");
      return;
    }
    const pitch = active?.pitch ?? 64;
    const ordered = notes
      .filter((note) => note.pitch === pitch)
      .sort((a, b) => a.start - b.start);
    let start = 0;
    for (const note of ordered) {
      if (note.start - start >= Math.min(0.5, safeDuration)) break;
      start = Math.max(start, note.end);
    }
    const end = Math.min(safeDuration, start + 0.5);
    if (end - start < MIN_LENGTH) {
      setError(
        "No space at this pitch. Choose another pitch or shorten a note.",
      );
      return;
    }
    if (
      commit([
        ...notes,
        { start: round(start), end: round(end), pitch, velocity: 90 },
      ])
    )
      setSelected(notes.length);
  }
  function remove() {
    if (selected === null || !active) return;
    const next = notes.filter((_, i) => i !== selected);
    if (commit(next, true))
      setSelected(next.length ? Math.min(selected, next.length - 1) : null);
  }
  function keyDown(event: KeyboardEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    if (target.matches("input, select, textarea")) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
      event.preventDefault();
      restore(event.shiftKey ? "redo" : "undo");
    } else if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      remove();
    }
  }
  function noteKeys(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const note = notes[index];
    let update: Partial<Note> | undefined;
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      update = {
        pitch: clamp(note.pitch + (event.key === "ArrowUp" ? 1 : -1), 0, 127),
      };
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const delta = event.key === "ArrowRight" ? STEP : -STEP;
      if (event.shiftKey)
        update = {
          end: round(
            clamp(note.end + delta, note.start + MIN_LENGTH, safeDuration),
          ),
        };
      else {
        const length = note.end - note.start;
        const start = round(
          clamp(note.start + delta, 0, safeDuration - length),
        );
        update = { start, end: round(start + length) };
      }
    }
    if (update) {
      event.preventDefault();
      setSelected(index);
      change(index, update);
    }
  }
  function startDrag(
    event: PointerEvent<HTMLButtonElement>,
    index: number,
    kind: Drag["kind"],
  ) {
    if (event.button !== 0 || !grid.current) return;
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    setSelected(index);
    const box = grid.current.getBoundingClientRect();
    drag.current = {
      index,
      kind,
      x: event.clientX,
      y: event.clientY,
      width: box.width,
      rowHeight: box.height / pitches.length,
      original: copy(notes),
      next: copy(notes),
    };
  }
  function moveDrag(event: PointerEvent<HTMLButtonElement>) {
    const current = drag.current;
    if (!current) return;
    const original = current.original[current.index];
    const dx =
      Math.round(
        (((event.clientX - current.x) / current.width) * safeDuration) / STEP,
      ) * STEP;
    const next = copy(current.original);
    if (current.kind === "resize") {
      next[current.index].end = round(
        clamp(original.end + dx, original.start + MIN_LENGTH, safeDuration),
      );
    } else {
      const length = original.end - original.start;
      const start = round(clamp(original.start + dx, 0, safeDuration - length));
      next[current.index] = {
        ...original,
        start,
        end: round(start + length),
        pitch: clamp(
          original.pitch -
            Math.round((event.clientY - current.y) / current.rowHeight),
          0,
          127,
        ),
      };
    }
    current.next = next;
    setPreview(next);
  }
  function finishDrag(event: PointerEvent<HTMLButtonElement>) {
    const current = drag.current;
    drag.current = null;
    setPreview(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    if (current) commit(current.next);
  }
  function cancelDrag() {
    drag.current = null;
    setPreview(null);
  }

  const details =
    active && selected !== null ? (
      <fieldset
        className={`melody-details ${detailsTarget ? "melody-details-rail" : ""}`}
      >
        <legend>
          Selected note <strong>{pitchName(active.pitch)}</strong>
        </legend>
        <label>
          Pitch
          <select
            aria-label="Selected note pitch"
            value={active.pitch}
            onChange={(event) =>
              change(selected, { pitch: Number(event.target.value) })
            }
          >
            {Array.from({ length: 128 }, (_, pitch) => (
              <option key={pitch} value={pitch}>
                {pitchName(pitch)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Start (seconds)
          <input
            aria-label="Selected note start"
            type="number"
            min="0"
            max={round(safeDuration - (active.end - active.start))}
            step="0.05"
            value={round(active.start)}
            onChange={(event) => {
              if (event.target.value === "") return;
              const start = Number(event.target.value);
              if (Number.isFinite(start))
                change(selected, {
                  start,
                  end: round(start + active.end - active.start),
                });
            }}
          />
        </label>
        <label>
          Length (seconds)
          <input
            aria-label="Selected note length"
            type="number"
            min="0.05"
            max={round(safeDuration - active.start)}
            step="0.05"
            value={round(active.end - active.start)}
            onChange={(event) => {
              if (event.target.value === "") return;
              const length = Number(event.target.value);
              if (Number.isFinite(length))
                change(selected, { end: round(active.start + length) });
            }}
          />
        </label>
        <p className="melody-keyboard-hint">
          With a note focused: ↑ ↓ change pitch, ← → move it, Shift + ← →
          changes its length. Delete removes the selected note.
        </p>
      </fieldset>
    ) : null;

  return (
    <div className="melody-editor" onKeyDown={keyDown}>
      <p
        className="melody-instruction melody-editing-help"
        id="melody-instructions"
      >
        Drag a note to move it. Drag its right edge to change its length. Use
        the fields below for precise edits.
      </p>
      <div
        className="melody-scroll"
        tabIndex={0}
        aria-label="Melody piano roll"
        aria-describedby="melody-instructions"
      >
        <div className="melody-roll">
          <div className="melody-pitches" aria-hidden="true">
            {pitches.map((pitch) => (
              <span key={pitch}>{pitchName(pitch)}</span>
            ))}
          </div>
          <div
            className="melody-grid"
            ref={grid}
            style={{
              height: `calc(${pitches.length} * var(--melody-row-height, 23px))`,
            }}
          >
            {pitches.map((pitch, row) => (
              <div
                key={pitch}
                className={`melody-grid-row ${[1, 3, 6, 8, 10].includes(pitch % 12) ? "melody-grid-sharp" : ""}`}
                style={{
                  top: `${(row / pitches.length) * 100}%`,
                  height: `${100 / pitches.length}%`,
                }}
              />
            ))}
            {Array.from({ length: Math.ceil(safeDuration) + 1 }, (_, i) => (
              <span
                key={i}
                className="melody-grid-line"
                style={{ left: `${(i / safeDuration) * 100}%` }}
              />
            ))}
            {shown.map((note, index) => {
              const top = ((high - note.pitch) / pitches.length) * 100;
              return (
                <div
                  key={index}
                  className={`melody-note ${index === selected ? "melody-note-selected" : ""}`}
                  style={{
                    left: `${(note.start / safeDuration) * 100}%`,
                    top: `${top}%`,
                    width: `${((note.end - note.start) / safeDuration) * 100}%`,
                    height: `${100 / pitches.length}%`,
                  }}
                >
                  <button
                    type="button"
                    className="melody-note-move"
                    aria-label={`Note ${index + 1}: ${pitchName(note.pitch)}, ${note.start.toFixed(2)} seconds, length ${(note.end - note.start).toFixed(2)} seconds`}
                    aria-pressed={selected === index}
                    onFocus={() => setSelected(index)}
                    onClick={() => setSelected(index)}
                    onKeyDown={(event) => noteKeys(event, index)}
                    onPointerDown={(event) => startDrag(event, index, "move")}
                    onPointerMove={moveDrag}
                    onPointerUp={finishDrag}
                    onPointerCancel={cancelDrag}
                  />
                  <button
                    type="button"
                    className="melody-note-resize"
                    aria-label={`Resize note ${index + 1}`}
                    tabIndex={-1}
                    onPointerDown={(event) => startDrag(event, index, "resize")}
                    onPointerMove={moveDrag}
                    onPointerUp={finishDrag}
                    onPointerCancel={cancelDrag}
                  />
                </div>
              );
            })}
          </div>
          <div className="melody-timeline" aria-hidden="true">
            {Array.from({ length: stepCount + 1 }, (_, i) => {
              const t = Math.min(safeDuration, i * 2);
              return (
                <span key={i} style={{ left: `${(t / safeDuration) * 100}%` }}>
                  {t}s
                </span>
              );
            })}
          </div>
        </div>
      </div>
      <div className="melody-toolbar" aria-label="Note editing actions">
        <button type="button" onClick={add} disabled={notes.length >= 256}>
          <span aria-hidden="true">＋</span> Add note
        </button>
        <button type="button" onClick={remove} disabled={!active}>
          Delete
        </button>
        <button
          type="button"
          onClick={() => restore("undo")}
          disabled={!undo.length}
        >
          Undo
        </button>
        <button
          type="button"
          onClick={() => restore("redo")}
          disabled={!redo.length}
        >
          Redo
        </button>
        <span className="melody-note-count">{notes.length} notes</span>
      </div>
      {error ? (
        <p className="melody-error" role="alert">
          {error}
        </p>
      ) : null}
      {!notes.length ? (
        <p className="melody-instruction">
          Add a note to begin. At least one note is needed to confirm the
          melody.
        </p>
      ) : null}
      {detailsTarget && active ? (
        <p className="melody-selection-summary">
          <span>Selected note</span>
          {pitchName(active.pitch)} · Starts at {round(active.start)}s · Length{" "}
          {round(active.end - active.start)}s
        </p>
      ) : null}
      {detailsTarget ? createPortal(details, detailsTarget) : details}
    </div>
  );
}

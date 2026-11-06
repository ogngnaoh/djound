import { useId, useRef, useState } from "react";
import { Button } from "../components/ui/button";
import { useAudio } from "./Player";
import { downbeatAt, measureTap } from "./grooveTiming";
import type { Recipe } from "./types";

const names = {
  swing_ride: "Swing ride",
  four_on_the_floor: "Four on the floor",
} as const;

export function grooveLabel(recipe: Recipe) {
  const grid = recipe.groove;
  return grid
    ? `${names[grid.pattern]} · ${recipe.bpm} BPM · first beat at ${grid.downbeatMs} ms`
    : "Original pulse · 100 BPM";
}

export function validGrid(recipe: Recipe, duration: number) {
  if (!recipe.groove) return recipe.bpm === 100;
  return (
    Number.isInteger(recipe.bpm) &&
    recipe.bpm >= 40 &&
    recipe.bpm <= 200 &&
    Number.isInteger(recipe.groove.downbeatMs) &&
    recipe.groove.downbeatMs >= 0 &&
    recipe.groove.downbeatMs + 10 <= duration * 1000 + 1e-7
  );
}

export function DrumGroove({
  projectId,
  recipe,
  duration,
  onChange,
}: {
  projectId: string;
  recipe: Recipe;
  duration: number;
  onChange: (recipe: Recipe) => void;
}) {
  const grid = recipe.groove;
  const audio = useAudio();
  const id = useId();
  const taps = useRef<number[]>([]);
  const [hint, setHint] = useState("");
  const canCapture =
    audio.track?.projectId === projectId &&
    !audio.loading &&
    !audio.error &&
    downbeatAt(audio.position, duration) !== null;

  function resetTaps() {
    taps.current = [];
    setHint("");
  }
  function tap(now: number) {
    const measured = measureTap(taps.current, now);
    taps.current = measured.taps;
    setHint(measured.hint);
    if (measured.bpm !== undefined) onChange({ ...recipe, bpm: measured.bpm });
  }
  function capture() {
    if (!grid) return;
    const downbeatMs = downbeatAt(audio.playhead(projectId), duration);
    if (!canCapture || downbeatMs === null) {
      setHint("Play or pause this project at least 10 ms before its end.");
      return;
    }
    onChange({ ...recipe, groove: { ...grid, downbeatMs } });
    setHint(`First beat set at ${downbeatMs} ms.`);
  }
  return (
    <div className="recipe-fields">
      <label>
        Drum pattern
        <select
          aria-label="Drum pattern"
          value={grid?.pattern ?? "pulse"}
          onChange={(e) => {
            const pattern = e.currentTarget.value;
            resetTaps();
            if (pattern === "swing_ride" || pattern === "four_on_the_floor") {
              onChange({
                ...recipe,
                drums: true,
                groove: { pattern, downbeatMs: grid?.downbeatMs ?? 0 },
              });
            } else {
              const { groove: _previous, ...rest } = recipe;
              onChange({ ...rest, bpm: 100 });
            }
          }}
        >
          <option value="pulse">Original pulse</option>
          {Object.entries(names).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {grid && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor={`${id}-tempo`} className="mr-auto">
              Backing tempo
            </label>
            <input
              id={`${id}-tempo`}
              aria-label="Backing tempo"
              className="!w-16"
              type="number"
              min={40}
              max={200}
              step={1}
              value={Number.isFinite(recipe.bpm) ? recipe.bpm : ""}
              onChange={(e) => {
                resetTaps();
                onChange({ ...recipe, bpm: e.currentTarget.valueAsNumber });
              }}
            />
            <Button
              type="button"
              variant="outline"
              size="xs"
              onClick={(e) => tap(e.timeStamp)}
            >
              Tap tempo
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor={`${id}-downbeat`} className="mr-auto">
              First beat (ms)
            </label>
            <input
              id={`${id}-downbeat`}
              aria-label="First beat (ms)"
              className="!w-16"
              type="number"
              min={0}
              max={Math.floor(duration * 1000 + 1e-7) - 10}
              step={1}
              value={Number.isFinite(grid.downbeatMs) ? grid.downbeatMs : ""}
              onChange={(e) => {
                setHint("");
                onChange({
                  ...recipe,
                  groove: {
                    ...grid,
                    downbeatMs: e.currentTarget.valueAsNumber,
                  },
                });
              }}
            />
            <Button
              type="button"
              variant="outline"
              size="xs"
              disabled={!canCapture}
              onClick={capture}
            >
              Use playhead
            </Button>
          </div>
          <p className="small subtle" role="status">
            {hint ||
              "Tap along to the beat. Play or pause this project at the first beat to use the playhead."}
          </p>
          {!validGrid(recipe, duration) && (
            <p className="notice" role="status">
              Use a whole tempo from 40 to 200 BPM and place the first beat at
              least 10 ms before the end.
            </p>
          )}
        </>
      )}
    </div>
  );
}

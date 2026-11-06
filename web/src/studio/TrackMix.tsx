import { useState } from "react";
import type { MixStrip, Recipe, Stem } from "./types";

const STEMS = ["lead", "bass", "drums"] as const;
const UNITY: MixStrip = { gainDb: 0, pan: 0, muted: false };
const NAMES = { lead: "Lead", bass: "Bass", drums: "Drums" };

function activeStems(recipe: Recipe) {
  return STEMS.filter((stem) => stem === "lead" || recipe[stem]);
}
function isUnity(s: MixStrip) {
  return s.gainDb === 0 && s.pan === 0 && !s.muted;
}

export function normalizeMix(recipe: Recipe): Recipe {
  if (!recipe.mix) return recipe;
  const mix = { ...recipe.mix };
  for (const stem of STEMS) {
    const strip = mix[stem];
    if (!strip || (stem !== "lead" && !recipe[stem]) || isUnity(strip))
      delete mix[stem];
  }
  const result: Recipe = { ...recipe, mix };
  if (!Object.keys(mix).length) delete result.mix;
  return result;
}

export function allTracksMuted(recipe: Recipe) {
  return activeStems(recipe).every((stem) => recipe.mix?.[stem]?.muted);
}

function panText(pan: number) {
  return pan === 0
    ? "Center"
    : `${Number((Math.abs(pan) * 100).toFixed(3))}% ${pan < 0 ? "left" : "right"}`;
}

export function mixChanges(recipe: Recipe, before?: Recipe) {
  return activeStems(recipe).flatMap((stem) => {
    const now = recipe.mix?.[stem] ?? UNITY;
    const was = before?.mix?.[stem] ?? UNITY;
    const changes: { label: string; previous: string; value: string }[] = [];
    if (now.gainDb !== was.gainDb)
      changes.push({
        label: `${NAMES[stem]} gain`,
        previous: `${was.gainDb} dB`,
        value: `${now.gainDb} dB`,
      });
    if (now.pan !== was.pan)
      changes.push({
        label: `${NAMES[stem]} pan`,
        previous: panText(was.pan),
        value: panText(now.pan),
      });
    if (now.muted !== was.muted)
      changes.push({
        label: `${NAMES[stem]} playback`,
        previous: was.muted ? "Muted" : "Audible",
        value: now.muted ? "Muted" : "Audible",
      });
    return changes;
  });
}

export function TrackMix({
  recipe,
  onChange,
}: {
  recipe: Recipe;
  onChange: (next: Recipe) => void;
}) {
  const [selection, setSelection] = useState<Stem>("lead");
  const active = activeStems(recipe);
  const stem = active.includes(selection) ? selection : "lead";
  const strip = recipe.mix?.[stem] ?? UNITY;
  function update(next: MixStrip) {
    onChange(normalizeMix({ ...recipe, mix: { ...recipe.mix, [stem]: next } }));
  }
  return (
    <details className="mix-inspector">
      <summary>Track mix</summary>
      <div className="mix-controls">
        <label>
          Track{" "}
          <select
            aria-label="Mix track"
            value={stem}
            onChange={(e) => setSelection(e.target.value as Stem)}
          >
            {active.map((item) => (
              <option key={item} value={item}>
                {NAMES[item]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Gain <output>{strip.gainDb} dB</output>
          <input
            type="range"
            min="-60"
            max="12"
            step="0.1"
            aria-label="Track gain"
            aria-valuetext={`${strip.gainDb} decibels`}
            value={strip.gainDb}
            onChange={(e) =>
              update({ ...strip, gainDb: e.currentTarget.valueAsNumber })
            }
          />
        </label>
        <label>
          Pan <output>{panText(strip.pan)}</output>
          <input
            type="range"
            min="-100"
            max="100"
            step="1"
            aria-label="Track pan"
            aria-valuetext={panText(strip.pan)}
            value={strip.pan * 100}
            onChange={(e) =>
              update({ ...strip, pan: e.currentTarget.valueAsNumber / 100 })
            }
          />
        </label>
        <label className="mix-mute">
          <input
            type="checkbox"
            checked={strip.muted}
            onChange={(e) => update({ ...strip, muted: e.target.checked })}
          />
          Mute {NAMES[stem].toLowerCase()}
        </label>
        {!isUnity(strip) && (
          <button
            type="button"
            className="text-action"
            onClick={() => update(UNITY)}
          >
            Reset {NAMES[stem].toLowerCase()} mix
          </button>
        )}
      </div>
    </details>
  );
}

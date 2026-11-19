import { useState } from "react";
import { Button } from "../components/ui/button";
import { level, type Take } from "./api";

type Control = Take["lastControl"];

// A clarification remains read-only until the musician chooses a level and requests a preview.
export function LevelChoice({
  basis,
  enabled,
  onConfirm,
}: {
  basis?: Take;
  enabled: boolean;
  onConfirm: (control: Control, gain: number) => void;
}) {
  const [control, setControl] = useState<Control>(
    basis?.lastControl ?? "vocals",
  );
  const current = (instrument: Control) =>
    (instrument === "vocals" ? basis?.vocalsDb : basis?.drumsDb) ?? 0;
  const [db, setDb] = useState(String(current(control)));
  const gain = Number(db);
  const valid =
    db.trim() !== "" &&
    Number.isFinite(gain) &&
    Math.abs(gain) <= 12 &&
    gain !== current(control);
  return (
    <details className="mix-level-choice">
      <summary>Choose an exact level</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (enabled && valid) onConfirm(control, gain);
        }}
      >
        <label>
          Instrument
          <select
            value={control}
            onChange={(e) => {
              const chosen = e.target.value as Control;
              setControl(chosen);
              setDb(String(current(chosen)));
            }}
          >
            <option value="vocals">Vocals</option>
            <option value="drums">Drums</option>
          </select>
        </label>
        <label>
          Target level (dB)
          <input
            type="number"
            value={db}
            min="-12"
            max="12"
            step="0.01"
            onChange={(e) => setDb(e.target.value)}
          />
        </label>
        <span>Current {level(current(control))}</span>
        <Button size="sm" variant="outline" disabled={!enabled || !valid}>
          Preview this level
        </Button>
      </form>
    </details>
  );
}

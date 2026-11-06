import { CHORDS, barStart, changeBar, isChord, removeChart } from "./harmony";
import type { ChordSuggestion, Harmony, Recipe } from "./types";

function timing(chart: Harmony) {
  return `4/4 · ${chart.bpm} BPM · first beat at ${chart.downbeatMs} ms`;
}

export function ChordChart({
  recipe,
  before,
  suggestions,
  onChange,
}: {
  recipe: Recipe;
  before?: Recipe;
  suggestions?: ChordSuggestion[];
  onChange?: (next: Recipe) => void;
}) {
  const chart = recipe.harmony;
  const prior = before?.harmony;
  if (!chart)
    return prior ? (
      <section className="chord-chart">
        <h3>Chord chart removed</h3>
        <p className="small subtle">
          {timing(prior)} · {prior.chords.map((c) => c ?? "Rest").join(" · ")}
        </p>
      </section>
    ) : null;
  const gridChanged =
    prior && (prior.bpm !== chart.bpm || prior.downbeatMs !== chart.downbeatMs);
  return (
    <section className="chord-chart">
      <h3>Chord chart</h3>
      <p className="small subtle">
        {before && !prior && "New chart · "}
        {timing(chart)}
      </p>
      {prior &&
        (gridChanged || prior.chords.length !== chart.chords.length) && (
          <details className="unchanged-settings">
            <summary>Previous chord chart</summary>
            <p className="small subtle">
              {timing(prior)} ·{" "}
              {prior.chords.map((c) => c ?? "Rest").join(" · ")}
            </p>
          </details>
        )}
      <ol className="chord-bars">
        {chart.chords.map((chord, index) => {
          const evidence = suggestions?.[index];
          const old = prior?.chords[index];
          return (
            <li key={index}>
              <div className="chord-bar-heading">
                <span>
                  Bar {index + 1}{" "}
                  <small>{barStart(chart, index).toFixed(2)} s</small>
                </span>
                {onChange ? (
                  <select
                    aria-label={`Chord for bar ${index + 1}`}
                    value={chord ?? ""}
                    onChange={(e) => {
                      const choice = e.currentTarget.value;
                      if (choice === "" || isChord(choice))
                        onChange(
                          changeBar(
                            recipe,
                            index,
                            choice === "" ? null : choice,
                          ),
                        );
                    }}
                  >
                    <option value="">Rest</option>
                    {CHORDS.map((label) => (
                      <option key={label} value={label}>
                        {label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <strong>{chord ?? "Rest"}</strong>
                )}
              </div>
              {old !== undefined && !gridChanged && old !== chord && (
                <p className="small subtle">Previously {old ?? "Rest"}</p>
              )}
              {evidence &&
                (evidence.ambiguous || evidence.alternatives.length > 0) && (
                  <p
                    className="small subtle"
                    data-chord-alternatives={
                      evidence.alternatives.length ? "" : undefined
                    }
                  >
                    {evidence.ambiguous ? "Ambiguous" : "Suggested"} ·{" "}
                    {evidence.chord ?? "Rest"}
                    {evidence.alternatives.length > 0 &&
                      ` · Alternatives ${evidence.alternatives.join(", ")}`}
                  </p>
                )}
            </li>
          );
        })}
      </ol>
      {chart.chords.every((chord) => chord === null) && (
        <p className="small subtle">
          {recipe.bass
            ? "The chords and bass rest in every bar."
            : "The chords rest in every bar."}
        </p>
      )}
      {onChange && (
        <button
          type="button"
          className="text-action"
          onClick={() => onChange(removeChart(recipe))}
        >
          Remove chord chart
        </button>
      )}
    </section>
  );
}

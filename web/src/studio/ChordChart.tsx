import {
  CHORDS,
  barStart,
  changeBar,
  chartBassNotice,
  isChord,
  removeChart,
} from "./harmony";
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
  const bassNotice = chartBassNotice(recipe, before);
  if (!chart)
    return prior ? (
      <section className="chord-chart">
        <h3>Chord chart removed</h3>
        <p className="small subtle">
          {timing(prior)} · {prior.chords.map((c) => c ?? "Rest").join(" · ")}
        </p>
        {bassNotice && <p className="small subtle">{bassNotice}</p>}
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
      {onChange ? (
        <div className="recipe-fields">
          <label>
            Chart accompaniment
            <select
              value={recipe.accompanimentStyle ?? "standard"}
              onChange={(event) => {
                const { accompanimentStyle, ...rest } = recipe;
                onChange(
                  event.currentTarget.value === "swing_combo"
                    ? { ...rest, accompanimentStyle: "swing_combo" }
                    : rest,
                );
              }}
            >
              <option value="standard">Standard</option>
              <option value="swing_combo">Swing combo</option>
            </select>
          </label>
        </div>
      ) : (
        (recipe.accompanimentStyle || before?.accompanimentStyle) && (
          <p className="small subtle">
            {recipe.accompanimentStyle
              ? "Swing combo accompaniment"
              : "Standard accompaniment"}
          </p>
        )
      )}
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
          The chords rest in every bar. {bassNotice}
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

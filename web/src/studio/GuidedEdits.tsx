const GROUPS = [
  {
    label: "Lead",
    edits: [
      "use piano",
      "use electric piano",
      "use guitar",
      "use the uploaded recording",
    ],
  },
  {
    label: "Backing parts",
    edits: ["add bass", "remove bass", "add drums", "remove drums"],
  },
  {
    label: "Chord chart",
    edits: ["suggest chords", "suggest chords at 100 bpm from 0 ms"],
  },
  {
    label: "Drum pattern",
    edits: [
      "use swing ride at 100 bpm from 0 ms",
      "use four on the floor at 100 bpm from 0 ms",
      "restore the original pulse",
    ],
  },
  {
    label: "Backing pattern and volume",
    edits: [
      "make the backing sparse",
      "make the backing steady",
      "make the backing soft",
      "make the backing normal",
    ],
  },
  ...["lead", "bass", "drums", "chords"].map((stem) => ({
    label: `Mix ${stem}`,
    edits: [
      `lower ${stem} by 3 dB`,
      `raise ${stem} by 3 dB`,
      `set ${stem} gain to 0 dB`,
      `pan ${stem} 50% left`,
      `pan ${stem} 50% right`,
      `center ${stem}`,
      `mute ${stem}`,
      `unmute ${stem}`,
      `set ${stem} tone to bright`,
      `set ${stem} tone to warm`,
      `set ${stem} tone to neutral`,
    ],
  })),
];

export const isGuided = (text: string) => /^edit:/i.test(text.trim());

export function GuidedEdits({
  text,
  alternate,
  onChange,
  onSwitch,
  disabled,
  initiallyOpen,
  chordsAvailable,
}: {
  text: string;
  alternate: string;
  onChange: (value: string) => void;
  onSwitch: (value: string) => void;
  disabled: boolean;
  initiallyOpen: boolean;
  chordsAvailable: boolean;
}) {
  const clean = text.trim();
  const guided = isGuided(text);
  return (
    <details className="guided-edits" open={initiallyOpen || undefined}>
      <summary>Guided edits</summary>
      {clean && !guided ? (
        <>
          <p>Keep your wording for reference while you choose commands.</p>
          <button
            type="button"
            className="text-action"
            disabled={disabled}
            onClick={() => onSwitch(alternate || "edit: ")}
          >
            {alternate ? "Resume guided draft" : "Compose guided edit"}
          </button>
        </>
      ) : (
        <>
          {guided && alternate && (
            <>
              <p className="small">
                Original wording
                <br />
                <span
                  style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}
                >
                  {alternate}
                </span>
              </p>
              <button
                type="button"
                className="text-action"
                disabled={disabled}
                onClick={() => onSwitch(alternate)}
              >
                Restore original wording
              </button>
            </>
          )}
          <label>
            Add a command
            <select
              aria-label="Add a guided edit"
              disabled={disabled}
              value=""
              onChange={(e) => {
                const base = guided
                  ? clean.slice(5).trim().replace(/;\s*$/, "").trim()
                  : "";
                onChange(`edit: ${base ? base + "; " : ""}${e.target.value}`);
              }}
            >
              <option value="" disabled>
                Choose an edit
              </option>
              {GROUPS.filter(
                (group) => group.label !== "Mix chords" || chordsAvailable,
              ).map(({ label, edits }) => (
                <optgroup key={label} label={label}>
                  {edits.map((edit) => (
                    <option key={edit}>{edit}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <p>
            Combine commands above and edit the numbers before review. Add a
            backing track before mixing it.
          </p>
        </>
      )}
    </details>
  );
}

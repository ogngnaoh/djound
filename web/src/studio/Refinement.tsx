import { Fragment, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Bookmark,
  Check,
  ChevronDown,
  CircleAlert,
  LoaderCircle,
  Play,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { useAudio } from "./Player";
import { mixChanges, TrackMix } from "./TrackMix";
import {
  activeStems,
  allTracksMuted,
  backingTiming,
  hasActiveBacking,
  normalizeMix,
} from "./mixState";
import { ChordChart } from "./ChordChart";
import { GuidedEdits, isGuided } from "./GuidedEdits";
import { DrumGroove, grooveLabel, validGrid } from "./DrumGroove";
import {
  DEFAULT_RECIPE,
  describeRecipe,
  instrumentName,
  type Project,
  type Proposal,
  type Recipe,
  type StudioService,
  type Track,
} from "./types";
const TRACK_OPTIONS = [
  { value: true, text: "On" },
  { value: false, text: "Off" },
] as const;
const ARRANGEMENT_FIELDS = [
  {
    key: "instrument",
    label: "Lead",
    options: [
      { value: "recorded", text: instrumentName("recorded") },
      { value: "piano", text: instrumentName("piano") },
      { value: "electric_piano", text: instrumentName("electric_piano") },
      { value: "guitar", text: instrumentName("guitar") },
    ],
  },
  { key: "bass", label: "Bass", options: TRACK_OPTIONS },
  { key: "drums", label: "Drums", options: TRACK_OPTIONS },
  {
    key: "density",
    label: "Backing density",
    options: [
      { value: "sparse", text: "Sparse" },
      { value: "steady", text: "Steady" },
    ],
  },
  {
    key: "backingVolume",
    label: "Backing volume",
    options: [
      { value: "soft", text: "Soft" },
      { value: "normal", text: "Normal" },
    ],
  },
] as const;

function optionValue(value: boolean | string) {
  if (typeof value === "boolean") return value ? "on" : "off";
  return value;
}

export function RecipeDetails({ recipe }: { recipe: Recipe }) {
  return (
    <>
      <dl className="recipe-details">
        {ARRANGEMENT_FIELDS.map(({ key, label, options }) => (
          <Fragment key={key}>
            <dt>{label}</dt>
            <dd>
              {options.find((o) => o.value === recipe[key])?.text}
              {key === "bass" &&
                recipe.bass &&
                !activeStems(recipe).includes("bass") &&
                " · resting with the chart"}
            </dd>
          </Fragment>
        ))}
        {mixChanges(recipe).map((change) => (
          <Fragment key={change.label}>
            <dt>{change.label}</dt>
            <dd>{change.value}</dd>
          </Fragment>
        ))}
        {recipe.groove && (
          <>
            <dt>{recipe.drums ? "Drum pattern" : "Saved drum pattern"}</dt>
            <dd>{grooveLabel(recipe)}</dd>
          </>
        )}
      </dl>
      {recipe.harmony && <ChordChart recipe={recipe} />}
    </>
  );
}

export function RecipeFields({
  recordedAvailable,
  recipe,
  before,
  onChange,
}: {
  recipe: Recipe;
  before?: Recipe;
  recordedAvailable: boolean;
  onChange: (r: Recipe) => void;
}) {
  function control({
    key,
    label,
    options,
  }: (typeof ARRANGEMENT_FIELDS)[number]) {
    const input = (
      <select
        aria-label={before ? `Proposed ${label.toLowerCase()}` : label}
        value={optionValue(recipe[key])}
        onChange={(event) => {
          const selected = options.find(
            (o) => optionValue(o.value) === event.target.value,
          );
          if (selected)
            onChange(normalizeMix({ ...recipe, [key]: selected.value }));
        }}
      >
        {options
          .filter((o) => recordedAvailable || o.value !== "recorded")
          .map((o) => (
            <option key={optionValue(o.value)} value={optionValue(o.value)}>
              {o.text}
            </option>
          ))}
      </select>
    );
    if (!before)
      return (
        <label key={key}>
          {label}
          {input}
        </label>
      );
    const previous = options.find((o) => o.value === before[key])?.text;
    return (
      <div
        className="change-row"
        key={key}
        data-changed={recipe[key] !== before[key]}
      >
        <span>{label}</span>
        <span className="review-value">
          <span className="subtle">
            {recipe[key] === before[key] ? "Unchanged" : `${previous} →`}
          </span>
          {input}
        </span>
      </div>
    );
  }
  if (!before)
    return (
      <div className="recipe-fields">{ARRANGEMENT_FIELDS.map(control)}</div>
    );
  const changed = ARRANGEMENT_FIELDS.filter(
    (f) => recipe[f.key] !== before[f.key],
  );
  const unchanged = ARRANGEMENT_FIELDS.filter(
    (f) => recipe[f.key] === before[f.key],
  );
  return (
    <div className="review-fields">
      {changed.map(control)}
      {unchanged.length > 0 && (
        <details className="unchanged-settings">
          <summary>Unchanged arrangement settings</summary>
          {unchanged.map(control)}
        </details>
      )}
    </div>
  );
}
const readDraft = (id: string, fallback: string) => {
  try {
    return localStorage.getItem(`djound.studio.draft.${id}`) ?? fallback;
  } catch {
    return fallback;
  }
};
export function Refinement({
  project,
  service,
  basisId,
  onUpdate,
  onInspect,
  onBasis,
  onCreationSettled,
  mode,
}: {
  project: Project;
  service: StudioService;
  basisId: string;
  onUpdate: (p: Project) => void;
  onInspect: (id: string) => void;
  onBasis: (id: string) => void;
  onCreationSettled: () => void;
  mode: string;
}) {
  const basis =
      project.tracks.find((t) => t.id === basisId) ?? project.tracks[0],
    audio = useAudio();
  const referenceKey = `djound.studio.guided.v1.${project.id}`;
  const [text, setText] = useState(() => readDraft(project.id, project.draft)),
    [alternate, setAlternate] = useState<string>(() => {
      try {
        const stored = JSON.parse(localStorage.getItem(referenceKey) ?? "null");
        if (
          stored?.active === text &&
          typeof stored.alternate === "string" &&
          isGuided(stored.alternate) !== isGuided(text)
        )
          return stored.alternate;
      } catch {}
      return "";
    }),
    [quickChart, setQuickChart] = useState<string | null>(null),
    [view, setView] = useState<
      "request" | "adjust" | "review" | "creating" | "result"
    >("request"),
    [recipe, setRecipe] = useState<Recipe>(basis?.recipe ?? DEFAULT_RECIPE),
    [proposal, setProposal] = useState<Proposal | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [result, setResult] = useState<Track | null>(null);
  const controller = useRef<AbortController | null>(null),
    gate = useRef(false),
    previousBasis = useRef(basisId),
    operation = useRef<"review" | "create" | null>(null),
    reviewedRevision = useRef<{
      source: number;
      draft: number;
    } | null>(null);
  const direction = quickChart ?? text;
  const supportedBasis = mode !== "backend" || basis.recipeKnown === true;
  const submittedBasis =
    proposal && (view === "creating" || view === "result")
      ? (project.tracks.find((track) => track.id === proposal.basisId) ?? basis)
      : basis;
  // Creation remains active when navigating to Library; the service owns its durable lifecycle.
  useEffect(
    () => () => {
      if (operation.current === "review") controller.current?.abort();
    },
    [],
  );
  useEffect(() => {
    if (previousBasis.current !== basisId) {
      // A workspace refresh can publish the next playable version before the
      // creation receipt settles. Honor a basis chosen during that window.
      if (view === "creating") return;
      previousBasis.current = basisId;
      setQuickChart(null);
      if (operation.current === "review") controller.current?.abort();
      setRecipe(basis?.recipe ?? DEFAULT_RECIPE);
      setProposal(null);
      setView("request");
      setError("Review your request for the new refinement basis.");
    }
  }, [basisId, basis, view]);
  useEffect(() => {
    if (
      proposal &&
      view !== "creating" &&
      view !== "result" &&
      (proposal.noteRevision !== project.noteRevision ||
        reviewedRevision.current?.source !== project.sourceRevision ||
        reviewedRevision.current?.draft !== project.draftRevision)
    ) {
      setProposal(null);
      setView("request");
      setError("The melody or saved draft changed. Review your changes again.");
    }
  }, [
    project.noteRevision,
    project.sourceRevision,
    project.draftRevision,
    proposal,
    view,
  ]);
  function changeText(
    value: string,
    parked = value === text || (isGuided(text) && isGuided(value))
      ? alternate
      : "",
  ) {
    setText(value);
    setAlternate(parked);
    try {
      localStorage.setItem(`djound.studio.draft.${project.id}`, value);
      if (parked)
        localStorage.setItem(
          referenceKey,
          JSON.stringify({ active: value, alternate: parked }),
        );
      else if (alternate) localStorage.removeItem(referenceKey);
    } catch {}
    setProposal(null);
    setError("");
  }
  function changeReviewedRecipe(next: Recipe) {
    setRecipe(next);
    setView("adjust");
    setError(
      "Settings changed. Review your arrangement before creating audio.",
    );
  }
  async function review(manual = false) {
    if (gate.current) return;
    gate.current = true;
    setBusy(true);
    setError("");
    const c = new AbortController();
    controller.current = c;
    operation.current = "review";
    try {
      const reviewText =
        manual && !direction.trim() ? "Direct arrangement changes" : direction;
      if (reviewText !== direction) {
        if (quickChart === null) changeText(reviewText);
        else setQuickChart(reviewText);
      }
      const saved = await service.saveDraft(project, reviewText, basisId);
      onUpdate(saved);
      c.signal.throwIfAborted();
      const p = await service.propose(
        saved,
        reviewText,
        basisId,
        manual ? recipe : undefined,
        c.signal,
        manual && proposal?.basisId === basisId && proposal.text === reviewText
          ? proposal.id
          : undefined,
      );
      if (c.signal.aborted) return;
      reviewedRevision.current = {
        source: saved.sourceRevision,
        draft: saved.draftRevision,
      };
      setProposal(p);
      setRecipe(p.recipe);
      setView("review");
    } catch (e) {
      if (previousBasis.current === basisId)
        setError(
          e instanceof DOMException && e.name === "AbortError"
            ? "Review cancelled. Your words are still here."
            : e instanceof Error
              ? e.message
              : "Could not review this change. Your words are preserved.",
        );
    } finally {
      controller.current = null;
      operation.current = null;
      gate.current = false;
      setBusy(false);
    }
  }
  async function create() {
    if (!proposal || gate.current) return;
    gate.current = true;
    setBusy(true);
    setError("");
    setView("creating");
    const c = new AbortController();
    controller.current = c;
    operation.current = "create";
    try {
      const next = await service.create(project, proposal, c.signal);
      onUpdate(next);
      const track = next.tracks.at(-1)!;
      setResult(track);
      onInspect(track.id);
      setView("result");
    } catch (e) {
      setError(
        e instanceof DOMException && e.name === "AbortError"
          ? "Creation cancelled. Your reviewed changes are still here."
          : e instanceof Error
            ? e.message
            : "Creation failed. Your previous recordings are safe.",
      );
      setView("review");
    } finally {
      controller.current = null;
      operation.current = null;
      gate.current = false;
      setBusy(false);
      onCreationSettled();
    }
  }
  function cancel() {
    controller.current?.abort();
    setError("Checking cancellation…");
  }
  async function keep() {
    if (!result) return;
    try {
      await service.keep(project.id, result.id, !result.kept);
      const next = await service.get(project.id);
      onUpdate(next);
      setResult(next.tracks.find((t) => t.id === result.id)!);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update Keep.");
    }
  }
  const disabled =
    !project.confirmed ||
    !project.available ||
    project.processing ||
    !supportedBasis;
  const duration = project.melodyDuration ?? project.tracks[0].duration;
  const guidedOnly =
    mode === "backend" &&
    project.available &&
    project.freeformAvailable === false;
  return (
    <div className="refinement">
      <header>
        <h2>Refining {submittedBasis?.title ?? "Original"}</h2>
        <p>
          {view === "review"
            ? "Review before creating audio."
            : view === "creating"
              ? "Your existing recordings stay available."
              : "Changes create a new version."}
        </p>
      </header>
      {!project.confirmed && (
        <div className="notice">Confirm your melody before arranging it.</div>
      )}
      {!project.available && (
        <div className="notice" role="status">
          {project.unavailableReason || "The local engine is unavailable."}{" "}
          Saved recordings remain playable.
        </div>
      )}
      {project.processing && !busy && (
        <p className="notice" role="status">
          A local operation is still running. Saved recordings remain playable.
        </p>
      )}
      {!supportedBasis && (
        <p className="notice" role="status">
          This recording has no local arrangement settings. Choose the original
          or a local version as the refinement basis.
        </p>
      )}
      {error && (
        <div className="notice error" role="alert">
          <CircleAlert size={17} />
          <span>{error}</span>
        </div>
      )}
      {view === "request" && (
        <>
          <label className="request-label" htmlFor="refinement-request">
            Describe a change
          </label>
          <textarea
            id="refinement-request"
            placeholder={
              guidedOnly
                ? "Try “warm the bass” or choose a guided edit below."
                : "Try “Remove drums and make the backing quieter.”"
            }
            value={direction}
            maxLength={1000}
            onChange={(e) => {
              if (quickChart === null) changeText(e.target.value);
              else {
                setQuickChart(e.target.value);
                setProposal(null);
                setError("");
              }
            }}
            disabled={busy}
          />
          {guidedOnly && (
            <p className="notice" role="status">
              The language model is offline. You can still type a specific edit,
              choose a guided edit, or adjust the tracks directly.
            </p>
          )}
          {mode === "backend" && (
            <div className="chord-suggest">
              <button
                type="button"
                className="text-action"
                disabled={disabled || busy}
                onClick={() => {
                  if (quickChart !== null) {
                    setQuickChart(null);
                  } else if (!text.trim() && !alternate) {
                    changeText("edit: suggest chords");
                  } else {
                    // Review this standalone command without replacing either saved draft.
                    try {
                      localStorage.setItem(
                        `djound.studio.draft.${project.id}`,
                        text,
                      );
                    } catch {}
                    setQuickChart("edit: suggest chords");
                  }
                  setError("");
                  setProposal(null);
                }}
              >
                {quickChart !== null
                  ? "Return to previous draft"
                  : "Suggest chords"}
              </button>
              {quickChart !== null && (
                <span className="small subtle">
                  Your previous draft is saved.
                </span>
              )}
            </div>
          )}
          {mode === "backend" && quickChart === null && (
            <GuidedEdits
              chordsAvailable={activeStems(basis.recipe).includes("chords")}
              alternate={alternate}
              text={text}
              onSwitch={(next) => changeText(next, text)}
              onChange={changeText}
              disabled={disabled || busy}
              initiallyOpen={guidedOnly}
            />
          )}
          <Button
            className="full"
            disabled={
              disabled ||
              busy ||
              !direction.trim() ||
              (mode === "backend" && /^edit:$/i.test(direction.trim()))
            }
            onClick={() => void review()}
          >
            {busy ? (
              <>
                <LoaderCircle className="spin" /> Reviewing changes…
              </>
            ) : (
              "Review changes"
            )}
          </Button>
          {busy && (
            <Button variant="ghost" className="full" onClick={cancel}>
              Cancel review
            </Button>
          )}
          <Button
            variant="ghost"
            className="full accent"
            disabled={disabled || busy}
            onClick={() => {
              setRecipe(basis.recipe);
              setProposal(null);
              setView("adjust");
              setError("");
            }}
          >
            <SlidersHorizontal /> Adjust arrangement
          </Button>
          <section className="current-arrangement">
            <h3>
              {basis.kind === "original"
                ? "Starting arrangement"
                : "Current arrangement"}
            </h3>
            {!supportedBasis && (
              <p className="small subtle">
                Arrangement settings are unavailable for this recording.
              </p>
            )}
            {supportedBasis && <RecipeDetails recipe={basis.recipe} />}
          </section>
          <details className="arrangement-disclosure">
            <summary>
              Arrangement details <ChevronDown size={16} />
            </summary>
            <p>
              {supportedBasis
                ? backingTiming(basis.recipe)
                : "This recording cannot be used for a local arrangement."}{" "}
              Your confirmed melody keeps its own timing.
            </p>
          </details>
        </>
      )}
      {view === "adjust" && (
        <>
          <div className="panel-section-title">
            <h3>Adjust arrangement</h3>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Back to request"
              onClick={() => setView("request")}
            >
              <X />
            </Button>
          </div>
          <p className="subtle">
            Choose the sound directly. Your melody stays unchanged.
          </p>
          <fieldset disabled={busy}>
            <RecipeFields
              recordedAvailable={mode === "backend"}
              recipe={recipe}
              onChange={(r) => {
                setRecipe(r);
              }}
            />
            {mode === "backend" && (
              <>
                <DrumGroove
                  projectId={project.id}
                  recipe={recipe}
                  duration={duration}
                  onChange={setRecipe}
                />
                <ChordChart
                  recipe={recipe}
                  before={proposal?.before}
                  suggestions={proposal?.chordSuggestions}
                  onChange={setRecipe}
                />
                <TrackMix
                  recipe={recipe}
                  onChange={setRecipe}
                  audition={
                    !busy && service.auditionTone
                      ? { project, basis, load: service.auditionTone }
                      : undefined
                  }
                />
              </>
            )}
          </fieldset>
          <Button
            className="full"
            disabled={
              disabled ||
              busy ||
              allTracksMuted(recipe) ||
              !validGrid(recipe, duration)
            }
            onClick={() => void review(true)}
          >
            {busy ? "Reviewing…" : "Review arrangement"}
          </Button>
          {allTracksMuted(recipe) && (
            <p role="status" className="small subtle">
              Unmute at least one active track to review this mix.
            </p>
          )}
          <p className="small subtle">
            Reviewing these controls does not create audio.
          </p>
        </>
      )}
      {view === "review" && proposal && (
        <>
          <section className="review-request">
            <h3>Describe a change</h3>
            <p>{proposal.text || "Direct arrangement changes"}</p>
            <button
              className="text-action"
              onClick={() => {
                setView("request");
                setProposal(null);
              }}
            >
              Edit request
            </button>
          </section>
          <h2 className="proposed-heading">Proposed changes</h2>
          {mode === "backend" && (
            <ChordChart
              recipe={recipe}
              before={proposal.before}
              suggestions={proposal.chordSuggestions}
              onChange={changeReviewedRecipe}
            />
          )}
          {grooveLabel(recipe) !== grooveLabel(proposal.before) && (
            <div className="change-row" data-changed="true">
              <span>Drum pattern</span>
              <span>
                <span className="subtle">
                  {grooveLabel(proposal.before)} →{" "}
                </span>
                {grooveLabel(recipe)}
              </span>
            </div>
          )}
          {mixChanges(recipe, proposal.before).map((change) => (
            <div className="change-row" data-changed="true" key={change.label}>
              <span>{change.label}</span>
              <span>
                <span className="subtle">{change.previous} → </span>
                {change.value}
              </span>
            </div>
          ))}
          <RecipeFields
            recordedAvailable={mode === "backend"}
            recipe={recipe}
            before={proposal.before}
            onChange={changeReviewedRecipe}
          />
          {!hasActiveBacking(recipe) &&
            (recipe.density !== proposal.before.density ||
              recipe.backingVolume !== proposal.before.backingVolume) && (
              <p className="small subtle">
                These settings apply when a backing part plays.
              </p>
            )}
          {basis.noteRevision != null &&
          basis.noteRevision !== proposal.noteRevision ? (
            <p className="notice">
              This uses your current melody (revision {proposal.noteRevision}).{" "}
              {basis.title} used revision {basis.noteRevision}. Its arrangement
              supplies the starting settings.
            </p>
          ) : (
            <p className="small subtle proposal-note">
              Your confirmed melody stays the same.
            </p>
          )}
          {recipe.instrument === "recorded" && (
            <p className="small subtle">
              The lead plays your uploaded audio. Confirmed notes guide the
              added backing.
            </p>
          )}
          {proposal.warnings.map((w, i) => (
            <p className="notice" key={i}>
              {w}
            </p>
          ))}
          <Button
            className="full"
            onClick={() => void create()}
            disabled={disabled || busy || !proposal.canGenerate}
          >
            Create version
          </Button>
          <Button
            variant="ghost"
            className="full accent"
            onClick={() => {
              setView("adjust");
            }}
          >
            <SlidersHorizontal /> Adjust arrangement
          </Button>
        </>
      )}
      {view === "creating" && (
        <div className="creation-state" role="status">
          <LoaderCircle className="spin" size={28} />
          <h2>Creating your next version</h2>
          <p>
            {mode === "prototype"
              ? "Simulating creation with sample audio."
              : "Waiting for the local engine to finish."}
          </p>
          <div className="submitted-request">
            <small>REQUEST</small>
            <p>{proposal?.text || "Direct arrangement changes"}</p>
          </div>
          <p className="subtle">
            Based on {submittedBasis.title}. Keep listening while you wait.
          </p>
          <Button variant="outline" className="full" onClick={cancel}>
            Cancel creation
          </Button>
        </div>
      )}
      {view === "result" && result && (
        <div className="result-state">
          <h2>{result.title} is ready.</h2>
          <p>{describeRecipe(result.recipe)}.</p>
          {mode === "prototype" && (
            <p className="small subtle">
              Prototype result · prerecorded sample audio
            </p>
          )}
          <Button className="full" onClick={() => audio.choose(result)}>
            <Play fill="currentColor" /> Play {result.title}
          </Button>
          <Button
            variant="outline"
            className="full"
            onClick={() => audio.choose(submittedBasis)}
          >
            Compare with {submittedBasis.title}
          </Button>
          <Button
            variant="outline"
            className="full"
            onClick={() => void keep()}
          >
            {result.kept ? <Check /> : <Bookmark />}
            {result.kept ? "Kept" : "Keep"}
          </Button>
          <Button
            variant="outline"
            className="full"
            onClick={() => {
              onBasis(result.id);
              setQuickChart(null);
              setView("request");
              setResult(null);
            }}
          >
            <ArrowRight /> Use this version
          </Button>
          <p className="subtle">
            Use {result.title} as the basis for another change.
          </p>
          <details>
            <summary>Request used</summary>
            <p>{proposal?.text || "Direct arrangement changes"}</p>
          </details>
          <p className="subtle small">
            Nothing starts automatically. Your listening and refinement basis
            stay as you left them.
          </p>
        </div>
      )}
    </div>
  );
}

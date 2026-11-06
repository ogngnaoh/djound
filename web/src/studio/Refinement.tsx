import { useEffect, useRef, useState } from "react";
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
export function RecipeDetails({ recipe }: { recipe: Recipe }) {
  return (
    <dl className="recipe-details">
      <dt>Lead</dt>
      <dd>{instrumentName(recipe.instrument)}</dd>
      <dt>Bass</dt>
      <dd>{recipe.bass ? "On" : "Off"}</dd>
      <dt>Drums</dt>
      <dd>{recipe.drums ? "On" : "Off"}</dd>
      <dt>Backing volume</dt>
      <dd>{recipe.backingVolume === "soft" ? "Soft" : "Normal"}</dd>
    </dl>
  );
}
export function RecipeFields({
  recipe,
  onChange,
}: {
  recipe: Recipe;
  onChange: (r: Recipe) => void;
}) {
  return (
    <div className="recipe-fields">
      <label>
        Lead
        <select
          aria-label="Lead"
          value={recipe.instrument}
          onChange={(e) =>
            onChange({
              ...recipe,
              instrument: e.target.value as Recipe["instrument"],
            })
          }
        >
          <option value="piano">Piano</option>
          <option value="electric_piano">Electric piano</option>
          <option value="guitar">Guitar</option>
        </select>
      </label>
      {(["bass", "drums"] as const).map((k) => (
        <label key={k}>
          {k === "bass" ? "Bass" : "Drums"}
          <select
            aria-label={k === "bass" ? "Bass" : "Drums"}
            value={recipe[k] ? "on" : "off"}
            onChange={(e) =>
              onChange({ ...recipe, [k]: e.target.value === "on" })
            }
          >
            <option value="on">On</option>
            <option value="off">Off</option>
          </select>
        </label>
      ))}
      <label>
        Backing density
        <select
          aria-label="Backing density"
          value={recipe.density}
          onChange={(e) =>
            onChange({
              ...recipe,
              density: e.target.value as Recipe["density"],
            })
          }
        >
          <option value="sparse">Sparse</option>
          <option value="steady">Steady</option>
        </select>
      </label>
      <label>
        Backing volume
        <select
          aria-label="Backing volume"
          value={recipe.backingVolume}
          onChange={(e) =>
            onChange({
              ...recipe,
              backingVolume: e.target.value as Recipe["backingVolume"],
            })
          }
        >
          <option value="soft">Soft</option>
          <option value="normal">Normal</option>
        </select>
      </label>
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
  const [text, setText] = useState(() => readDraft(project.id, project.draft)),
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
  function changeText(value: string) {
    setText(value);
    try {
      localStorage.setItem(`djound.studio.draft.${project.id}`, value);
    } catch {}
    setProposal(null);
    setError("");
  }
  function changeReviewedRecipe(next: Recipe) {
    setRecipe(next);
    setProposal(null);
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
        manual && !text.trim() ? "Direct arrangement changes" : text;
      if (reviewText !== text) changeText(reviewText);
      const saved = await service.saveDraft(project, reviewText, basisId);
      onUpdate(saved);
      c.signal.throwIfAborted();
      const p = await service.propose(
        saved,
        reviewText,
        basisId,
        manual ? recipe : undefined,
        c.signal,
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
            placeholder="Try “Remove drums and make the backing quieter.”"
            value={text}
            maxLength={1000}
            onChange={(e) => changeText(e.target.value)}
            disabled={busy}
          />
          <Button
            className="full"
            disabled={disabled || busy || !text.trim()}
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
                ? `Backing is ${basis.recipe.density}, at a fixed 100 BPM.`
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
              recipe={recipe}
              onChange={(r) => {
                setRecipe(r);
                setProposal(null);
              }}
            />
          </fieldset>
          <Button
            className="full"
            disabled={disabled || busy}
            onClick={() => void review(true)}
          >
            {busy ? "Reviewing…" : "Review arrangement"}
          </Button>
          <p className="small subtle">
            Reviewing these controls does not create audio.
          </p>
        </>
      )}
      {view === "review" && proposal && (
        <>
          <section className="review-request">
            <h3>Describe a change</h3>
            <p>{text || "Direct arrangement changes"}</p>
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
          <div className="change-row">
            <span>Lead</span>
            <span>
              {instrumentName(recipe.instrument)}
              <small>
                {recipe.instrument === proposal.before.instrument
                  ? "Unchanged"
                  : `Previously ${instrumentName(proposal.before.instrument)}`}
              </small>
            </span>
          </div>
          <div className="change-row">
            <span>Melody</span>
            <span className="subtle">Unchanged</span>
          </div>
          <div className="change-row">
            <span>Drums</span>
            <span className="review-value">
              <span className="subtle">
                {proposal.before.drums ? "On" : "Off"} →
              </span>
              <select
                aria-label="Proposed drums"
                value={recipe.drums ? "on" : "off"}
                onChange={(e) =>
                  changeReviewedRecipe({
                    ...recipe,
                    drums: e.target.value === "on",
                  })
                }
              >
                <option value="on">On</option>
                <option value="off">Off</option>
              </select>
            </span>
          </div>
          <div className="change-row">
            <span>Backing volume</span>
            <span className="review-value">
              <span className="subtle">
                {proposal.before.backingVolume === "soft" ? "Soft" : "Normal"} →
              </span>
              <select
                aria-label="Proposed backing volume"
                value={recipe.backingVolume}
                onChange={(e) =>
                  changeReviewedRecipe({
                    ...recipe,
                    backingVolume: e.target.value as Recipe["backingVolume"],
                  })
                }
              >
                <option value="soft">Soft</option>
                <option value="normal">Normal</option>
              </select>
            </span>
          </div>
          <details className="other-settings">
            <summary>Other arrangement settings</summary>
            <RecipeDetails recipe={recipe} />
            <p>Density: {recipe.density}</p>
          </details>
          <p className="small subtle proposal-note">
            ⓘ &nbsp; Proposal only. No new audio yet.
          </p>
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
              setProposal(null);
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

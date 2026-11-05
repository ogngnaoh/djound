import { useEffect, useMemo, useRef, useState } from "react";
import {
  Link,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Bookmark,
  Check,
  ChevronRight,
  Folder,
  LoaderCircle,
  MoreHorizontal,
  Music2,
  Pause,
  Play,
  Plus,
  Settings2,
  SlidersHorizontal,
} from "lucide-react";
import { Button } from "../components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetDescription,
  SheetTrigger,
} from "../components/ui/sheet";
import {
  createStudioService,
  pendingBackendCreation,
  recoverBackendCreation,
} from "./service";
import { AudioProvider, Transport, Waveform, clock, useAudio } from "./Player";
import { Refinement } from "./Refinement";
import { MelodyEditor } from "./MelodyEditor";
import {
  DEFAULT_RECIPE,
  type Mode,
  type Note,
  type Project,
  type Scenario,
  type Track,
} from "./types";
import { Context, useStudio, storage, remember } from "./context";
import { Loading, Failure } from "./Views";
import { LibraryPage } from "./LibraryPage";
import { NewProject } from "./NewProject";
import "./studio.css";
const scenarios: { value: Scenario; label: string }[] = [
  { value: "normal", label: "Complete project" },
  { value: "empty", label: "Empty library" },
  { value: "unclear", label: "Unclear request" },
  { value: "unavailable", label: "Engine unavailable" },
  { value: "creation-failure", label: "Creation failure" },
  { value: "playback-failure", label: "Playback failure" },
  { value: "stale", label: "Stale proposal" },
];
export default function StudioApp() {
  const [mode, setModeState] = useState<Mode>(() =>
      new URLSearchParams(location.search).get("mode") === "prototype"
        ? "prototype"
        : new URLSearchParams(location.search).get("mode") === "backend"
          ? "backend"
          : storage("djound.mode", "prototype") === "backend"
            ? "backend"
            : "prototype",
    ),
    [scenario, setScenarioState] = useState<Scenario>("normal");
  const service = useMemo(
    () => createStudioService(mode, scenario),
    [mode, scenario],
  );
  const client = useQueryClient();
  function setMode(v: Mode) {
    remember("djound.mode", v);
    setModeState(v);
    setScenarioState("normal");
    client.clear();
  }
  function setScenario(v: Scenario) {
    setScenarioState(v);
    client.clear();
  }
  return (
    <Context.Provider value={{ mode, scenario, service, setMode, setScenario }}>
      <AudioProvider
        key={mode + scenario}
        failed={scenario === "playback-failure"}
      >
        <Shell />
      </AudioProvider>
    </Context.Provider>
  );
}
function Shell() {
  const { mode, scenario, setMode, setScenario, service } = useStudio(),
    navigate = useNavigate(),
    location = useLocation(),
    audio = useAudio(),
    client = useQueryClient();
  const [tools, setTools] = useState(false);
  const last = storage(
    `djound.last.${mode}`,
    mode === "prototype" ? "evening" : "",
  );
  const projectPath =
    location.pathname.startsWith("/remix/") &&
    location.pathname !== "/remix/new"
      ? location.pathname.split("/").slice(0, 3).join("/")
      : last
        ? `/remix/${last}`
        : "/remix/new";
  useEffect(() => {
    document.title = "Djound — Your music workspace";
    window.scrollTo(0, 0);
    setTools(false);
  }, [location.pathname]);
  useEffect(() => {
    const viewport = window.visualViewport;
    function update() {
      document.documentElement.style.setProperty(
        "--keyboard-inset",
        `${Math.max(0, window.innerHeight - (viewport?.height || window.innerHeight) - (viewport?.offsetTop || 0))}px`,
      );
    }
    viewport?.addEventListener("resize", update);
    update();
    return () => viewport?.removeEventListener("resize", update);
  }, []);
  return (
    <div className={`studio-app ${audio.track ? "with-player" : ""}`}>
      <a className="skip-link" href="#workspace">
        Skip to workspace
      </a>
      <header className="navigation">
        <Link className="brand" to="/" aria-label="Djound home">
          <img src="/brand/wordmark.svg" alt="Djound" />
        </Link>
        {mode === "prototype" && (
          <span className="mobile-prototype-label">Prototype</span>
        )}
        <nav aria-label="Main navigation">
          <NavLink to="/" end>
            <Folder />
            Library
          </NavLink>
          <NavLink to="/remix/new">
            <Plus />
            New project
          </NavLink>
          <Link
            className={
              location.pathname.startsWith("/remix/") &&
              location.pathname !== "/remix/new"
                ? "active"
                : ""
            }
            to={projectPath}
          >
            <SlidersHorizontal />
            Project Studio
          </Link>
        </nav>
        <div className="workspace-mode">
          <button onClick={() => setTools(true)}>
            <span className={mode === "prototype" ? "mode-dot" : ""} />
            {mode === "prototype" ? "Prototype workspace" : "Local workspace"}
            <Settings2 size={14} />
          </button>
          <span>
            {mode === "prototype"
              ? "Sample data · no AI required"
              : "Connected to your local backend"}
          </span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="mobile-tools"
          aria-label="Workspace options"
          onClick={() => setTools(true)}
        >
          <MoreHorizontal />
        </Button>
      </header>
      <main id="workspace">
        <Routes>
          <Route path="/" element={<LibraryPage />} />
          <Route path="/remix/new" element={<NewProject />} />
          <Route path="/remix/:id" element={<ProjectPage />} />
          <Route path="/remix/:id/melody" element={<ProjectPage melody />} />
          <Route
            path="/remix/:id/notes"
            element={<ProjectPage melody editing />}
          />
          <Route
            path="*"
            element={
              <div className="standalone">
                <h1>Page not found</h1>
                <Link className="text-action" to="/">
                  Return to Library
                </Link>
              </div>
            }
          />
        </Routes>
      </main>
      <Transport />
      <Sheet open={tools} onOpenChange={setTools}>
        <SheetContent>
          <div className="workspace-options">
            <SheetTitle>Workspace options</SheetTitle>
            <SheetDescription>
              Choose the prototype or your local installation.
            </SheetDescription>
            <label>
              Data source
              <select
                aria-label="Data source"
                value={mode}
                onChange={(e) => {
                  setMode(e.target.value as Mode);
                  navigate("/");
                }}
              >
                <option value="prototype">Clickable prototype</option>
                <option value="backend">Existing backend</option>
              </select>
            </label>
            {mode === "prototype" ? (
              <>
                <p>
                  Sample projects and prerecorded versions. Creation and
                  interpretation are simulated; no model is called.
                </p>
                <label>
                  Review scenario
                  <select
                    aria-label="Review scenario"
                    value={scenario}
                    onChange={(e) => {
                      setScenario(e.target.value as Scenario);
                      navigate(
                        e.target.value === "empty" ? "/" : "/remix/evening",
                      );
                    }}
                  >
                    {scenarios.map((s) => (
                      <option key={s.value} value={s.value}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  variant="outline"
                  onClick={() => {
                    service.reset?.();
                    client.clear();
                    audio.clear();
                    setScenario("normal");
                    navigate("/");
                    setTools(false);
                  }}
                >
                  Reset sample workspace
                </Button>
                <p className="small subtle">
                  Reset affects prototype data only.
                </p>
              </>
            ) : (
              <p>
                Your existing backend must be running on port 8080. AI-dependent
                actions report real availability; no sample fallback is used.
              </p>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
function ProjectHeader({
  project,
  eyebrow = "PROJECT STUDIO",
}: {
  project: Project;
  eyebrow?: string;
}) {
  const [actions, setActions] = useState(false);
  return (
    <header className="project-header">
      <Sheet open={actions} onOpenChange={setActions}>
        <SheetTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="project-actions-trigger"
            aria-label="Project actions"
          >
            <MoreHorizontal />
          </Button>
        </SheetTrigger>
        <SheetContent>
          <div className="workspace-options">
            <SheetTitle>{project.title}</SheetTitle>
            <SheetDescription>
              Return to your music or review the melody behind it.
            </SheetDescription>
            <Button variant="outline" asChild>
              <Link
                to={`/remix/${project.id}/melody`}
                onClick={() => setActions(false)}
              >
                <SlidersHorizontal />
                Review melody notes
              </Link>
            </Button>
            <Button variant="outline" asChild>
              <Link
                to={`/remix/${project.id}`}
                onClick={() => setActions(false)}
              >
                <Music2 />
                Open Project Studio
              </Link>
            </Button>
            <Button variant="ghost" asChild>
              <Link to="/" onClick={() => setActions(false)}>
                <Folder />
                Back to Library
              </Link>
            </Button>
            <p className="small subtle">
              {project.fileName} · {clock(project.tracks[0]?.duration ?? 0)}
              <br />
              Your uploaded original is always preserved.
            </p>
          </div>
        </SheetContent>
      </Sheet>
      <span className="eyebrow">{eyebrow}</span>
      <h1>{project.title}</h1>
      <p>
        {project.fileName} · {clock(project.tracks[0]?.duration ?? 0)}
      </p>
    </header>
  );
}
function ProjectPage({
  melody = false,
  editing = false,
}: {
  melody?: boolean;
  editing?: boolean;
}) {
  const { id } = useParams(),
    { service, mode, scenario } = useStudio(),
    client = useQueryClient();
  const key = ["studio-project", mode, scenario, id];
  const q = useQuery({
    queryKey: key,
    queryFn: () => service.get(id!),
    retry: false,
  });
  useEffect(() => {
    if (id) remember(`djound.last.${mode}`, id);
  }, [id, mode]);
  function update(p: Project) {
    client.setQueryData(key, p);
    void client.invalidateQueries({ queryKey: ["studio-library"] });
  }
  if (q.isPending) return <Loading />;
  if (q.isError)
    return <Failure message={q.error.message} retry={() => void q.refetch()} />;
  return melody ? (
    <MelodyPage key={id} project={q.data} onUpdate={update} editing={editing} />
  ) : (
    <Studio key={id} project={q.data} onUpdate={update} />
  );
}
function Studio({
  project,
  onUpdate,
}: {
  project: Project;
  onUpdate: (p: Project) => void;
}) {
  const { mode, service } = useStudio(),
    location = useLocation(),
    audio = useAudio();
  const requested = new URLSearchParams(location.search).get("recording");
  const [inspected, setInspected] = useState(
      requested || project.tracks.at(-1)!.id,
    ),
    [basisId, setBasisId] = useState(
      project.basisId || project.tracks.at(-1)!.id,
    ),
    [sheet, setSheet] = useState(false),
    [fullHistory, setFullHistory] = useState(false),
    [error, setError] = useState("");
  const track =
    project.tracks.find((t) => t.id === inspected) ?? project.tracks[0];
  const compact = useCompact();
  const refineTrigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!audio.track) audio.choose(project.tracks.at(-1)!, false);
  }, []);
  useEffect(() => {
    if (requested && project.tracks.some((t) => t.id === requested))
      setInspected(requested);
  }, [requested, project.tracks]);
  async function basis(id: string) {
    const previous = basisId;
    setBasisId(id);
    setError("");
    try {
      const draft = storage(`djound.studio.draft.${project.id}`, project.draft);
      onUpdate(await service.saveDraft(project, draft, id));
    } catch (e) {
      setBasisId(previous);
      setError(
        e instanceof Error ? e.message : "Could not save refinement basis.",
      );
    }
  }
  async function keep(t: Track) {
    try {
      await service.keep(project.id, t.id, !t.kept);
      onUpdate(await service.get(project.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not keep version.");
    }
  }
  const panel = (
    <Refinement
      project={project}
      service={service}
      basisId={basisId}
      onUpdate={onUpdate}
      onInspect={setInspected}
      onBasis={(id) => void basis(id)}
      mode={mode}
    />
  );
  return (
    <div className="page-grid">
      <section className="main-column studio-page">
        <ProjectHeader project={project} />
        {mode === "backend" && (
          <CreationRecovery project={project} onUpdate={onUpdate} />
        )}{" "}
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
        <section className="recording-inspector">
          <div className="recording-heading">
            <h2>{track.title}</h2>
            <span className="small subtle">
              {audio.track?.id === track.id
                ? audio.playing
                  ? "Playing"
                  : "Paused"
                : "Viewing"}
            </span>
          </div>
          <p className="subtle">{track.description}</p>
          <Waveform track={track} />
          <ListeningChoices project={project} />
        </section>
        <details className="source-details">
          <summary>
            <ChevronRight size={17} />
            Source and technical details
          </summary>
          <div>
            <p>
              {project.fileName} · {clock(project.tracks[0].duration)}
            </p>
            <p>
              {mode === "prototype"
                ? "Prototype recordings use authored sample audio."
                : "Recordings are stored in your local workspace."}{" "}
              Melody timing is preserved. Backing uses a fixed 100 BPM.
            </p>
            <Link className="text-action" to={`/remix/${project.id}/melody`}>
              Review melody notes <ArrowRight size={15} />
            </Link>
          </div>
        </details>
        <section
          className={`version-history ${fullHistory ? "history-expanded" : ""}`}
        >
          <div className="section-heading">
            <h2>Version history</h2>
            <span className="desktop-label small subtle">
              Select a row to inspect
            </span>
            <button
              className="text-action mobile-history"
              onClick={() => setFullHistory(!fullHistory)}
            >
              {fullHistory ? "Show recent" : "View all"}
            </button>
          </div>
          {project.tracks.map((t, i) => (
            <div
              className={`version-row ${t.id === track.id ? "inspected" : ""} ${i < project.tracks.length - 2 ? "older-version" : ""}`}
              key={t.id}
            >
              <button
                className="version-inspect"
                aria-label={`Inspect ${t.title}`}
                aria-pressed={t.id === track.id}
                onClick={() => setInspected(t.id)}
              >
                <strong>{t.title}</strong>
                <small>
                  {t.id === basisId
                    ? "Refinement basis"
                    : t.kind === "original"
                      ? "Uploaded source"
                      : t.kept
                        ? "Kept"
                        : "Created audio"}
                </small>
              </button>
              <span className="version-description">{t.description}</span>
              <button
                className="keep-marker"
                aria-label={`${t.kept ? "Unkeep" : "Keep"} ${t.title}`}
                onClick={() => void keep(t)}
                disabled={t.kind === "original"}
              >
                {t.kind === "version" && (
                  <>
                    <Bookmark
                      size={14}
                      fill={t.kept ? "currentColor" : "none"}
                    />
                    <span>{t.kept ? "Kept" : "Keep"}</span>
                  </>
                )}
              </button>
              <button
                className="row-play"
                aria-label={`${audio.track?.id === t.id && audio.playing ? "Pause" : "Play"} ${t.title}`}
                onClick={() =>
                  audio.track?.id === t.id ? audio.toggle() : audio.choose(t)
                }
              >
                {audio.track?.id === t.id && audio.playing ? (
                  <Pause size={12} fill="currentColor" />
                ) : (
                  <Play size={12} fill="currentColor" />
                )}
                {audio.track?.id === t.id && audio.playing ? "Pause" : "Play"}
              </button>
              {t.kind === "version" ? (
                <button
                  className={`row-use ${t.id === basisId ? "in-use" : ""}`}
                  disabled={t.id === basisId}
                  onClick={() => void basis(t.id)}
                >
                  {t.id === basisId ? "In use" : "Use this version"}
                </button>
              ) : (
                <span />
              )}
            </div>
          ))}
        </section>
        {!project.confirmed && (
          <div className="confirm-callout">
            <p>Review and confirm your melody to create an arrangement.</p>
            <Button asChild>
              <Link to={`/remix/${project.id}/melody`}>
                Review melody <ArrowRight />
              </Link>
            </Button>
          </div>
        )}
        {compact && (
          <Button
            ref={refineTrigger}
            className="full mobile-refine-trigger"
            onClick={() => setSheet(true)}
          >
            Describe a change
          </Button>
        )}
      </section>
      {!compact && <aside className="context-column">{panel}</aside>}
      {compact && (
        <Sheet open={sheet} onOpenChange={setSheet} modal={false}>
          <SheetContent
            className="refinement-sheet"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              refineTrigger.current?.focus();
            }}
            onInteractOutside={(e) => {
              if ((e.target as Element)?.closest(".transport"))
                e.preventDefault();
            }}
          >
            <SheetTitle className="sr-only">Refinement</SheetTitle>
            <SheetDescription className="sr-only">
              Review changes and create a version. Your player remains
              available.
            </SheetDescription>
            {panel}
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}
function ListeningChoices({
  project,
  preview,
}: {
  project: Project;
  preview?: Track;
}) {
  const audio = useAudio(),
    tracks = preview ? [project.tracks[0], preview] : project.tracks;
  return (
    <div className="listen-choices">
      <span>Listen to</span>
      <div role="group" aria-label="Choose a recording to play">
        {tracks.map((t) => (
          <button
            key={t.id}
            aria-pressed={audio.track?.id === t.id}
            className={audio.track?.id === t.id ? "active" : ""}
            onClick={() =>
              audio.track?.id === t.id ? audio.toggle() : audio.choose(t)
            }
          >
            {t.title}
          </button>
        ))}
      </div>
    </div>
  );
}
function useCompact() {
  const [value, setValue] = useState(() => window.innerWidth < 1280);
  useEffect(() => {
    const mq = matchMedia("(max-width:1279px)");
    const update = () => setValue(mq.matches);
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return value;
}
function MelodyPage({
  project,
  onUpdate,
  editing,
}: {
  project: Project;
  onUpdate: (p: Project) => void;
  editing: boolean;
}) {
  const { service, mode } = useStudio(),
    navigate = useNavigate(),
    audio = useAudio();
  const compact = useCompact();
  type SavedNoteEdits = {
    version: 1;
    sourceRevision: number;
    baseNoteRevision: number;
    notes: Note[];
  };
  const draftKey = `djound.melody-edits.v1.${mode}.${project.id}.${project.sourceRevision}`;
  function readEdits(): SavedNoteEdits | null {
    try {
      const saved = JSON.parse(
        localStorage.getItem(draftKey) || "null",
      ) as SavedNoteEdits | null;
      if (
        saved?.version !== 1 ||
        saved.sourceRevision !== project.sourceRevision ||
        !Number.isInteger(saved.baseNoteRevision) ||
        !Array.isArray(saved.notes) ||
        saved.notes.length > 256 ||
        !saved.notes.every((note) =>
          [note.start, note.end, note.pitch, note.velocity].every(
            Number.isFinite,
          ),
        )
      )
        return null;
      return saved;
    } catch {
      return null;
    }
  }
  const [edits, setEdits] = useState<SavedNoteEdits | null>(readEdits),
    [busy, setBusy] = useState(""),
    [error, setError] = useState("");
  const notes = edits?.notes ?? project.notes;
  const staleEdits =
    !!edits &&
    (edits.sourceRevision !== project.sourceRevision ||
      edits.baseNoteRevision !== project.noteRevision);
  const dirty = !!edits;
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    setEdits(readEdits());
  }, [draftKey]);
  useEffect(() => {
    if (!audio.track) audio.choose(project.tracks[0], false);
  }, [project.id, audio.track, audio.choose]);
  useEffect(() => () => controller.current?.abort(), []);
  function discardEdits() {
    setEdits(null);
    setError("");
    try {
      localStorage.removeItem(draftKey);
    } catch {
      setError(
        "Could not clear saved note edits. Browser storage is unavailable.",
      );
    }
  }
  function changeNotes(next: Note[]) {
    if (controller.current) {
      setError("Wait for the current note operation before editing again.");
      return;
    }
    if (staleEdits) {
      setError(
        "The saved melody changed. Discard your local edits and reload the current notes before editing.",
      );
      return;
    }
    if (JSON.stringify(next) === JSON.stringify(project.notes)) {
      discardEdits();
      return;
    }
    const value: SavedNoteEdits = {
      version: 1,
      sourceRevision: project.sourceRevision,
      baseNoteRevision: edits?.baseNoteRevision ?? project.noteRevision,
      notes: next,
    };
    setEdits(value);
    setError("");
    try {
      localStorage.setItem(draftKey, JSON.stringify(value));
    } catch {
      setError(
        "Your edits are available here, but could not be saved in this browser. Keep this page open until you save them.",
      );
    }
  }
  async function saveNotes(confirm: boolean, signal: AbortSignal) {
    if (staleEdits)
      throw new Error(
        "The saved melody changed. Discard local edits and reload before saving.",
      );
    signal.throwIfAborted();
    const saved = await service.saveNotes(project, notes, confirm);
    discardEdits();
    onUpdate(saved);
    return saved;
  }
  const preview: Track | undefined =
    !dirty && project.previewUrl
      ? {
          id: `preview-${project.id}-${project.noteRevision}`,
          projectId: project.id,
          projectTitle: project.title,
          title: "Detected melody",
          description:
            mode === "prototype"
              ? "Prototype melody preview"
              : "Melody preview",
          url: project.previewUrl,
          duration: project.tracks[0].duration,
          kind: "preview",
          kept: false,
          recipe: DEFAULT_RECIPE,
        }
      : undefined;
  async function run(
    label: string,
    fn: (signal: AbortSignal) => Promise<Project>,
    after?: (p: Project) => void,
  ) {
    if (controller.current) return;
    setBusy(label);
    setError("");
    const c = new AbortController();
    controller.current = c;
    try {
      const next = await fn(c.signal);
      if (!c.signal.aborted) {
        onUpdate(next);
        after?.(next);
      }
    } catch (e) {
      if (!c.signal.aborted)
        setError(
          e instanceof Error
            ? e.message
            : "This action could not finish. Your notes are preserved.",
        );
    } finally {
      if (controller.current === c) {
        controller.current = null;
        setBusy("");
      }
    }
  }
  async function previewNotes() {
    await run(
      dirty ? "Saving notes…" : "Preparing preview…",
      async (signal) => {
        if (staleEdits)
          throw new Error(
            "Discard local edits and reload the current melody before previewing.",
          );
        const saved = dirty ? await saveNotes(false, signal) : project;
        signal.throwIfAborted();
        setBusy("Preparing preview…");
        return service.preview(saved, signal);
      },
      (p) => {
        if (p.previewUrl)
          audio.choose({
            id: `preview-${p.id}-${p.noteRevision}`,
            projectId: p.id,
            projectTitle: p.title,
            title: "Detected melody",
            description: "Melody preview",
            url: p.previewUrl,
            duration: p.tracks[0].duration,
            kind: "preview",
            kept: false,
            recipe: DEFAULT_RECIPE,
          });
      },
    );
  }
  return (
    <div className={`page-grid ${editing ? "editing-page" : ""}`}>
      <section className="main-column melody-page">
        <ProjectHeader project={project} />
        <div className="section-heading">
          <h2>{editing ? "Edit notes" : "Check your melody"}</h2>
          <Link className="text-action" to={`/remix/${project.id}`}>
            Back to Studio
          </Link>
        </div>
        <p className="subtle">
          {editing
            ? "Move notes or change their length. The uploaded original is never changed."
            : "Compare the detected melody with your original, then confirm when it sounds right."}
        </p>
        {staleEdits && (
          <div className="notice error" role="alert">
            <p>
              The saved melody changed since these edits began. Your local edits
              are preserved, but cannot overwrite the newer notes.
            </p>
            <Button variant="outline" onClick={discardEdits}>
              Discard local edits and reload
            </Button>
          </div>
        )}
        {dirty && !staleEdits && (
          <p className="notice" role="status">
            Unsaved melody changes are kept in this browser. Preview or save
            them before confirming.
          </p>
        )}
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
        {busy && (
          <p className="notice" role="status">
            <LoaderCircle className="spin" size={18} />
            {busy}
            {(busy === "Detecting melody…" ||
              busy === "Preparing preview…") && (
              <button
                className="text-action"
                onClick={() => {
                  controller.current?.abort();
                }}
              >
                Cancel
              </button>
            )}
          </p>
        )}
        {!project.notes.length ? (
          <div className="detect-state">
            <Waveform track={project.tracks[0]} />
            <ListeningChoices
              project={{ ...project, tracks: [project.tracks[0]] }}
            />
            <Button
              disabled={!!busy || !project.available}
              onClick={() =>
                void run("Detecting melody…", (signal) =>
                  service.detect(project, signal),
                )
              }
            >
              {busy ? "Detecting…" : "Detect melody"}
            </Button>
            <p className="small subtle">
              {mode === "prototype"
                ? "This demonstration uses sample notes."
                : "The local engine will suggest notes for you to review."}
            </p>
            {!project.available && (
              <p role="status">
                The local engine is unavailable. Your original is still
                playable.
              </p>
            )}
          </div>
        ) : editing ? (
          <MelodyEditor
            key={`${project.id}.${project.sourceRevision}.${project.noteRevision}.${staleEdits}`}
            notes={notes}
            duration={project.tracks[0].duration}
            onChange={changeNotes}
            detailsTargetId={!compact ? "melody-note-details" : undefined}
          />
        ) : (
          <>
            <div className="notes-preview" aria-label="Detected melody notes">
              {notes.map((n, i) => (
                <span
                  key={i}
                  style={{
                    left: `${(n.start / project.tracks[0].duration) * 100}%`,
                    width: `${Math.max(1, ((n.end - n.start) / project.tracks[0].duration) * 100)}%`,
                    top: `${80 - (n.pitch - 48) * 3}%`,
                  }}
                />
              ))}
            </div>
            <p className="small subtle">
              {notes.length} notes ·{" "}
              {dirty
                ? "Unsaved edits — review before confirming"
                : project.confirmed
                  ? "Confirmed melody"
                  : "Suggested notes — review before confirming"}
            </p>
            <Button variant="outline" asChild>
              <Link to={`/remix/${project.id}/notes`}>
                <SlidersHorizontal />
                Edit notes
              </Link>
            </Button>
            <p className="small subtle">
              Optional — only if something sounds wrong.
            </p>
          </>
        )}
        {project.notes.length > 0 && (
          <div className="melody-bottom">
            <ListeningChoices
              project={{ ...project, tracks: [project.tracks[0]] }}
              preview={preview}
            />
            <Button
              variant="outline"
              disabled={!!busy || !project.available || staleEdits}
              onClick={() => void previewNotes()}
            >
              <Play />
              Preview melody
            </Button>
          </div>
        )}
      </section>
      <aside className="context-column melody-context">
        <h2>
          {editing
            ? !compact
              ? "Note details"
              : "Finish editing"
            : "Your melody, your call."}
        </h2>
        {editing && !compact && <div id="melody-note-details" />}
        <p className="subtle">
          {editing
            ? "Return to melody review to listen and confirm."
            : "Detection is a starting point. Your confirmed notes guide every new arrangement."}
        </p>
        {project.notes.length > 0 &&
          (editing ? (
            <>
              <Button
                className="full"
                disabled={!!busy || staleEdits}
                onClick={() =>
                  void run(
                    "Saving notes…",
                    (signal) => saveNotes(false, signal),
                    () => navigate(`/remix/${project.id}/melody`),
                  )
                }
              >
                Done editing
              </Button>
              <Button
                variant="ghost"
                className="full accent"
                disabled={!!busy}
                onClick={() => {
                  discardEdits();
                  navigate(`/remix/${project.id}/melody`);
                }}
              >
                Discard edits
              </Button>
            </>
          ) : (
            <>
              <Button
                className="full"
                disabled={!!busy || staleEdits}
                onClick={() =>
                  void run(
                    "Confirming melody…",
                    (signal) => saveNotes(true, signal),
                    () => navigate(`/remix/${project.id}`),
                  )
                }
              >
                <Check />
                Use this melody
              </Button>
              <p className="small subtle">
                You can review the notes again later. Previous audio versions
                remain unchanged.
              </p>
            </>
          ))}
        <div className="melody-tip">
          <Music2 size={20} />
          <h3>Listen for the shape.</h3>
          <p className="subtle">
            Compare pitch and timing. It doesn’t need to sound like the original
            instrument.
          </p>
        </div>
      </aside>
    </div>
  );
}

function CreationRecovery({
  project,
  onUpdate,
}: {
  project: Project;
  onUpdate: (p: Project) => void;
}) {
  const [pending, setPending] = useState<{
      projectId: string;
      text: string;
      basisId: string | null;
    } | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    void pendingBackendCreation()
      .then(setPending)
      .catch(() => setError("Could not read pending creation recovery."));
  }, [project.id]);
  if (!pending || pending.projectId !== project.id)
    return error ? (
      <p role="alert" className="notice error">
        {error}
      </p>
    ) : null;
  return (
    <div className="notice recovery-notice">
      <div>
        <strong>A previous creation needs confirmation.</strong>
        <p>{pending.text}</p>
        <p className="small">
          Recover the exact saved request without creating a duplicate.
        </p>
        {error && <p role="alert">{error}</p>}
      </div>
      <Button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            onUpdate(await recoverBackendCreation(project.id));
            setPending(null);
          } catch (e) {
            setError(
              e instanceof Error ? e.message : "Could not recover creation.",
            );
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Recovering…" : "Recover previous creation"}
      </Button>
    </div>
  );
}

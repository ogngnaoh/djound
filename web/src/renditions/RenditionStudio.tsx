import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftRight,
  ChevronDown,
  Play,
  Scan,
  Sparkles,
  Star,
  Upload,
} from "lucide-react";
import { useLocation, useParams, useSearchParams } from "react-router-dom";
import { Button } from "../components/ui/button";
import { readLocal, writeLocal } from "../recovery";
import { useAudio, clock } from "../studio/Player";
import { Loading } from "../studio/Views";
import {
  projectKey,
  recordingList,
  RenditionError,
  readProject,
  renditionRequest,
  type RecordingProject,
  type Rendition,
  type RenditionAnswer,
  type RenditionCommand,
} from "./api";
import { useRecoverableRequest } from "./useRecoverableRequest";
import { RestoreRecording } from "./RestoreRecording";
import { LibraryReturnLink } from "./libraryNavigation";
import { correspondingPosition, passageAuditionStart } from "./songTime";
import { TakeHistory } from "./TakeHistory";
import { TakeDetails } from "./TakeDetails";
import { RecordingWaveform } from "./RecordingWaveform";
import { RecordingArtwork, recordingName } from "./recordingPresentation";
import "./renditions.css";

const styles = [
  ["Jazz Trio", "Piano, bass, and drums"],
  ["Solo Piano", "Melody and accompaniment on one piano"],
  ["Lo-fi Hip-hop", "A laid-back beat and soft keys"],
  ["Cinematic Strings", "A string ensemble carries the tune"],
];
type TakeRequest = Omit<RenditionCommand, "requestId"> & { key: string };
type AnswerRequest = Omit<RenditionAnswer, "requestId"> & {
  key: string;
  questionId: string;
};
const answerLabels = {
  keep: "Keep it as recorded",
  develop: "Apply my change there too",
} satisfies Record<RenditionAnswer["choice"], string>;
type Passage = { start: string; end: string };
type Revision = { text: string; range: Passage | null };
type LinkChoice = { requested: string; fallback: string; audioError?: boolean };
const inProgress = (take: Rendition) =>
  take.state === "queued" || take.state === "running";
const progressText = (take: Rendition) =>
  take.state === "queued" ? "Queued" : take.progress || "Making take…";

function linkStatus(take: Rendition | undefined, audioError = false) {
  if (audioError) return "The linked take could not be played.";
  if (take && inProgress(take)) return "The linked take is still being made.";
  if (take?.state === "clarify") return "The linked take needs more direction.";
  if (take?.state === "failed") return "The linked take did not finish.";
  if (take?.state === "ready" && take.audioUrl)
    return "The linked take is ready.";
  return "The linked take is unavailable.";
}

function PassageChoices({
  question,
  disabled,
  onAnswer,
}: {
  question: Rendition;
  disabled: boolean;
  onAnswer: (question: Rendition, choice: RenditionAnswer["choice"]) => void;
}) {
  return (
    <div
      className="rendition-answer-options"
      role="group"
      aria-label="Choose what happens to the edited passage"
    >
      {(["keep", "develop"] as const).map((choice) => (
        <Button
          key={choice}
          type="button"
          size="sm"
          variant="outline"
          disabled={disabled}
          onClick={() => onAnswer(question, choice)}
        >
          {answerLabels[choice]}
        </Button>
      ))}
    </div>
  );
}

export default function RenditionStudio() {
  const { id = "" } = useParams();
  const query = useQuery({
    queryKey: projectKey(id),
    queryFn: ({ signal }) => readProject(id, signal),
    refetchInterval: (q) =>
      q.state.data?.takes.some(inProgress) ? 1500 : false,
  });
  if (
    query.error instanceof RenditionError &&
    query.error.code === "RENDITION_PROJECT_TRASHED"
  )
    return <RestoreRecording id={id} />;
  if (!query.data)
    return query.isError ? (
      <section className="standalone">
        <h1>
          {query.error instanceof RenditionError && query.error.status === 404
            ? "Recording unavailable"
            : "Couldn’t open this recording"}
        </h1>
        <p role="alert">{query.error.message}</p>
        <Button onClick={() => void query.refetch()}>Try again</Button>
        <LibraryReturnLink />
      </section>
    ) : (
      <Loading />
    );
  return (
    <RecordingSession
      key={id}
      project={query.data}
      refreshError={query.error?.message}
      refresh={() => void query.refetch()}
    />
  );
}

function RecordingSession({
  project,
  refreshError,
  refresh,
}: {
  project: RecordingProject;
  refreshError?: string;
  refresh: () => void;
}) {
  const client = useQueryClient();
  // A worker can finish while the listener is comparing a different take.
  const finishedCount = project.takes.filter((t) => !inProgress(t)).length;
  const previousFinishedCount = useRef(finishedCount);
  useEffect(() => {
    if (previousFinishedCount.current !== finishedCount) {
      previousFinishedCount.current = finishedCount;
      void client.invalidateQueries({ queryKey: recordingList.queryKey });
    }
  }, [client, finishedCount]);
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const player = useRef<HTMLAudioElement>(null);
  const takeTitle = useRef<HTMLHeadingElement>(null);
  const direction = useRef<HTMLTextAreaElement>(null);
  const seekOnLoad = useRef<number | null>(null);
  const lastPosition = useRef(new Map<string, number>());
  const stopAt = useRef<number | null>(null);
  const clearLegacyPlayer = useAudio().clear;
  const [text, setText] = useState("");
  const [range, setRange] = useState<Passage | null>(null);
  const [revising, setRevising] = useState<Rendition | null>(null);
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [previousId, setPreviousId] = useState("");
  const [lastRequest, setLastRequest] = useState("");
  const [audition, setAudition] = useState<{
    workingId: string;
    referenceId: string;
  } | null>(null);
  const requestedId = params.get("take");
  const linked = project.takes.find((take) => take.id === requestedId);
  const linkedReady = linked?.state === "ready" && !!linked.audioUrl;
  const favoriteId = project.takes.find(
    (take) =>
      take.id === project.favoriteId && take.state === "ready" && take.audioUrl,
  )?.id;
  const [linkChoice, setLinkChoice] = useState<LinkChoice | null>(null);
  // A queued link may become ready while its listener is auditioning the fallback.
  // Keep that audio and editing choice stable until they choose which take to open.
  const recovery =
    linkChoice && linkChoice.requested === requestedId
      ? linkChoice
      : requestedId !== null && requestedId !== "original" && !linkedReady
        ? { requested: requestedId, fallback: favoriteId ?? "original" }
        : null;
  useEffect(() => {
    if (recovery?.requested !== linkChoice?.requested) setLinkChoice(recovery);
  }, [recovery, linkChoice]);
  const wanted = recovery?.fallback ?? requestedId ?? favoriteId ?? "original";
  const selected = project.takes.find(
    (t) => t.id === wanted && t.state === "ready" && t.audioUrl,
  );
  const activeId = selected?.id ?? "original";
  const heardId =
    audition?.workingId === activeId ? audition.referenceId : activeId;
  const heard = project.takes.find((take) => take.id === heardId);
  const heardUrl = heard?.audioUrl ?? project.originalUrl;
  const duration = heard?.durationSeconds ?? project.durationSeconds;
  const label = (id: string) => {
    const index = project.takes.findIndex((t) => t.id === id);
    if (index < 0) return id === "original" ? "Original" : "Earlier take";
    return `Take ${index + 1} · ${project.takes[index].label}`;
  };
  const activeName = label(activeId);
  const fallbackName =
    selected && selected.id === project.favoriteId
      ? "your favorite, " + activeName
      : activeName;
  const parentId = activeId;
  const parentTake = selected ?? null;
  const parentDuration = parentTake?.durationSeconds ?? project.durationSeconds;
  const comparisonTakes = project.takes.filter(
    (take) =>
      (take.id === previousId || take.id === selected?.parentId) &&
      take.id !== activeId &&
      take.state === "ready" &&
      take.audioUrl,
  );
  const newestChild = project.takes
    .filter((t) => t.parentId === activeId)
    .at(-1);
  const latest =
    project.takes.find((t) => t.requestId === lastRequest) ??
    (newestChild &&
    (newestChild.state === "clarify" ||
      inProgress(newestChild) ||
      newestChild.answerTo)
      ? newestChild
      : undefined);
  const revisionKey = (id: string) =>
    "rendition-revision:" + project.id + ":" + id;
  useEffect(() => {
    if (revising)
      writeLocal("rendition-revision:" + project.id + ":" + revising.id, {
        text,
        range,
      });
  }, [project.id, revising, text, range]);
  const creation = useRecoverableRequest(
    "rendition-take:" + project.id,
    async (cmd: TakeRequest | AnswerRequest) => {
      const { key: requestId, ...body } = cmd;
      const route = "/" + project.id + "/takes";
      const answering = "questionId" in body;
      await renditionRequest(
        answering ? route + "/" + body.questionId + "/answer" : route,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            answering
              ? { requestId, choice: body.choice }
              : { requestId, ...body },
          ),
        },
      );
      setLastRequest(requestId);
      void client.invalidateQueries({ queryKey: recordingList.queryKey });
      if ("prompt" in cmd) {
        setRevising(null);
        setText((previous) => (previous.trim() === cmd.prompt ? "" : previous));
      }
      await client.invalidateQueries(
        { queryKey: projectKey(project.id) },
        { throwOnError: true },
      );
    },
  );

  useEffect(() => {
    clearLegacyPlayer();
    const audio = player.current;
    return () => audio?.pause();
  }, [clearLegacyPlayer]);
  useEffect(() => {
    setAudition((current) =>
      current && current.workingId !== activeId ? null : current,
    );
  }, [activeId]);
  useEffect(() => {
    const audio = player.current;
    if (audio && audio.getAttribute("src") !== heardUrl) {
      seekOnLoad.current = null;
      audio.src = heardUrl;
    }
  }, [heardUrl]);

  function positionIn(target: Rendition | null) {
    const audio = player.current;
    const mediaUrl = audio?.getAttribute("src");
    const current = project.takes.find(
      (t) => t.audioUrl && t.audioUrl === mediaUrl,
    );
    const seconds = seekOnLoad.current ?? audio?.currentTime ?? 0;
    return correspondingPosition(
      seconds,
      current?.songTimeMap,
      target?.songTimeMap,
      lastPosition.current.get(target?.id ?? "original"),
    );
  }

  function switchAudio(
    take: Rendition | null,
    from?: number,
    startPlayback = true,
  ) {
    const audio = player.current;
    const url = take ? take.audioUrl : project.originalUrl;
    if (!audio || !url) return;
    const same = audio.getAttribute("src") === url;
    if (!same)
      lastPosition.current.set(
        heardId,
        seekOnLoad.current ?? audio.currentTime,
      );
    const position = from ?? (audio.ended ? 0 : positionIn(take));
    stopAt.current = null;
    setNotice("");
    if (!same) {
      seekOnLoad.current = position;
      audio.src = url;
    } else if (from !== undefined) {
      if (Number.isFinite(audio.duration))
        audio.currentTime = Math.max(0, Math.min(from, audio.duration));
      else seekOnLoad.current = from;
    }
    if (!startPlayback) return;
    void audio.play().catch((error: Error) => {
      if (
        error.name !== "AbortError" &&
        player.current?.getAttribute("src") === url
      ) {
        setNotice(
          "Playback did not start. Press Play in the audio player to try again.",
        );
      }
    });
  }
  function chooseTake(
    take: Rendition | null,
    from?: number,
    startPlayback = true,
  ) {
    const id = take?.id ?? "original";
    if (id === requestedId && recovery) setLinkChoice(null);
    if (id !== activeId) setPreviousId(activeId);
    if (id !== activeId || (recovery && id !== requestedId)) {
      setRange(null);
      setText("");
      setRevising(null);
    }
    // Keep a sounding reference stable while the router commits a different
    // working take. Clearing it early briefly reloads the previous working WAV.
    if (id === activeId) setAudition(null);
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.set("take", id);
        return next;
      },
      { replace: true, state: location.state },
    );
    switchAudio(take, from, startPlayback);
  }
  function compare(take: Rendition | null) {
    const referenceId = take?.id ?? "original";
    const returnToWorking = heardId === referenceId;
    const target = returnToWorking ? (selected ?? null) : take;
    setAudition(returnToWorking ? null : { workingId: activeId, referenceId });
    switchAudio(target, positionIn(target), player.current?.paused === false);
  }
  function openFromLink(take: Rendition | null) {
    chooseTake(take, undefined, player.current?.paused === false);
  }
  function mediaFailed(audio: HTMLAudioElement) {
    if (!audio.error || audio.getAttribute("src") !== heardUrl) return;
    if (
      requestedId !== null &&
      heardId === activeId &&
      activeId !== "original" &&
      (recovery || activeId === requestedId)
    ) {
      // A ready row may point to a missing file. If even the favorite fails,
      // keep the saved link and offer Original instead of retrying in a loop.
      setLinkChoice({
        requested: requestedId,
        fallback:
          favoriteId && favoriteId !== activeId && favoriteId !== requestedId
            ? favoriteId
            : "original",
        audioError: activeId === requestedId || recovery?.audioError,
      });
      setNotice("");
      return;
    }
    setNotice("This audio could not be opened. Try playing it again.");
  }
  function passageAt(position: number, length: number): Passage {
    const start = Math.max(0, Math.min(Math.floor(position), length - 1));
    return { start: String(start), end: String(Math.min(start + 10, length)) };
  }
  function resumeRevision(take: Rendition, choosePassage = false) {
    const parent = project.takes.find((t) => t.id === take.parentId) ?? null;
    const saved = readLocal<Revision | null>(revisionKey(take.id), null);
    const anchor = readLocal<{ requestId: string; seconds: number } | null>(
      "rendition-cursor:" + project.id,
      null,
    );
    const position =
      !choosePassage && anchor?.requestId === take.requestId
        ? anchor.seconds
        : positionIn(parent);
    const bounds = saved
      ? saved.range
      : take.startSeconds != null && take.endSeconds != null
        ? { start: String(take.startSeconds), end: String(take.endSeconds) }
        : null;
    const next =
      bounds ??
      (choosePassage
        ? passageAt(
            position,
            parent?.durationSeconds ?? project.durationSeconds,
          )
        : null);
    chooseTake(
      parent,
      next ? passageAuditionStart(Number(next.start)) : position,
      player.current?.paused === false,
    );
    setRevising(take);
    setText(saved?.text ?? take.request);
    setRange(next);
    requestAnimationFrame(() => direction.current?.focus());
  }
  const start = Number(range?.start);
  const end = Number(range?.end);
  const validRange =
    !!range?.start &&
    !!range.end &&
    Number.isFinite(start) &&
    Number.isFinite(end) &&
    start >= 0 &&
    end > start &&
    end <= parentDuration;
  function make(prompt: string) {
    if (!prompt.trim() || recovery) return;
    const bounds: Pick<TakeRequest, "startSeconds" | "endSeconds"> = {};
    if (range) {
      if (!validRange) {
        setNotice("Choose a start and end within this recording.");
        return;
      }
      bounds.startSeconds = start;
      bounds.endSeconds = end;
    }
    setNotice("");
    const key = crypto.randomUUID();
    writeLocal("rendition-cursor:" + project.id, {
      requestId: key,
      seconds: positionIn(parentTake),
    });
    void creation.submit({
      key,
      parentId,
      prompt: prompt.trim(),
      ...bounds,
    });
  }
  function answer(question: Rendition, choice: RenditionAnswer["choice"]) {
    setNotice("");
    void creation.submit({
      key: crypto.randomUUID(),
      questionId: question.id,
      choice,
    });
  }
  async function favorite() {
    if (!selected || saving) return;
    setSaving(true);
    setNotice("");
    try {
      await renditionRequest("/" + project.id + "/favorite", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ takeId: selected.id }),
      });
      void client.invalidateQueries({ queryKey: recordingList.queryKey });
      await client.invalidateQueries(
        { queryKey: projectKey(project.id) },
        { throwOnError: true },
      );
    } catch (e) {
      setNotice(
        e instanceof Error ? e.message : "Could not keep this favorite.",
      );
    } finally {
      setSaving(false);
    }
  }
  const locked = creation.busy || !!creation.pending || !!recovery;
  return (
    <section className="take-explorer" data-working-take-id={activeId}>
      <div className="explorer-main">
        <header className="explorer-hero">
          <RecordingArtwork
            id={project.id}
            title={project.title}
            className="explorer-art"
          />
          <div className="explorer-identity">
            <h1>{recordingName(project.title)}</h1>
            <div className="explorer-take-name">
              <h2 ref={takeTitle} tabIndex={-1}>
                {activeName}
              </h2>
              {selected && (
                <button
                  className="explorer-favorite"
                  data-kept={activeId === project.favoriteId}
                  disabled={saving || activeId === project.favoriteId}
                  aria-label={
                    activeId === project.favoriteId
                      ? "Kept as favorite"
                      : "Keep " + activeName + " as favorite"
                  }
                  onClick={() => void favorite()}
                >
                  <Star aria-hidden="true" />
                  {activeId === project.favoriteId
                    ? "Favorite"
                    : "Keep favorite"}
                </button>
              )}
            </div>
            <p>
              {selected?.request ??
                project.title + " · " + clock(project.durationSeconds)}
            </p>
          </div>
          <div className="explorer-hero-actions">
            {selected?.exportUrl && (
              <a
                className="explorer-button"
                href={selected.exportUrl}
                download
                aria-label={"Download " + activeName + " as WAV"}
              >
                <Upload aria-hidden="true" />
                Export
              </a>
            )}
            <TakeDetails
              project={project}
              take={selected}
              parent={label(selected?.parentId ?? "original")}
            />
          </div>
        </header>
        {recovery && (
          <div className="explorer-reply">
            <p role="status">
              {linkStatus(linked, recovery.audioError)} You can listen to{" "}
              {fallbackName}. Choose a take to edit.
            </p>
            <Button
              variant="outline"
              onClick={() => openFromLink(selected ?? null)}
            >
              Use {activeName}
            </Button>
            {linkedReady && (
              <Button variant="outline" onClick={() => openFromLink(linked)}>
                {recovery.audioError ? "Retry" : "Open"} {label(linked.id)}
              </Button>
            )}
          </div>
        )}
        <audio
          ref={player}
          controls
          preload="metadata"
          aria-label={"Listen to " + label(heardId)}
          data-take-id={heardId}
          onError={(event) => mediaFailed(event.currentTarget)}
          onTimeUpdate={(e) => {
            if (
              stopAt.current !== null &&
              e.currentTarget.currentTime >= stopAt.current
            ) {
              e.currentTarget.pause();
              stopAt.current = null;
            }
          }}
          onLoadedMetadata={(e) => {
            const position = seekOnLoad.current;
            if (position !== null) {
              const a = e.currentTarget;
              a.currentTime = Math.max(
                0,
                Math.min(position, Math.max(0, a.duration - 0.1)),
              );
              seekOnLoad.current = null;
            }
          }}
        />
        <RecordingWaveform
          audioRef={player}
          recordingId={project.id}
          takeId={heardId}
          audioUrl={heardUrl}
          duration={duration}
          label={label(heardId)}
          onSeek={() => {
            stopAt.current = null;
          }}
        />
        {(selected || comparisonTakes.length > 0) && (
          <div
            className="explorer-comparison"
            role="group"
            aria-label="Compare takes"
          >
            <span className="explorer-compare-label">
              <ArrowLeftRight aria-hidden="true" />
              Compare
            </span>
            {selected && (
              <button
                type="button"
                className="explorer-button"
                aria-pressed={heardId === "original"}
                aria-label={
                  heardId === "original"
                    ? "Return to " + activeName
                    : "Compare original"
                }
                onClick={() => compare(null)}
              >
                <Play aria-hidden="true" />
                Original
              </button>
            )}
            <button
              type="button"
              className="explorer-button"
              aria-pressed={heardId === activeId}
              aria-label={"Listen to working take " + activeName}
              onClick={() => {
                setAudition(null);
                switchAudio(
                  selected ?? null,
                  positionIn(selected ?? null),
                  player.current?.paused === false,
                );
              }}
            >
              <Play aria-hidden="true" />
              {selected?.label ?? "Original"}
            </button>
            {comparisonTakes.map((take) => (
              <button
                type="button"
                key={take.id}
                className="explorer-compare-parent"
                aria-pressed={heardId === take.id}
                aria-label={
                  heardId === take.id
                    ? "Return to " + activeName
                    : "Compare " + label(take.id)
                }
                onClick={() => compare(take)}
              >
                <ArrowLeftRight aria-hidden="true" />
                <span>{take.label}</span>
              </button>
            ))}
          </div>
        )}
        {heardId !== activeId && (
          <p className="explorer-audition" role="status">
            Listening to {label(heardId)} · Editing {activeName}
          </p>
        )}
        <section className="explorer-composer">
          <h2 aria-label={"Make a version of " + activeName}>
            {selected ? "Edit " : "Reimagine "}
            {activeName}
          </h2>
          {revising && (
            <div className="explorer-reply">
              <p>{revising.error}</p>
              <button
                className="text-action"
                onClick={() => {
                  setRevising(null);
                  setRange(null);
                  setText("");
                }}
              >
                Start a new request
              </button>
            </div>
          )}
          {!selected && (
            <div
              className="explorer-styles"
              role="group"
              aria-label="Suggested sounds"
            >
              {styles.map(([style, description]) => (
                <button
                  type="button"
                  className="explorer-button"
                  key={style}
                  title={description}
                  disabled={locked}
                  aria-pressed={text === style}
                  onClick={() => {
                    setText(style);
                    direction.current?.focus();
                  }}
                >
                  {style}
                </button>
              ))}
            </div>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              make(text);
            }}
          >
            <textarea
              ref={direction}
              maxLength={1000}
              disabled={!!recovery}
              aria-label="Describe a version or change"
              placeholder={
                selected
                  ? "What would you like to keep or change?"
                  : "Describe the sound you want for this recording…"
              }
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            <div className="explorer-composer-actions">
              <button
                className="explorer-button"
                type="button"
                disabled={!!recovery}
                aria-expanded={!!range}
                onClick={() => {
                  if (range) setRange(null);
                  else if (
                    !revising &&
                    !text.trim() &&
                    latest?.state === "clarify" &&
                    latest.parentId === activeId
                  )
                    resumeRevision(latest, true);
                  else {
                    setRange(passageAt(positionIn(parentTake), parentDuration));
                  }
                }}
              >
                <Scan aria-hidden="true" />
                {range ? "Use whole recording" : "Choose a passage"}
                <ChevronDown aria-hidden="true" />
              </button>
              <button
                className="explorer-make"
                aria-label="Make a take"
                disabled={!text.trim() || locked}
              >
                <Sparkles aria-hidden="true" />
                Make take
              </button>
            </div>
            {range && (
              <fieldset className="explorer-range">
                <legend className="sr-only">Selected passage</legend>
                <label>
                  Start (s)
                  <input
                    aria-label="Passage start in seconds"
                    type="number"
                    min="0"
                    max={parentDuration}
                    step="any"
                    value={range.start}
                    onChange={(e) =>
                      setRange({ ...range, start: e.target.value })
                    }
                  />
                </label>
                <label>
                  End (s)
                  <input
                    aria-label="Passage end in seconds"
                    type="number"
                    min="0"
                    max={parentDuration}
                    step="any"
                    value={range.end}
                    onChange={(e) =>
                      setRange({ ...range, end: e.target.value })
                    }
                  />
                </label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!validRange}
                  onClick={() => {
                    setAudition(null);
                    switchAudio(parentTake, start);
                    stopAt.current = end;
                  }}
                >
                  <Play /> Play passage
                </Button>
              </fieldset>
            )}
          </form>
          {creation.pending && !creation.busy && (
            <Button
              variant="outline"
              onClick={() => void creation.submit(creation.pending!)}
            >
              Resume{" "}
              {"questionId" in creation.pending
                ? "answer · " + answerLabels[creation.pending.choice]
                : "request · " + creation.pending.prompt}
            </Button>
          )}
          {latest && latest.id !== revising?.id && (
            <div className="explorer-status" role="status">
              {latest.state === "ready" ? (
                <Button
                  variant="secondary"
                  onClick={() =>
                    chooseTake(
                      latest,
                      passageAuditionStart(
                        latest.appliedStartSeconds ?? latest.startSeconds,
                      ),
                    )
                  }
                >
                  <Play /> Play new take · {latest.label}
                </Button>
              ) : inProgress(latest) ? (
                latest.parentId === activeId ? (
                  progressText(latest)
                ) : (
                  progressText(latest) + " · From " + label(latest.parentId)
                )
              ) : (
                <>
                  <p>
                    {latest.error || latest.caption || "Try another direction."}
                  </p>
                  {latest.state === "clarify" &&
                    latest.clarificationKind === "retained_passage" && (
                      <>
                        <p>
                          <q>{latest.request}</q>
                          {latest.parentId !== activeId &&
                            " · From " + label(latest.parentId)}
                        </p>
                        <PassageChoices
                          question={latest}
                          disabled={locked}
                          onAnswer={answer}
                        />
                      </>
                    )}
                  {latest.state === "clarify" && (
                    <button
                      className="text-action"
                      onClick={() => resumeRevision(latest)}
                    >
                      {latest.clarificationKind === "retained_passage"
                        ? "Reword request"
                        : "Continue this request"}
                    </button>
                  )}
                </>
              )}
            </div>
          )}
        </section>
        {(notice || creation.error || refreshError) && (
          <p role="alert" className="explorer-error">
            {notice || creation.error || refreshError}{" "}
            {refreshError && (
              <button className="text-action" onClick={refresh}>
                Refresh
              </button>
            )}
          </p>
        )}
      </div>
      <TakeHistory
        project={project}
        activeId={activeId}
        choose={(take) => {
          chooseTake(take);
          if (window.matchMedia("(max-width: 820px)").matches)
            requestAnimationFrame(() => takeTitle.current?.focus());
        }}
        describe={label}
        pendingActions={(take) => (
          <>
            <p>
              {take.error ||
                (take.state === "clarify"
                  ? "Try a more specific direction."
                  : "Could not finish this take.")}
            </p>
            {take.clarificationKind === "retained_passage" &&
              (latest?.id !== take.id || latest.id === revising?.id) && (
                <details>
                  <summary>Answer question</summary>
                  <PassageChoices
                    question={take}
                    disabled={locked}
                    onAnswer={answer}
                  />
                </details>
              )}
            <button
              type="button"
              className="explorer-text-action"
              onClick={() => resumeRevision(take)}
            >
              Revise request
            </button>
          </>
        )}
      />
    </section>
  );
}

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bookmark, Check, Download, Play } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Button } from "../components/ui/button";
import { readLocal, writeLocal } from "../recovery";
import { useAudio, clock } from "../studio/Player";
import { Failure, Loading } from "../studio/Views";
import {
  projectKey,
  recordingList,
  readProject,
  renditionRequest,
  type RecordingProject,
  type Rendition,
  type RenditionAnswer,
  type RenditionCommand,
} from "./api";
import { useRecoverableRequest } from "./useRecoverableRequest";
import { correspondingPosition, passageAuditionStart } from "./songTime";
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
const inProgress = (take: Rendition) =>
  take.state === "queued" || take.state === "running";
const progressText = (take: Rendition) =>
  take.state === "queued" ? "Queued" : take.progress || "Making take…";

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
  if (!query.data)
    return query.isError ? (
      <Failure
        message={query.error.message}
        retry={() => void query.refetch()}
      />
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
  const player = useRef<HTMLAudioElement>(null);
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
  const wanted = params.get("take") || project.favoriteId || "original";
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
    if (id !== activeId) {
      setPreviousId(activeId);
      setRange(null);
      setText("");
      setRevising(null);
    } else {
      setAudition(null);
    }
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.set("take", id);
        return next;
      },
      { replace: true },
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
    direction.current?.focus();
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
    if (!prompt.trim()) return;
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
  const locked = creation.busy || !!creation.pending;
  return (
    <section
      className="mix-editor rendition-studio"
      data-working-take-id={activeId}
    >
      <header className="mix-heading">
        <Link to="/renditions">
          <ArrowLeft size={15} /> Your recordings
        </Link>
        <h1>{project.title}</h1>
      </header>
      <div className="mix-edit-grid">
        <div className="mix-main">
          <div className="mix-time">
            <strong>
              {label(heardId)}
              {heardId !== activeId && " · Comparing"}
            </strong>
            <span>{clock(duration)}</span>
          </div>
          <audio
            ref={player}
            controls
            preload="metadata"
            aria-label={"Listen to " + label(heardId)}
            data-take-id={heardId}
            onError={() =>
              setNotice("This audio could not be opened. Try playing it again.")
            }
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
          <div className="mix-save">
            {comparisonTakes.map((take) => (
              <Button
                key={take.id}
                size="sm"
                variant="outline"
                aria-pressed={heardId === take.id}
                onClick={() => compare(take)}
              >
                <Play />{" "}
                {heardId === take.id
                  ? "Return to " + activeName
                  : "Compare " + label(take.id)}
              </Button>
            ))}
            {selected && (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  aria-pressed={heardId === "original"}
                  onClick={() => compare(null)}
                >
                  <Play />{" "}
                  {heardId === "original"
                    ? "Return to " + activeName
                    : "Compare original"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={saving || project.favoriteId === activeId}
                  onClick={() => void favorite()}
                >
                  {project.favoriteId === activeId ? <Check /> : <Bookmark />}
                  {project.favoriteId === activeId
                    ? "Kept as favorite"
                    : heardId !== activeId
                      ? "Keep " + activeName + " as favorite"
                      : "Keep as favorite"}
                </Button>
                {selected.exportUrl && (
                  <Button asChild size="sm" variant="ghost">
                    <a href={selected.exportUrl} download>
                      <Download />{" "}
                      {heardId !== activeId
                        ? "Download " + activeName
                        : "Download this take"}
                    </a>
                  </Button>
                )}
              </>
            )}
          </div>
          <section className="mix-conversation">
            <h2>Make a version of {label(parentId)}</h2>
            {revising && (
              <div className="mix-reply">
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
            <div
              className="rendition-styles"
              role="group"
              aria-label="Suggested sounds"
            >
              {styles.map(([style, description]) => (
                <Button
                  key={style}
                  variant="outline"
                  disabled={locked}
                  onClick={() => make(style)}
                >
                  <strong>{style}</strong>
                  <span>{description}</span>
                </Button>
              ))}
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                make(text);
              }}
            >
              <textarea
                ref={direction}
                maxLength={1000}
                aria-label="Describe a version or change"
                placeholder={
                  selected
                    ? "What would you change about this take?"
                    : "Describe the sound you want…"
                }
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
              <div>
                <button
                  className="text-action"
                  type="button"
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
                      setRange(
                        passageAt(positionIn(parentTake), parentDuration),
                      );
                    }
                  }}
                >
                  {range ? "Use whole recording" : "Choose a passage"}
                </button>
                <Button disabled={!text.trim() || locked}>Make a take</Button>
              </div>
              {range && (
                <fieldset className="mix-range-fields">
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
              <div className="mix-status" role="status">
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
                      {latest.error ||
                        latest.caption ||
                        "Try another direction."}
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
            <p role="alert" className="notice error">
              {notice || creation.error || refreshError}{" "}
              {refreshError && (
                <button className="text-action" onClick={refresh}>
                  Refresh
                </button>
              )}
            </p>
          )}
        </div>
        <aside className="mix-takes">
          <h2>Recordings</h2>
          <article className={activeId === "original" ? "current" : ""}>
            <button
              onClick={() => chooseTake(null)}
              aria-current={activeId === "original" ? "true" : undefined}
              aria-label="Play original"
            >
              <Play size={16} /> Original
            </button>
          </article>
          {project.takes.map((take) => (
            <article
              key={take.id}
              className={activeId === take.id ? "current" : ""}
            >
              {take.audioUrl && take.state === "ready" ? (
                <button
                  onClick={() => chooseTake(take)}
                  aria-current={activeId === take.id ? "true" : undefined}
                  aria-label={"Play " + label(take.id)}
                >
                  <Play size={16} />
                  <strong>{label(take.id)}</strong>
                  {project.favoriteId === take.id && (
                    <Bookmark size={15} aria-label="Favorite" />
                  )}
                </button>
              ) : (
                <strong>{label(take.id)}</strong>
              )}
              <small>From {label(take.parentId)}</small>
              <span>{take.request}</span>
              {take.caption && <small>{take.caption}</small>}
              {inProgress(take) && (
                <small role="status">{progressText(take)}</small>
              )}
              {(take.state === "failed" || take.state === "clarify") && (
                <>
                  <small>
                    {take.error ||
                      (take.state === "clarify"
                        ? "Try a more specific direction."
                        : "Could not finish this take.")}
                  </small>
                  {take.clarificationKind === "retained_passage" &&
                    (latest?.id !== take.id || latest.id === revising?.id) && (
                      <details>
                        <summary className="text-action">
                          Answer question
                        </summary>
                        <PassageChoices
                          question={take}
                          disabled={locked}
                          onAnswer={answer}
                        />
                      </details>
                    )}
                  <button
                    className="text-action"
                    onClick={() => resumeRevision(take)}
                  >
                    Revise request
                  </button>
                </>
              )}
            </article>
          ))}
        </aside>
      </div>
    </section>
  );
}

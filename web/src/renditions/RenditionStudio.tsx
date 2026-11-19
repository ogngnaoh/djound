import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bookmark, Check, Download, Play } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Button } from "../components/ui/button";
import { useAudio, clock } from "../studio/Player";
import { Failure, Loading } from "../studio/Views";
import {
  projectKey,
  readProject,
  renditionRequest,
  type RecordingProject,
  type Rendition,
  type RenditionCommand,
} from "./api";
import { useRecoverableRequest } from "./useRecoverableRequest";
import "./renditions.css";

const styles = [
  ["Jazz Trio", "Piano, bass, and drums"],
  ["Solo Piano", "Melody and accompaniment on one piano"],
  ["Lo-fi Hip-hop", "A laid-back beat and soft keys"],
  ["Cinematic Strings", "A string ensemble carries the tune"],
];
type TakeRequest = Omit<RenditionCommand, "requestId"> & { key: string };
const inProgress = (take: Rendition) =>
  take.state === "queued" || take.state === "running";

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
  const [params, setParams] = useSearchParams();
  const player = useRef<HTMLAudioElement>(null);
  const direction = useRef<HTMLTextAreaElement>(null);
  const seekOnLoad = useRef<number | null>(null);
  const clearLegacyPlayer = useAudio().clear;
  const [text, setText] = useState("");
  const [range, setRange] = useState<{ start: string; end: string } | null>(
    null,
  );
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [previousId, setPreviousId] = useState("");
  const [lastRequest, setLastRequest] = useState("");
  const wanted = params.get("take") || project.favoriteId || "original";
  const selected = project.takes.find(
    (t) => t.id === wanted && t.state === "ready" && t.audioUrl,
  );
  const activeId = selected?.id ?? "original";
  const activeUrl = selected?.audioUrl ?? project.originalUrl;
  const duration = selected?.durationSeconds ?? project.durationSeconds;
  const label = (id: string) => {
    const index = project.takes.findIndex((t) => t.id === id);
    if (index < 0) return id === "original" ? "Original" : "Earlier take";
    return `Take ${index + 1} · ${project.takes[index].label}`;
  };
  const activeName = label(activeId);
  const previousTake = project.takes.find(
    (take) =>
      take.id === previousId && take.id !== activeId && take.state === "ready",
  );
  const latest = project.takes.find((t) => t.requestId === lastRequest);
  const creation = useRecoverableRequest(
    "rendition-take:" + project.id,
    async (cmd: TakeRequest) => {
      const { key: requestId, ...body } = cmd;
      await renditionRequest("/" + project.id + "/takes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requestId, ...body }),
      });
      setLastRequest(requestId);
      setText((previous) => (previous.trim() === cmd.prompt ? "" : previous));
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
    const audio = player.current;
    if (audio && audio.getAttribute("src") !== activeUrl) {
      seekOnLoad.current = null;
      audio.src = activeUrl;
    }
  }, [activeUrl]);

  function play(take: Rendition | null, from?: number) {
    const audio = player.current;
    const url = take ? take.audioUrl : project.originalUrl;
    if (!audio || !url) return;
    const same = audio.getAttribute("src") === url;
    const position = from ?? (audio.ended ? 0 : audio.currentTime);
    setNotice("");
    setRange(null);
    if (activeId !== (take?.id ?? "original")) setPreviousId(activeId);
    setParams({ take: take?.id ?? "original" }, { replace: true });
    if (!same) {
      seekOnLoad.current = position;
      audio.src = url;
    } else if (from !== undefined) {
      audio.currentTime = Math.max(0, Math.min(from, audio.duration || 0));
    }
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
  function make(prompt: string) {
    if (!prompt.trim()) return;
    const bounds: Pick<TakeRequest, "startSeconds" | "endSeconds"> = {};
    if (range) {
      const start = Number(range.start);
      const end = Number(range.end);
      if (
        !range.start ||
        !range.end ||
        !Number.isFinite(start) ||
        !Number.isFinite(end) ||
        start < 0 ||
        end <= start ||
        end > duration
      ) {
        setNotice("Choose a start and end within this recording.");
        return;
      }
      bounds.startSeconds = start;
      bounds.endSeconds = end;
    }
    setNotice("");
    void creation.submit({
      key: crypto.randomUUID(),
      parentId: activeId,
      prompt: prompt.trim(),
      ...bounds,
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
    <section className="mix-editor rendition-studio">
      <header className="mix-heading">
        <Link to="/renditions">
          <ArrowLeft size={15} /> Your recordings
        </Link>
        <h1>{project.title}</h1>
      </header>
      <div className="mix-edit-grid">
        <div className="mix-main">
          <div className="mix-time">
            <strong>{activeName}</strong>
            <span>{clock(duration)}</span>
          </div>
          <audio
            ref={player}
            controls
            preload="metadata"
            aria-label={"Listen to " + activeName}
            data-take-id={activeId}
            onError={() =>
              setNotice("This audio could not be opened. Try playing it again.")
            }
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
            {previousTake && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => play(previousTake)}
              >
                <Play /> Compare {label(previousTake.id)}
              </Button>
            )}
            {selected && (
              <>
                <Button size="sm" variant="outline" onClick={() => play(null)}>
                  <Play /> Compare original
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
                    : "Keep as favorite"}
                </Button>
                {selected.exportUrl && (
                  <Button asChild size="sm" variant="ghost">
                    <a href={selected.exportUrl} download>
                      <Download /> Download this take
                    </a>
                  </Button>
                )}
              </>
            )}
          </div>
          <section className="mix-conversation">
            <h2>Make a version of {activeName}</h2>
            <div className="rendition-styles">
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
                    : "Or describe the sound you want…"
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
                    else {
                      const start = Math.max(
                        0,
                        Math.min(
                          Math.floor(player.current?.currentTime || 0),
                          duration - 1,
                        ),
                      );
                      setRange({
                        start: String(start),
                        end: String(Math.min(start + 10, duration)),
                      });
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
                      max={duration}
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
                      max={duration}
                      step="any"
                      value={range.end}
                      onChange={(e) =>
                        setRange({ ...range, end: e.target.value })
                      }
                    />
                  </label>
                </fieldset>
              )}
            </form>
            {creation.pending && !creation.busy && (
              <Button
                variant="outline"
                onClick={() => void creation.submit(creation.pending!)}
              >
                Resume request · {creation.pending.prompt}
              </Button>
            )}
            {latest && (
              <div className="mix-status" role="status">
                {latest.state === "ready" ? (
                  <Button
                    variant="secondary"
                    onClick={() =>
                      play(
                        latest,
                        Math.max(
                          0,
                          (latest.appliedStartSeconds ??
                            latest.startSeconds ??
                            0) - 2,
                        ),
                      )
                    }
                  >
                    <Play /> Play new take · {latest.label}
                  </Button>
                ) : inProgress(latest) ? (
                  `Making ${latest.label}…`
                ) : (
                  latest.error || latest.caption || "Try another direction."
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
            <button onClick={() => play(null)} aria-label="Play original">
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
                  onClick={() => play(take)}
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
                <small role="status">
                  {take.state === "queued" ? "Queued" : "Making take…"}
                </small>
              )}
              {(take.state === "failed" || take.state === "clarify") && (
                <>
                  <small>
                    {take.error ||
                      (take.state === "clarify"
                        ? "Try a more specific direction."
                        : "Could not finish this take.")}
                  </small>
                  <button
                    className="text-action"
                    onClick={() => {
                      setParams({ take: take.parentId }, { replace: true });
                      setText(take.request);
                      setRange(
                        take.startSeconds != null && take.endSeconds != null
                          ? {
                              start: String(take.startSeconds),
                              end: String(take.endSeconds),
                            }
                          : null,
                      );
                      setNotice("");
                      direction.current?.focus();
                    }}
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

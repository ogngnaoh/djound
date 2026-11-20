import { useState, type ReactNode } from "react";
import {
  AudioLines,
  ChevronDown,
  CircleHelp,
  Clock3,
  Heart,
  ListMusic,
  Play,
} from "lucide-react";
import { clock } from "../studio/Player";
import type { RecordingProject, Rendition } from "./api";

export function TakeHistory({
  project,
  activeId,
  choose,
  describe,
  pendingActions,
}: {
  project: RecordingProject;
  activeId: string;
  choose: (take: Rendition | null) => void;
  describe: (id: string) => string;
  pendingActions: (take: Rendition) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const readyCount = project.takes.filter(
    (t) => t.state === "ready" && t.audioUrl,
  ).length;
  const count = readyCount === 1 ? "1 take" : readyCount + " takes";
  return (
    <aside
      className="take-history"
      aria-label="Take history"
      data-expanded={expanded}
    >
      <header>
        <div className="take-history-title">
          <h2>Takes</h2>
          <p>
            {count}
            <span>·</span>
            Original {clock(project.durationSeconds)}
          </p>
        </div>
        <button
          className="take-history-toggle"
          type="button"
          aria-expanded={expanded}
          aria-controls="take-timeline"
          onClick={() => setExpanded(!expanded)}
        >
          <ListMusic aria-hidden="true" />
          <strong>Take history</strong>
          <span>{count}</span>
          <ChevronDown aria-hidden="true" />
        </button>
      </header>
      <div id="take-timeline" className="take-timeline">
        {[null, ...project.takes].map((take, index) => {
          const id = take?.id ?? "original";
          const name = describe(id);
          const playable = !take || (!!take.audioUrl && take.state === "ready");
          const busy = take?.state === "running" || take?.state === "queued";
          const time = take ? take.durationSeconds : project.durationSeconds;
          const row = (
            <>
              <span className="take-history-icon" aria-hidden="true">
                {playable ? <Play /> : busy ? <Clock3 /> : <CircleHelp />}
              </span>
              <span className="take-history-copy">
                <span className="take-history-top">
                  <strong>
                    {take && (
                      <span className="take-index">
                        {String(index).padStart(2, "0")}
                      </span>
                    )}
                    {take?.label ?? "Original"}
                  </strong>
                  {time != null && <time>{clock(time)}</time>}
                  {take && project.favoriteId === id && (
                    <Heart aria-label="Favorite" className="take-heart" />
                  )}
                </span>
                <span className="take-request">
                  {take?.request || "The source recording"}
                </span>
              </span>
            </>
          );
          return (
            <article key={id} data-current={id === activeId} aria-label={name}>
              {playable ? (
                <button
                  type="button"
                  className="take-history-row"
                  aria-label={take ? "Play " + name : "Play original"}
                  aria-current={id === activeId ? "true" : undefined}
                  onClick={() => {
                    choose(take);
                    setExpanded(false);
                  }}
                >
                  {row}
                </button>
              ) : (
                <div className="take-history-row">{row}</div>
              )}
              {take && !playable && (
                <div className="take-history-status">
                  {busy ? (
                    <p role="status">
                      <AudioLines aria-hidden="true" />
                      {take.state === "queued"
                        ? "Queued"
                        : take.progress || "Making take…"}
                    </p>
                  ) : (
                    pendingActions(take)
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </aside>
  );
}

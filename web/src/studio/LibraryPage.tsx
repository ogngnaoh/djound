import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bookmark, Music2, Pause, Play, Plus, Search } from "lucide-react";
import { Button } from "../components/ui/button";
import { Waveform, clock, useAudio } from "./Player";
import { RecipeDetails } from "./Refinement";
import { useStudio } from "./context";
import { Loading, Failure } from "./Views";

function useProjects() {
  const { mode, scenario, service } = useStudio();
  return useQuery({
    queryKey: ["studio-library", mode, scenario],
    queryFn: () => service.list(),
    retry: false,
  });
}
export function LibraryPage() {
  const { mode } = useStudio(),
    q = useProjects(),
    audio = useAudio();
  const [search, setSearch] = useState(""),
    [selectedId, setSelectedId] = useState("");
  if (q.isPending) return <Loading />;
  if (q.isError)
    return <Failure message={q.error.message} retry={() => void q.refetch()} />;
  const projects = q.data,
    filtered = projects.filter((p) =>
      `${p.title} ${p.fileName}`.toLowerCase().includes(search.toLowerCase()),
    );
  const kept = projects.flatMap((p) =>
    p.tracks.filter((t) => t.kept).map((track) => ({ project: p, track })),
  );
  const selected = kept.find((v) => v.track.id === selectedId) ?? kept[0];
  return (
    <div className="page-grid">
      <section className="main-column library-page">
        <header className="project-header">
          <span className="eyebrow">LIBRARY</span>
          <h1>Library</h1>
          <p>Recent projects and kept versions</p>
        </header>
        {projects.length ? (
          <>
            <div className="library-search">
              <label>
                <Search size={19} />
                <input
                  aria-label="Find a project"
                  placeholder="Find a project"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </label>
              <Button asChild>
                <Link to="/remix/new">
                  <Plus />
                  New project
                </Link>
              </Button>
            </div>
            <section className="recent-projects">
              <h2>Recent projects</h2>
              {filtered.length ? (
                filtered.map((p) => (
                  <article className="project-row" key={p.id}>
                    <div>
                      <h3>
                        <Link to={`/remix/${p.id}`}>{p.title}</Link>
                      </h3>
                      <p>
                        {p.tracks.length - 1}{" "}
                        {p.tracks.length === 2 ? "version" : "versions"} ·{" "}
                        {clock(p.tracks[0].duration)}
                      </p>
                    </div>
                    <span className="project-date">
                      {new Date(p.updatedAt).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                      })}
                    </span>
                    <Button variant="outline" asChild>
                      <Link to={`/remix/${p.id}`}>Open project</Link>
                    </Button>
                  </article>
                ))
              ) : (
                <div className="empty-search">
                  <h3>No projects found</h3>
                  <p>Try another name or clear your search.</p>
                  <Button variant="ghost" onClick={() => setSearch("")}>
                    Clear search
                  </Button>
                </div>
              )}
            </section>
            <section className="kept-section">
              <h2>Kept versions</h2>
              {kept.length ? (
                kept.map(({ project: p, track: t }) => (
                  <article
                    className={`kept-row ${selected?.track.id === t.id ? "selected-kept" : ""}`}
                    key={t.id}
                  >
                    <Bookmark className="accent" size={18} />
                    <button
                      className="kept-title"
                      onClick={() => setSelectedId(t.id)}
                    >
                      <strong>
                        {p.title} · {t.title}
                      </strong>
                      <span>{t.description}</span>
                    </button>
                    <Button
                      variant="outline"
                      onClick={() =>
                        audio.track?.id === t.id
                          ? audio.toggle()
                          : audio.choose(t)
                      }
                    >
                      {audio.track?.id === t.id && audio.playing ? (
                        <Pause />
                      ) : (
                        <Play />
                      )}
                      {audio.track?.id === t.id && audio.playing
                        ? "Pause"
                        : "Play"}
                    </Button>
                    <Button variant="outline" asChild>
                      <Link to={`/remix/${p.id}?recording=${t.id}`}>
                        Open project
                      </Link>
                    </Button>
                  </article>
                ))
              ) : (
                <p className="subtle">
                  Keep a version in your Studio to find it here.
                </p>
              )}
              <p className="small subtle">
                Kept versions stay inside their original project.
              </p>
            </section>
          </>
        ) : (
          <div className="library-empty">
            <div className="empty-symbol">
              <Music2 size={30} />
            </div>
            <h2>Your next melody starts here.</h2>
            <p>
              Bring a short recording. Explore a different arrangement.
              <br />
              Keep the versions you want to hear again.
            </p>
            <Button asChild>
              <Link to="/remix/new">
                <Plus />
                New project
              </Link>
            </Button>
            <span className="small subtle">
              Local music. A little room to experiment.
            </span>
          </div>
        )}
      </section>
      <aside className="context-column library-context">
        {selected ? (
          <>
            <h2>{selected.project.title}</h2>
            <p className="accent kept-caption">
              <Bookmark size={16} />
              {selected.track.title} · Kept
            </p>
            <p>{selected.track.description}</p>
            <Waveform track={selected.track} compact />
            <section>
              <h3>This recording</h3>
              <dl className="recipe-details">
                <dt>Duration</dt>
                <dd>{clock(selected.track.duration)}</dd>
              </dl>
              {mode === "prototype" ? (
                <RecipeDetails recipe={selected.track.recipe} />
              ) : (
                <p className="small subtle">
                  Review a local arrangement to see its confirmed settings.
                </p>
              )}
            </section>
            <Button variant="outline" className="full" asChild>
              <Link
                to={`/remix/${selected.project.id}?recording=${selected.track.id}`}
              >
                Open project
              </Link>
            </Button>
            <p className="small subtle">
              Opening a project does not interrupt playback or change your
              refinement basis.
            </p>
          </>
        ) : (
          <>
            <h2>A place for your ideas.</h2>
            <p className="subtle">
              Your projects, original recordings, and kept versions stay
              together.
            </p>
            <div className="quiet-symbol">
              <img src="/brand/symbol.svg" alt="" />
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

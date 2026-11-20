import { useQuery } from "@tanstack/react-query";
import {
  AudioLines,
  ExternalLink,
  Folder,
  Heart,
  Layers,
  Music2,
  Search,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { recordingList, trashedRecordings, type Recording } from "./api";
import { RecordingImport } from "./RecordingImport";
import {
  rememberLibraryRow,
  useLibraryOrigin,
  useRestoreLibraryRow,
} from "./libraryNavigation";
import { RecordingArtwork, recordingName } from "./recordingPresentation";
import { clock } from "../studio/Player";
import { RecordingTrashAction, RecordingTrashControls } from "./RecordingTrash";
import "./recording-library.css";

function LibraryRow({
  item,
  featured,
  favorites,
  inTrash,
}: {
  item: Recording;
  featured: boolean;
  favorites: boolean;
  inTrash: boolean;
}) {
  const origin = useLibraryOrigin();
  const name = recordingName(item.title);
  const duration = clock(item.favoriteDurationSeconds ?? item.durationSeconds);
  const target =
    "/renditions/" +
    item.id +
    (favorites && item.favoriteId ? "?take=" + item.favoriteId : "");
  return (
    <article
      className={"collection-row" + (featured ? " collection-feature" : "")}
    >
      <Link
        className="collection-open"
        to={target}
        state={origin}
        id={"recording-" + item.id}
        onClick={() => rememberLibraryRow(item.id)}
        aria-label={
          (inTrash ? "View " : "Open ") +
          name +
          (item.favoriteLabel ? ", favorite " + item.favoriteLabel : "")
        }
      >
        <RecordingArtwork className="collection-art" id={item.id} />
        <span className="collection-identity">
          <strong>{name}</strong>
          <span className="collection-take">
            {item.favoriteLabel || "Original"}
            {item.favoriteId && (
              <Heart size={16} fill="currentColor" aria-label="Favorite" />
            )}
          </span>
          {featured && (
            <span className="collection-meta">
              {item.readyTakeCount}{" "}
              {item.readyTakeCount === 1 ? "take" : "takes"} · {duration}
            </span>
          )}
          {item.inFlightCount > 0 && (
            <small className="collection-job">Making a take…</small>
          )}
        </span>
        {featured ? (
          <span className="collection-open-action">
            <ExternalLink size={16} />
            Open studio
          </span>
        ) : (
          <span className="collection-duration">{duration}</span>
        )}
      </Link>
      <RecordingTrashAction recording={item} inTrash={inTrash} />
    </article>
  );
}

const collections = [
  { view: "", label: "All recordings", icon: Layers },
  { view: "favorites", label: "Favorites", icon: Heart },
  { view: "trash", label: "Trash", icon: Trash2 },
];

export default function RecordingLibrary() {
  const [params, setParams] = useSearchParams();
  const view =
    collections.find((entry) => entry.view === params.get("view"))?.view ?? "";
  const inTrash = view === "trash";
  const favorites = view === "favorites";
  const search = params.get("q") ?? "";
  const list = useQuery(inTrash ? trashedRecordings : recordingList);
  useRestoreLibraryRow(list.isSuccess);
  const collection = (list.data ?? []).filter(
    (row) => !favorites || row.favoriteId,
  );
  const query = search.trim().toLocaleLowerCase();
  const rows = collection.filter((row) =>
    row.title.toLocaleLowerCase().includes(query),
  );
  function collectionUrl(name: string) {
    const next = new URLSearchParams(params);
    if (name) next.set("view", name);
    else next.delete("view");
    return { pathname: "/", search: next.toString() };
  }
  function changeSearch(value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set("q", value);
    else next.delete("q");
    setParams(next, { replace: true });
  }
  return (
    <div className="collection-layout">
      <aside className="collection-rail">
        <nav className="collection-views" aria-label="Recording collections">
          {collections.map(({ view: name, label, icon: Icon }) => (
            <Link
              key={name}
              to={collectionUrl(name)}
              aria-current={view === name ? "page" : undefined}
            >
              <Icon aria-hidden="true" />
              {label}
            </Link>
          ))}
        </nav>
        <nav className="collection-tools" aria-label="Other tools">
          <Link className="collection-tools-heading" to="/tools/library">
            Other tools
          </Link>
          <Link to="/mixes">
            <SlidersHorizontal />
            Mix editor
          </Link>
          <Link to="/remix/new">
            <Music2 />
            Melody studio
          </Link>
        </nav>
      </aside>
      <section className="collection-content">
        <header className="collection-heading">
          <div>
            <h1>{inTrash ? "Trash" : favorites ? "Favorites" : "Library"}</h1>
            {list.isSuccess && (
              <p>
                {collection.length}{" "}
                {collection.length === 1 ? "recording" : "recordings"}
              </p>
            )}
          </div>
          {!inTrash && <RecordingImport />}
        </header>
        {(!list.isSuccess || collection.length > 0 || search) && (
          <label className="collection-search">
            <Search aria-hidden="true" size={19} />
            <input
              type="search"
              value={search}
              aria-label="Search recordings"
              placeholder="Search your recordings"
              onChange={(e) => changeSearch(e.target.value)}
            />
          </label>
        )}
        {list.isPending && <p role="status">Loading recordings…</p>}
        {list.isError && (
          <p className="collection-error" role="alert">
            {list.error.message}{" "}
            <button className="text-action" onClick={() => void list.refetch()}>
              Try again
            </button>
          </p>
        )}
        <RecordingTrashControls>
          {rows.map((item, index) => (
            <LibraryRow
              key={item.id}
              item={item}
              featured={!view && !query && index === 0}
              favorites={favorites}
              inTrash={inTrash}
            />
          ))}
        </RecordingTrashControls>
        {list.isSuccess && rows.length === 0 && (
          <div className="collection-empty">
            {search.trim() ? (
              <Search />
            ) : inTrash ? (
              <Trash2 />
            ) : favorites ? (
              <Heart />
            ) : (
              <AudioLines />
            )}
            <h2>
              {search.trim()
                ? "No matching recordings"
                : inTrash
                  ? "Trash is empty"
                  : favorites
                    ? "No favorites yet"
                    : "Bring your first recording"}
            </h2>
            <p>
              {search.trim() ? (
                <button
                  className="text-action"
                  onClick={() => changeSearch("")}
                >
                  Clear search
                </button>
              ) : inTrash ? (
                "Recordings you move to Trash can be restored here."
              ) : favorites ? (
                "Keep a take as a favorite in the studio to find it here."
              ) : (
                "Import a song or voice memo, then describe the instrumental you want to hear."
              )}
            </p>
          </div>
        )}
        <Link className="collection-mobile-tools" to="/tools/library">
          <Folder size={16} />
          Other tools
        </Link>
      </section>
    </div>
  );
}

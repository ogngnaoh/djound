import { useQuery } from "@tanstack/react-query";
import { ArrowRight, AudioLines } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { recordingList, trashedRecordings } from "./api";
import { RecordingImport } from "./RecordingImport";
import {
  rememberLibraryRow,
  useLibraryOrigin,
  useRestoreLibraryRow,
} from "./libraryNavigation";
import { clock } from "../studio/Player";
import { RecordingTrashAction, RecordingTrashControls } from "./RecordingTrash";
import "../mixes/mixes.css";

export default function RecordingLibrary() {
  const origin = useLibraryOrigin();
  const [params, setParams] = useSearchParams();
  const view = params.get("view");
  const inTrash = view === "trash";
  const favorites = view === "favorites";
  const search = params.get("q") ?? "";
  const list = useQuery(inTrash ? trashedRecordings : recordingList);
  useRestoreLibraryRow(list.isSuccess);
  const rows = list.data?.filter(
    (row) =>
      (!favorites || row.favoriteId) &&
      row.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
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
    <section className="mix-library">
      <header>
        <h1>{inTrash ? "Trash" : favorites ? "Favorites" : "Library"}</h1>
        {!inTrash && <RecordingImport />}
      </header>
      <nav
        className="collection-views flex flex-wrap gap-4 my-4"
        aria-label="Recording collections"
      >
        <Link
          to={collectionUrl("")}
          aria-current={!favorites && !inTrash ? "page" : undefined}
        >
          All recordings
        </Link>
        <Link
          to={collectionUrl("favorites")}
          aria-current={favorites ? "page" : undefined}
        >
          Favorites
        </Link>
        <Link
          to={collectionUrl("trash")}
          aria-current={inTrash ? "page" : undefined}
        >
          Trash
        </Link>
      </nav>
      <label className="collection-search">
        <span className="sr-only">Search recordings</span>
        <input
          type="search"
          value={search}
          placeholder="Search recordings"
          onChange={(e) => changeSearch(e.target.value)}
        />
      </label>
      {list.isPending && <p role="status">Loading recordings…</p>}
      {list.isError && (
        <p className="notice error" role="alert">
          {list.error.message}{" "}
          <button className="text-action" onClick={() => void list.refetch()}>
            Try again
          </button>
        </p>
      )}
      <RecordingTrashControls>
        {rows?.map((item) => (
          <article key={item.id} className="flex flex-wrap items-center gap-2">
            <Link
              to={
                "/renditions/" +
                item.id +
                (favorites && item.favoriteId ? "?take=" + item.favoriteId : "")
              }
              state={origin}
              id={"recording-" + item.id}
              onClick={() => rememberLibraryRow(item.id)}
              className="mix-library-row min-w-0 flex-1"
            >
              <AudioLines aria-hidden="true" />
              <span>
                <strong>
                  {item.title.replace(/\.(?:wav|mp3|m4a)$/i, "") || item.title}
                </strong>
                <small>
                  {clock(item.favoriteDurationSeconds ?? item.durationSeconds)}{" "}
                  · {item.readyTakeCount}{" "}
                  {item.readyTakeCount === 1 ? "take" : "takes"}
                  {item.favoriteLabel && " · Favorite: " + item.favoriteLabel}
                  {item.inFlightCount > 0 && " · Making a take…"}
                </small>
              </span>
              <ArrowRight aria-hidden="true" />
            </Link>
            <RecordingTrashAction recording={item} inTrash={inTrash} />
          </article>
        ))}
      </RecordingTrashControls>
      {rows?.length === 0 && !list.isError && (
        <p>
          {search.trim() ? (
            <>
              No recordings match your search.{" "}
              <button className="text-action" onClick={() => changeSearch("")}>
                Clear search
              </button>
            </>
          ) : inTrash ? (
            "Trash is empty."
          ) : favorites ? (
            "Keep a favorite in the studio to find it here."
          ) : (
            "Import your first recording to make an instrumental take."
          )}
        </p>
      )}
    </section>
  );
}

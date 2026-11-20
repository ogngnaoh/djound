import { useEffect } from "react";
import { ArrowLeft } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";

export function useLibraryOrigin() {
  const location = useLocation();
  return {
    library: {
      pathname: location.pathname,
      search: location.search,
      index: window.history.state?.idx,
    },
  };
}

export function rememberLibraryRow(id: string) {
  // Keep the scroll position on this particular Back entry, including after reload.
  window.history.replaceState(
    { ...window.history.state, recordingRow: { id, scrollY: window.scrollY } },
    "",
  );
}

export function useRestoreLibraryRow(loaded: boolean) {
  const location = useLocation();
  useEffect(() => {
    const row = window.history.state?.recordingRow;
    if (!loaded || !row) return;
    const frame = requestAnimationFrame(() => {
      document
        .getElementById("recording-" + row.id)
        ?.focus({ preventScroll: true });
      window.scrollTo(0, row.scrollY);
    });
    return () => cancelAnimationFrame(frame);
  }, [loaded, location.key]);
}

export function LibraryReturnLink() {
  const origin = useLocation().state?.library;
  const navigate = useNavigate();
  const inCollection =
    origin?.pathname === "/" || origin?.pathname === "/renditions";
  const to = inCollection
    ? { pathname: origin.pathname, search: origin.search }
    : "/";
  const view = inCollection && new URLSearchParams(origin.search).get("view");
  return (
    <Link
      className="text-action"
      to={to}
      onClick={(event) => {
        if (
          inCollection &&
          Number.isInteger(origin.index) &&
          window.history.state?.idx === origin.index + 1 &&
          event.button === 0 &&
          !event.metaKey &&
          !event.ctrlKey &&
          !event.altKey &&
          !event.shiftKey
        ) {
          event.preventDefault();
          navigate(-1);
        }
      }}
    >
      <ArrowLeft size={15} aria-hidden="true" />
      {view === "favorites"
        ? "Favorites"
        : view === "trash"
          ? "Trash"
          : "Library"}
    </Link>
  );
}

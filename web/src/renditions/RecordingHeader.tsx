import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Folder, Settings2 } from "lucide-react";
import { Link } from "react-router-dom";
import { LibraryReturnLink } from "./libraryNavigation";
import { projectKey, readProject } from "./api";
import { recordingName } from "./recordingPresentation";

function CurrentRecording({ id }: { id: string }) {
  // The studio owns loading and refresh. Its header observes the same cached project.
  const { data } = useQuery({
    queryKey: projectKey(id),
    queryFn: ({ signal }) => readProject(id, signal),
    enabled: false,
  });
  return data ? (
    <>
      <ChevronRight className="desk-crumb-chevron" aria-hidden="true" />
      <span className="desk-crumb-title" aria-current="page">
        {recordingName(data.title)}
      </span>
    </>
  ) : null;
}

export function RecordingHeader({
  recordingId,
  onOptions,
}: {
  recordingId?: string;
  onOptions: () => void;
}) {
  return (
    <header className="desk-header">
      <Link className="desk-brand" to="/" aria-label="Djound home">
        <img
          src="/brand/wordmark-dark.svg"
          alt="Djound"
          width={130}
          height={32}
        />
      </Link>
      <nav aria-label="Breadcrumb" className="desk-breadcrumb">
        {recordingId ? (
          <>
            <LibraryReturnLink />
            <CurrentRecording id={recordingId} />
          </>
        ) : (
          <Link to="/" aria-current="page">
            <Folder size={19} aria-hidden="true" /> Library
          </Link>
        )}
      </nav>
      <button
        className="desk-options"
        type="button"
        onClick={onOptions}
        aria-label="Workspace options"
        title="Workspace options"
      >
        <Settings2 size={18} />
      </button>
    </header>
  );
}

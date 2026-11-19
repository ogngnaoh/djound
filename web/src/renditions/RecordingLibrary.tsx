import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, AudioLines, Upload } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "../components/ui/button";
import {
  projectKey,
  recordingList,
  renditionRequest,
  type RecordingProject,
} from "./api";
import { useRecoverableRequest } from "./useRecoverableRequest";
import "../mixes/mixes.css";

export default function RecordingLibrary() {
  const input = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const client = useQueryClient();
  const list = useQuery(recordingList);
  const [dragging, setDragging] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  const upload = useRecoverableRequest(
    "rendition-import",
    async (command: { key: string; file: File }) => {
      const body = new FormData();
      body.append("file", command.file);
      const res = await renditionRequest("", {
        method: "POST",
        body,
        headers: { "Idempotency-Key": command.key },
      });
      const project: RecordingProject = await res.json();
      client.setQueryData(projectKey(project.id), project);
      void client.invalidateQueries({ queryKey: recordingList.queryKey });
      navigate("/renditions/" + project.id);
    },
  );
  function choose(files: FileList | null) {
    if (!files?.length || upload.busy || upload.pending) return;
    if (files.length !== 1) {
      setSelectionError("Choose one recording at a time.");
      return;
    }
    setSelectionError("");
    void upload.submit({ key: crypto.randomUUID(), file: files[0] });
  }
  return (
    <section className="mix-library">
      <header>
        <h1>Reimagine a recording</h1>
        <p>Hear a song or voice memo as a new instrumental rendition.</p>
      </header>
      <div
        className={"mix-drop" + (dragging ? " over" : "")}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          choose(e.dataTransfer.files);
        }}
      >
        <AudioLines size={32} aria-hidden="true" />
        <h2>Drop your recording here</h2>
        <p>MP3, WAV, or M4A · 1 second to 5 minutes · up to 64 MiB</p>
        <input
          type="file"
          className="sr-only"
          ref={input}
          aria-label="Import recording"
          accept=".mp3,.wav,.m4a,audio/mpeg,audio/wav,audio/mp4"
          disabled={upload.busy || !!upload.pending}
          onChange={(e) => {
            choose(e.target.files);
            e.target.value = "";
          }}
        />
        <Button
          disabled={upload.busy || !!upload.pending}
          onClick={() => input.current?.click()}
        >
          <Upload />
          {upload.busy && upload.pending
            ? "Opening recording…"
            : "Choose recording"}
        </Button>
        {upload.pending && !upload.busy && (
          <Button
            variant="outline"
            onClick={() => void upload.submit(upload.pending!)}
          >
            Resume import of {upload.pending.file.name}
          </Button>
        )}
      </div>
      {(selectionError || upload.error) && (
        <p role="alert" className="notice error">
          {selectionError || upload.error}
        </p>
      )}
      <h2 className="mix-list-title">Your recordings</h2>
      {list.isPending && <p role="status">Loading recordings…</p>}
      {list.isError && (
        <p className="notice error" role="alert">
          {list.error.message}{" "}
          <button className="text-action" onClick={() => void list.refetch()}>
            Try again
          </button>
        </p>
      )}
      {list.data?.map((item) => (
        <Link
          to={"/renditions/" + item.id}
          key={item.id}
          className="mix-library-row"
        >
          <AudioLines />
          <span>
            <strong>{item.title}</strong>
          </span>
          <ArrowRight />
        </Link>
      ))}
      {list.data?.length === 0 && (
        <p>Imported recordings and their versions will appear here.</p>
      )}
    </section>
  );
}

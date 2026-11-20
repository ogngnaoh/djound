import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "../components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetTrigger,
} from "../components/ui/sheet";
import {
  projectKey,
  recordingList,
  renditionRequest,
  type RecordingProject,
} from "./api";
import { useLibraryOrigin } from "./libraryNavigation";
import { useRecoverableRequest } from "./useRecoverableRequest";

export function RecordingImport() {
  const input = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const returnToStudio = useRef(false);
  const [dragging, setDragging] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  const navigate = useNavigate();
  const origin = useLibraryOrigin();
  const client = useQueryClient();
  useEffect(
    () => () => {
      returnToStudio.current = false;
    },
    [],
  );
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
      if (returnToStudio.current)
        navigate("/renditions/" + project.id + "?take=original", {
          state: origin,
        });
    },
  );
  function choose(files: FileList | null) {
    if (!files?.length || upload.busy || upload.pending) return;
    const file = files[0];
    if (files.length !== 1)
      return setSelectionError("Choose one recording at a time.");
    if (file.size > 64 * 1024 * 1024)
      return setSelectionError("Choose a file of 64 MiB or smaller.");
    if (!file.size)
      return setSelectionError(
        "This file is empty. Choose a recording with audio.",
      );
    setSelectionError("");
    void upload.submit({ key: crypto.randomUUID(), file });
  }
  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        returnToStudio.current = next;
        setOpen(next);
        setSelectionError("");
        setDragging(false);
      }}
    >
      <SheetTrigger asChild>
        <Button>
          <Upload />
          {upload.pending
            ? upload.busy
              ? "Importing recording…"
              : "Resume import"
            : "Import recording"}
        </Button>
      </SheetTrigger>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Import recording</SheetTitle>
          <SheetDescription>
            MP3, WAV, or M4A · 1 second to 5 minutes · up to 64 MiB
          </SheetDescription>
        </SheetHeader>
        <div
          className={"mix-drop" + (dragging ? " over" : "")}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            choose(event.dataTransfer.files);
          }}
        >
          <Upload aria-hidden="true" />
          {upload.pending ? (
            <>
              <p role="status">
                {upload.busy ? "Importing " : "Pending import: "}
                {upload.pending.file.name}
              </p>
              {!upload.busy && (
                <Button onClick={() => void upload.submit(upload.pending!)}>
                  Resume import
                </Button>
              )}
            </>
          ) : (
            <>
              <p>Drop your recording here</p>
              <input
                type="file"
                hidden
                ref={input}
                aria-label="Choose recording file"
                accept=".mp3,.wav,.m4a,audio/mpeg,audio/wav,audio/mp4"
                onChange={(event) => {
                  choose(event.target.files);
                  event.target.value = "";
                }}
              />
              <Button
                disabled={upload.busy}
                onClick={() => input.current?.click()}
              >
                Choose recording
              </Button>
            </>
          )}
        </div>
        {(selectionError || upload.error) && (
          <p className="notice error" role="alert">
            {selectionError || upload.error}
          </p>
        )}
      </SheetContent>
    </Sheet>
  );
}

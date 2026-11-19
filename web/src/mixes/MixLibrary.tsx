import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, AudioLines, Upload } from "lucide-react";
import { Button } from "../components/ui/button";
import { pendingCommand } from "../recovery";
import { mixRequest, MixError, formatTime, type Mix } from "./api";
import "./mixes.css";

type ImportCommand = { key: string; file: File };
export const mixListQuery = {
  queryKey: ["finished-mixes"],
  queryFn: () => mixRequest<{ items: Mix[] }>(""),
};
export default function MixLibrary() {
  const client = useQueryClient();
  const list = useQuery(mixListQuery);
  const input = useRef<HTMLInputElement>(null),
    navigate = useNavigate();
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [drag, setDrag] = useState(false);
  const [pending, setPending] = useState<ImportCommand>();
  useEffect(() => {
    void pendingCommand<ImportCommand>("mix-import")
      .then(setPending)
      .catch((e) => setError(e.message));
  }, []);
  async function open(file: File, previous?: ImportCommand) {
    if (busy) return;
    setBusy(true);
    setError("");
    const command = previous ?? { key: crypto.randomUUID(), file };
    try {
      await pendingCommand("mix-import", command);
      setPending(command);
      const form = new FormData();
      form.append("file", command.file);
      const state = await mixRequest<Mix>("", "POST", form, command.key);
      await pendingCommand("mix-import", null, command.key);
      client.setQueryData(["finished-mix", state.id], state);
      void client.invalidateQueries({ queryKey: mixListQuery.queryKey });
      navigate("/mixes/" + state.id);
    } catch (e) {
      if (e instanceof MixError && e.status >= 400 && e.status < 500) {
        await pendingCommand("mix-import", null, command.key);
        setPending(undefined);
      }
      setError(e instanceof Error ? e.message : "Could not import this song.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="mix-library">
      <header>
        <h1>Edit a finished mix</h1>
        <p>
          Open a stereo MP3, select a passage and describe the change you want
          to hear.
        </p>
      </header>
      <div
        className={"mix-drop" + (drag ? " over" : "")}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          if (e.dataTransfer.files.length === 1 && !pending)
            void open(e.dataTransfer.files[0]);
          else setError("Choose one stereo MP3 at a time.");
        }}
      >
        <AudioLines size={32} />
        <h2>{busy ? "Opening recording…" : "Drop your finished mix here"}</h2>
        <p>Stereo MP3 · up to 5 minutes · 44.1 or 48 kHz</p>
        <input
          className="sr-only"
          ref={input}
          type="file"
          aria-label="Import stereo MP3"
          accept=".mp3,audio/mpeg"
          disabled={busy || !!pending}
          onChange={(e) => {
            if (e.target.files?.[0]) void open(e.target.files[0]);
            e.target.value = "";
          }}
        />
        <Button
          disabled={busy || !!pending}
          onClick={() => input.current?.click()}
        >
          <Upload />
          Choose MP3
        </Button>
        {pending && !busy && (
          <Button
            variant="outline"
            onClick={() => void open(pending.file, pending)}
          >
            Resume import of {pending.file.name}
          </Button>
        )}
      </div>
      {(error || list.error) && (
        <p role="alert" className="notice error">
          {error || list.error?.message}
        </p>
      )}
      <h2 className="mix-list-title">Your mixes</h2>
      {list.data?.items.map((p) => (
        <Link className="mix-library-row" to={"/mixes/" + p.id} key={p.id}>
          <AudioLines />
          <span>
            <strong>{p.title}</strong>
            <small>
              {formatTime(p.durationMs / 1000)} · {p.takes.length}{" "}
              {p.takes.length === 1 ? "take" : "takes"}
            </small>
          </span>
          <ArrowRight />
        </Link>
      ))}
      {list.data?.items.length === 0 && (
        <p className="subtle">Imported songs will appear here.</p>
      )}
    </section>
  );
}

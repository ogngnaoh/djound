import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, LoaderCircle, Upload } from "lucide-react";
import { Button } from "../components/ui/button";
import { remember, useStudio } from "./context";

export function NewProject() {
  const { service, mode } = useStudio(),
    navigate = useNavigate(),
    client = useQueryClient();
  const [file, setFile] = useState<File | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  async function start(sample = false) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const p = sample ? await service.sample() : await service.upload(file!);
      client.invalidateQueries({ queryKey: ["studio-library"] });
      remember(`djound.last.${mode}`, p.id);
      navigate(`/remix/${p.id}/melody`);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Could not open this recording.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="page-grid">
      <section className="main-column new-project">
        <header className="project-header">
          <span className="eyebrow">NEW PROJECT</span>
          <h1>Start with a melody.</h1>
          <p>A familiar tune. A different possibility.</p>
        </header>
        <div
          className={`upload-zone ${drag ? "dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            if (e.dataTransfer.files.length !== 1) {
              setError("Choose one recording at a time.");
              return;
            }
            setFile(e.dataTransfer.files[0]);
            setError("");
          }}
        >
          <div className="upload-symbol">
            <Upload size={26} />
          </div>
          <h2>{file ? file.name : "Bring a short recording"}</h2>
          <p>
            {file
              ? "Ready to start a new project."
              : "Drop your audio file here, or choose one below."}
          </p>
          <input
            ref={input}
            className="sr-only"
            type="file"
            accept=".wav,.mp3,audio/wav,audio/mpeg"
            aria-label="Choose audio file"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setError("");
            }}
          />
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => input.current?.click()}
          >
            {file ? "Choose another file" : "Choose audio file"}
          </Button>
          <p className="small subtle">
            WAV or MP3 · 1–12 seconds · up to 64 MiB
          </p>
        </div>
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
        <div className="upload-actions">
          <Button disabled={!file || busy} onClick={() => void start()}>
            {busy ? <LoaderCircle className="spin" /> : <ArrowRight />}
            {busy ? "Opening recording…" : "Continue to melody review"}
          </Button>
          <button
            className="text-action"
            disabled={busy}
            onClick={() => void start(true)}
          >
            Try a sample melody <ArrowRight size={15} />
          </button>
        </div>
        <p className="small subtle">
          {mode === "prototype"
            ? "Prototype uploads stay in this browser. Sample arrangements use prerecorded audio."
            : "Your recording is saved in your local installation."}
        </p>
      </section>
      <aside className="context-column upload-guide">
        <h2>
          From a melody
          <br />
          to something new.
        </h2>
        <ol>
          <li>
            <span>01</span>
            <div>
              <h3>Bring your melody</h3>
              <p>A clear, short recording works best.</p>
            </div>
          </li>
          <li>
            <span>02</span>
            <div>
              <h3>Check the notes</h3>
              <p>Listen, make corrections, and confirm.</p>
            </div>
          </li>
          <li>
            <span>03</span>
            <div>
              <h3>Make it your own</h3>
              <p>Try another instrument or shape the backing.</p>
            </div>
          </li>
        </ol>
        <p className="small subtle">
          Your uploaded original is always preserved.
        </p>
      </aside>
    </div>
  );
}

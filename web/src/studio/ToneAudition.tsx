import { useId, useLayoutEffect, useRef, useState } from "react";
import { useAudio } from "./Player";
import type { Project, Stem, StudioService, Tone, Track } from "./types";

export type AuditionSource = {
  project: Project;
  basis: Track;
  load: NonNullable<StudioService["auditionTone"]>;
};

export function ToneAudition({
  project,
  basis,
  load,
  stem,
  tone,
}: AuditionSource & { stem: Stem; tone: Tone }) {
  const { choose, clear, reserveAudition } = useAudio();
  const previewId = useId();
  const pending = useRef<AbortController | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useLayoutEffect(() => () => clear(previewId), [clear, previewId]);
  useLayoutEffect(() => {
    setLoading(false);
    setError("");
    return () => {
      pending.current?.abort();
      pending.current = null;
    };
  }, [stem, tone]);

  async function preview() {
    const controller = reserveAudition();
    pending.current = controller;
    setLoading(true);
    setError("");
    try {
      const blob = await load(project, basis.id, stem, tone, controller.signal);
      if (controller.signal.aborted) return;
      choose({
        ...basis,
        id: previewId,
        kind: "preview",
        auditionBasisId: basis.id,
        title: `${basis.title} · ${tone[0].toUpperCase()}${tone.slice(1)} ${stem}`,
        description: "Tone preview · full mix",
        url: URL.createObjectURL(blob),
      });
    } catch (e) {
      if (!controller.signal.aborted)
        setError(
          e instanceof Error ? e.message : "Could not load the tone preview.",
        );
    } finally {
      if (pending.current === controller) {
        pending.current = null;
        setLoading(false);
      }
    }
  }
  return (
    <>
      <button
        type="button"
        className="text-action"
        disabled={loading}
        onClick={() => void preview()}
      >
        {loading ? "Preparing preview…" : "Preview tone"}
      </button>
      <p className="small subtle" role="status">
        {error ||
          (tone === "neutral"
            ? "Hear the full mix with no tone coloring on this track."
            : "Hear the selected tone in the full mix.")}
      </p>
    </>
  );
}

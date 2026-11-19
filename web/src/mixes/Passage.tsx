import { memo, useEffect, useMemo, useRef, useState } from "react";
import { peaks } from "../studio/Player";
import { formatTime, type Mix, type Selection } from "./api";
import { Button } from "../components/ui/button";

// A filled envelope keeps the shape legible at both whole-song and phrase scale.
const Envelope = memo(function Envelope({
  values,
  from = 0,
  to = 1,
}: {
  values: number[];
  from?: number;
  to?: number;
}) {
  const shape = useMemo(() => {
    if (!values.length) return "";
    const first = Math.min(
      values.length - 1,
      Math.max(0, Math.floor(from * values.length)),
    );
    const end = Math.max(
      first + 1,
      Math.min(values.length, Math.ceil(to * values.length)),
    );
    const count = Math.min(400, end - first);
    const points = Array.from({ length: count }, (_, i) => {
      const begin = first + Math.floor((i * (end - first)) / count),
        stop = first + Math.floor(((i + 1) * (end - first)) / count);
      let energy = 0;
      for (let j = begin; j < stop; j++) energy += values[j] * values[j];
      return Math.max(1, Math.sqrt(energy / (stop - begin)) * 45);
    });
    const x = (i: number) => ((i * 1000) / Math.max(1, count - 1)).toFixed(2);
    return (
      "M" +
      points.map((h, i) => x(i) + "," + (50 - h).toFixed(2)).join(" L") +
      " L" +
      points
        .map((_, i) => {
          const j = count - 1 - i;
          return x(j) + "," + (50 + points[j]).toFixed(2);
        })
        .join(" L") +
      " Z"
    );
  }, [values, from, to]);
  return (
    <svg
      viewBox="0 0 1000 100"
      preserveAspectRatio="none"
      role="img"
      aria-label="Audio waveform"
    >
      <path d={shape} fill="currentColor" />
    </svg>
  );
});

export function Passage({
  mix,
  position,
  onSelect,
  onSeek,
}: {
  mix: Mix;
  position: number;
  onSelect: (s: Selection) => void;
  onSeek: (seconds: number) => void;
}) {
  const [values, setValues] = useState<number[]>([]);
  const [start, setStart] = useState(() =>
      String((mix.selection?.startFrame ?? 0) / mix.sampleRate),
    ),
    [end, setEnd] = useState(() =>
      String(
        mix.selection
          ? mix.selection.endFrame / mix.sampleRate
          : Math.min(6, mix.durationMs / 1000),
      ),
    );
  const anchor = useRef<number | null>(null);
  const duration = mix.frames / mix.sampleRate || mix.durationMs / 1000;
  useEffect(() => {
    let current = true;
    void peaks(mix.importUrl, 8192).then((p) => {
      if (current) setValues(p);
    });
    return () => {
      current = false;
    };
  }, [mix.importUrl]);
  useEffect(() => {
    if (mix.selection) {
      setStart(String(mix.selection.startFrame / mix.sampleRate));
      setEnd(String(mix.selection.endFrame / mix.sampleRate));
    }
  }, [mix.selection?.startFrame, mix.selection?.endFrame, mix.sampleRate]);
  const prepared = mix.selection;
  const focusFrom = prepared
    ? Math.max(0, prepared.startFrame / mix.sampleRate - 1)
    : 0;
  const focusTo = prepared
    ? Math.min(duration, prepared.endFrame / mix.sampleRate + 1)
    : duration;
  const a = Number(start),
    b = Number(end);
  const valid =
    mix.frames > 0 &&
    start !== "" &&
    end !== "" &&
    a >= 0 &&
    b > a &&
    b - a <= 30 &&
    Math.round(b * mix.sampleRate) <= mix.frames;
  function offset(event: React.PointerEvent<HTMLDivElement>) {
    const r = event.currentTarget.getBoundingClientRect();
    return (
      Math.round(
        Math.min(1, Math.max(0, (event.clientX - r.left) / r.width)) *
          duration *
          100,
      ) / 100
    );
  }
  return (
    <section aria-label="Passage selection" className="mix-passage">
      <div className="mix-time">
        <strong>Song overview</strong>
        <span>
          {formatTime(position)} / {formatTime(duration)}
        </span>
      </div>
      <div
        className="mix-wave"
        aria-label="Drag across the waveform to select a passage"
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          anchor.current = offset(e);
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (anchor.current === null) return;
          const t = offset(e);
          setStart(String(Math.min(anchor.current, t)));
          setEnd(String(Math.max(anchor.current, t)));
        }}
        onPointerUp={(e) => {
          if (anchor.current === null) return;
          const t = offset(e);
          if (Math.abs(anchor.current - t) < 0.02) {
            setStart(String(Math.min(t, Math.max(0, duration - 1))));
            setEnd(String(Math.min(duration, t + 6)));
          }
          anchor.current = null;
        }}
        onPointerCancel={() => {
          anchor.current = null;
        }}
      >
        <Envelope values={values} />
        <div
          className="mix-range"
          style={{
            left: (100 * a) / duration + "%",
            width: (100 * Math.max(0, b - a)) / duration + "%",
          }}
        />
        <div
          className="mix-playhead"
          style={{ left: Math.min(100, (100 * position) / duration) + "%" }}
        />
      </div>
      <div className="mix-ruler" aria-hidden="true">
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <span key={v}>{formatTime(v * duration)}</span>
        ))}
      </div>
      <form
        className="mix-range-fields"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid)
            onSelect({
              startFrame: Math.round(a * mix.sampleRate),
              endFrame: Math.round(b * mix.sampleRate),
            });
        }}
      >
        <label>
          From{" "}
          <input
            type="number"
            step="any"
            min="0"
            max={duration}
            value={start}
            aria-label="Passage start in seconds"
            onChange={(e) => setStart(e.target.value)}
          />{" "}
          s
        </label>
        <label>
          To{" "}
          <input
            type="number"
            step="any"
            min="0"
            max={duration}
            value={end}
            aria-label="Passage end in seconds"
            onChange={(e) => setEnd(e.target.value)}
          />{" "}
          s
        </label>
        <Button disabled={!valid} size="sm" variant="outline">
          Prepare passage
        </Button>
        {!valid && mix.frames > 0 && (
          <span role="status">Choose up to 30 seconds inside this song.</span>
        )}
      </form>
      {prepared && (
        <div className="mix-focus">
          <div className="mix-time">
            <strong>Selected passage</strong>
            <span>
              {formatTime(prepared.startFrame / mix.sampleRate)} –{" "}
              {formatTime(prepared.endFrame / mix.sampleRate)}
            </span>
          </div>
          <div className="mix-focus-wave">
            <Envelope
              values={values}
              from={focusFrom / duration}
              to={focusTo / duration}
            />
            <div
              className="mix-range"
              style={{
                left:
                  (100 * (prepared.startFrame / mix.sampleRate - focusFrom)) /
                    (focusTo - focusFrom) +
                  "%",
                width:
                  (100 * (prepared.endFrame - prepared.startFrame)) /
                    mix.sampleRate /
                    (focusTo - focusFrom) +
                  "%",
              }}
            />
            {position >= focusFrom && position <= focusTo && (
              <div
                className="mix-playhead"
                style={{
                  left:
                    (100 * (position - focusFrom)) / (focusTo - focusFrom) +
                    "%",
                }}
              />
            )}
            <input
              type="range"
              aria-label="Seek in passage"
              min={focusFrom}
              max={focusTo}
              step=".01"
              value={Math.max(focusFrom, Math.min(focusTo, position))}
              onChange={(e) => onSeek(Number(e.target.value))}
            />
          </div>
        </div>
      )}
    </section>
  );
}

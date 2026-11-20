import { useQuery } from "@tanstack/react-query";
import { Pause, Play, Volume2, VolumeX } from "lucide-react";
import {
  type RefObject,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { clock } from "../studio/Player";
import { renditionRequest } from "./api";
import "./recording-waveform.css";

type Props = {
  audioRef: RefObject<HTMLAudioElement | null>;
  recordingId: string;
  takeId: string;
  audioUrl: string;
  duration: number;
  label: string;
  onSeek: () => void;
};

async function readPeaks(
  recordingId: string,
  takeId: string,
  signal: AbortSignal,
) {
  const response = await renditionRequest(
    `/${recordingId}/takes/${takeId}/waveform`,
    { signal },
  );
  const peaks: unknown = await response.json();
  if (
    !Array.isArray(peaks) ||
    !peaks.length ||
    peaks.length > 512 ||
    !peaks.every(
      (value) => Number.isInteger(value) && value >= 0 && value <= 32768,
    )
  )
    throw new Error("Waveform unavailable.");
  return peaks as number[];
}

function waveBars(peaks: number[]) {
  const count = Math.min(192, peaks.length);
  // Scale the drawing to this recording so soft passages are visible when seeking.
  const peakLevel = Math.max(1, ...peaks);
  const bars = Array.from({ length: count }, (_, i) => {
    const from = Math.floor((i * peaks.length) / count);
    const to = Math.floor(((i + 1) * peaks.length) / count);
    const height = (
      (100 * Math.max(...peaks.slice(from, to))) /
      peakLevel
    ).toFixed(2);
    return `M${i + 0.5} -${height}V${height}`;
  });
  return { count, path: bars.join(" ") };
}

function currentMedia(audio: HTMLAudioElement, url: string) {
  return (
    audio.currentSrc === new URL(url, audio.ownerDocument.baseURI).href &&
    !audio.error &&
    audio.readyState >= HTMLMediaElement.HAVE_METADATA &&
    Number.isFinite(audio.duration) &&
    audio.duration > 0
  );
}

const initial = {
  url: "",
  position: 0,
  duration: 0,
  playing: false,
  volume: 1,
  muted: false,
};

export function RecordingWaveform({
  audioRef,
  recordingId,
  takeId,
  audioUrl,
  duration,
  label,
  onSeek,
}: Props) {
  const peaks = useQuery({
    queryKey: ["rendition-waveform", recordingId, takeId],
    queryFn: ({ signal }) => readPeaks(recordingId, takeId, signal),
    staleTime: Infinity, // A ready take and its source WAV are immutable.
    retry: false,
  });
  const shape = useMemo(
    () => (peaks.data ? waveBars(peaks.data) : null),
    [peaks.data],
  );
  const gradient = useId();
  const [media, setMedia] = useState(initial);
  const [playError, setPlayError] = useState<{
    url: string;
    message: string;
  } | null>(null);
  const previousVolume = useRef(1);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const hadControls = audio.controls;
    audio.controls = false;
    audio.dataset.waveformControls = "";
    return () => {
      audio.controls = hadControls;
      delete audio.dataset.waveformControls;
    };
  }, [audioRef]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    function update() {
      if (!audio) return;
      const ready = currentMedia(audio, audioUrl);
      setMedia({
        url: audioUrl,
        position: ready ? Math.min(audio.currentTime, audio.duration) : 0,
        duration: ready ? audio.duration : 0,
        playing: ready && !audio.paused && !audio.ended,
        volume: audio.volume,
        muted: audio.muted || audio.volume === 0,
      });
    }
    const events = [
      "loadedmetadata",
      "durationchange",
      "timeupdate",
      "seeking",
      "seeked",
      "play",
      "pause",
      "ended",
      "emptied",
      "error",
      "volumechange",
    ];
    for (const event of events) audio.addEventListener(event, update);
    update();
    return () => {
      for (const event of events) audio.removeEventListener(event, update);
    };
  }, [audioRef, audioUrl]);

  const live = media.url === audioUrl ? media : initial;
  const length =
    live.duration || (Number.isFinite(duration) ? Math.max(0, duration) : 0);
  const fraction = length ? Math.min(1, live.position / length) : 0;

  function seek(seconds: number) {
    const audio = audioRef.current;
    if (!audio || !currentMedia(audio, audioUrl) || !Number.isFinite(seconds))
      return;
    onSeek();
    audio.currentTime = Math.max(0, Math.min(seconds, audio.duration));
    setMedia((old) => ({ ...old, url: audioUrl, position: audio.currentTime }));
  }

  async function togglePlayback() {
    const audio = audioRef.current;
    if (
      !audio ||
      audio.src !== new URL(audioUrl, audio.ownerDocument.baseURI).href
    )
      return;
    if (!audio.paused) {
      audio.pause();
      return;
    }
    setPlayError(null);
    try {
      if (audio.error) audio.load();
      await audio.play();
    } catch (error) {
      if (
        audio.src === new URL(audioUrl, audio.ownerDocument.baseURI).href &&
        !(error instanceof DOMException && error.name === "AbortError") &&
        !audio.error
      )
        setPlayError({
          url: audioUrl,
          message: "Playback could not start. Press Play to try again.",
        });
    }
  }

  return (
    <section
      className="recording-waveform"
      aria-label={`Playback for ${label}`}
    >
      <div className="recording-wave-ruler" aria-hidden="true">
        {(length >= 8 ? [0, 0.25, 0.5, 0.75, 1] : [0, 1]).map((part) => (
          <span key={part} style={{ left: part * 100 + "%" }}>
            {clock(part * length)}
          </span>
        ))}
      </div>
      <div className="recording-wave-track">
        {shape ? (
          <svg
            viewBox={`0 -100 ${shape.count} 200`}
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <defs>
              <linearGradient id={gradient}>
                <stop offset={fraction} className="recording-wave-played" />
                <stop offset={fraction} className="recording-wave-unplayed" />
              </linearGradient>
            </defs>
            <path
              d={shape.path}
              stroke={`url(#${gradient})`}
              strokeWidth=".63"
              strokeLinecap="round"
              fill="none"
            />
          </svg>
        ) : (
          <span className="recording-wave-placeholder">
            {peaks.isPending ? "Loading waveform…" : "Waveform unavailable"}
          </span>
        )}
        <span
          aria-hidden="true"
          className="recording-wave-head"
          style={{ left: fraction * 100 + "%" }}
        />
        <input
          key={audioUrl}
          type="range"
          min={0}
          max={length || 1}
          step="any"
          value={live.position}
          disabled={!live.duration}
          aria-label={`Seek in ${label}`}
          aria-valuetext={`${clock(live.position)} of ${clock(length)}`}
          onChange={(e) => seek(e.currentTarget.valueAsNumber)}
          onKeyDown={(e) => {
            const delta = Math.min(5, length / 10);
            const targets: Record<string, number> = {
              ArrowLeft: live.position - delta,
              ArrowDown: live.position - delta,
              ArrowRight: live.position + delta,
              ArrowUp: live.position + delta,
              PageDown: live.position - length / 10,
              PageUp: live.position + length / 10,
              Home: 0,
              End: length,
            };
            if (Object.hasOwn(targets, e.key)) {
              e.preventDefault();
              seek(targets[e.key]);
            }
          }}
        />
      </div>
      <div className="recording-wave-controls">
        <button
          className="recording-wave-play"
          type="button"
          aria-label={live.playing ? "Pause audio" : "Play audio"}
          onClick={() => void togglePlayback()}
        >
          {live.playing ? (
            <Pause aria-hidden="true" />
          ) : (
            <Play aria-hidden="true" />
          )}
        </button>
        <span className="recording-wave-time" aria-label="Playback time">
          {clock(live.position)} / {clock(length)}
        </span>
        {peaks.isError && (
          <button
            type="button"
            className="text-action"
            onClick={() => void peaks.refetch()}
          >
            Retry waveform
          </button>
        )}
        <div className="recording-wave-volume">
          <button
            type="button"
            aria-label={live.muted ? "Unmute audio" : "Mute audio"}
            onClick={() => {
              const audio = audioRef.current;
              if (!audio) return;
              if (live.muted && !audio.volume)
                audio.volume = previousVolume.current;
              audio.muted = !live.muted;
            }}
          >
            {live.muted ? (
              <VolumeX aria-hidden="true" />
            ) : (
              <Volume2 aria-hidden="true" />
            )}
          </button>
          <input
            type="range"
            aria-label="Volume"
            min="0"
            max="1"
            step=".01"
            value={live.muted ? 0 : live.volume}
            aria-valuetext={
              Math.round((live.muted ? 0 : live.volume) * 100) + "%"
            }
            onChange={(e) => {
              const audio = audioRef.current;
              if (!audio) return;
              audio.volume = e.currentTarget.valueAsNumber;
              if (audio.volume > 0) previousVolume.current = audio.volume;
              audio.muted = audio.volume === 0;
            }}
          />
        </div>
      </div>
      {playError?.url === audioUrl && <p role="alert">{playError.message}</p>}
    </section>
  );
}

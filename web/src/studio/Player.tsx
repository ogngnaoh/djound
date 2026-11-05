import {
  createContext,
  useContext,
  useRef,
  useState,
  useCallback,
  useEffect,
  type ReactNode,
} from "react";
import type { Track } from "./types";
import { Button } from "../components/ui/button";
import { Slider } from "../components/ui/slider";
import "./player-controls.css";
import {
  Play,
  Pause,
  RotateCcw,
  Volume2,
  VolumeX,
  AudioLines,
} from "lucide-react";
export const clock = (v: number) =>
  `${Math.floor((Number.isFinite(v) ? v : 0) / 60)}:${Math.floor(
    (Number.isFinite(v) ? v : 0) % 60,
  )
    .toString()
    .padStart(2, "0")}`;
type AudioState = {
  track: Track | null;
  playing: boolean;
  loading: boolean;
  error: string;
  position: number;
  duration: number;
  volume: number;
  choose: (t: Track, play?: boolean) => void;
  toggle: () => void;
  seek: (n: number) => void;
  restart: () => void;
  setVolume: (n: number) => void;
  retry: () => void;
  clear: () => void;
};
const Context = createContext<AudioState | null>(null);
export function useAudio() {
  const c = useContext(Context);
  if (!c) throw new Error("Missing audio provider");
  return c;
}
export function AudioProvider({
  children,
  failed = false,
}: {
  children: ReactNode;
  failed?: boolean;
}) {
  const audio = useRef<HTMLAudioElement>(null),
    current = useRef<Track | null>(null);
  const [track, setTrack] = useState<Track | null>(null),
    [playing, setPlaying] = useState(false),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [position, setPosition] = useState(0),
    [duration, setDuration] = useState(0),
    [volume, setVolumeState] = useState(0.7);
  const fail = useRef(failed);
  fail.current = failed;
  const play = useCallback(() => {
    const a = audio.current;
    if (!a) return;
    if (fail.current) {
      setError("Audio could not be loaded. Retry when you’re ready.");
      setLoading(false);
      return;
    }
    setError("");
    if (a.ended) a.currentTime = 0;
    void a.play().catch((e) => {
      if (e.name !== "AbortError") {
        setError("This recording could not be played. Retry audio.");
        setPlaying(false);
        setLoading(false);
      }
    });
  }, []);
  const choose = useCallback(
    (t: Track, start = true) => {
      const a = audio.current;
      if (!a) return;
      if (current.current?.id !== t.id || current.current.url !== t.url) {
        a.pause();
        current.current = t;
        setTrack(t);
        setPosition(0);
        setDuration(t.duration);
        setPlaying(false);
        setError("");
        setLoading(true);
        a.src = t.url;
        a.load();
      }
      if (start) play();
    },
    [play],
  );
  const toggle = useCallback(() => {
    const a = audio.current;
    if (!a) return;
    if (a.paused) play();
    else a.pause();
  }, [play]);
  const seek = useCallback((n: number) => {
    const a = audio.current;
    if (a && a.readyState >= 1) {
      a.currentTime = Math.max(0, Math.min(n, a.duration || 0));
      setPosition(a.currentTime);
    }
  }, []);
  const restart = useCallback(() => seek(0), [seek]);
  const setVolume = useCallback((n: number) => {
    if (audio.current) audio.current.volume = n;
    setVolumeState(n);
  }, []);
  const retry = useCallback(() => {
    const a = audio.current;
    if (!a) return;
    setError("");
    setLoading(true);
    a.load();
    play();
  }, [play]);
  const clear = useCallback(() => {
    audio.current?.pause();
    audio.current?.removeAttribute("src");
    current.current = null;
    setTrack(null);
    setPosition(0);
    setPlaying(false);
    setError("");
  }, []);
  useEffect(() => {
    if (audio.current) audio.current.volume = 0.7;
  }, []);
  return (
    <Context.Provider
      value={{
        track,
        playing,
        loading,
        error,
        position,
        duration,
        volume,
        choose,
        toggle,
        seek,
        restart,
        setVolume,
        retry,
        clear,
      }}
    >
      {children}
      <audio
        ref={audio}
        preload="metadata"
        onLoadedMetadata={() => {
          setDuration(audio.current?.duration || 0);
          setLoading(false);
        }}
        onCanPlay={() => setLoading(false)}
        onWaiting={() => setLoading(true)}
        onPlaying={() => {
          setPlaying(true);
          setLoading(false);
        }}
        onPause={() => setPlaying(false)}
        onTimeUpdate={() => setPosition(audio.current?.currentTime || 0)}
        onEnded={() => setPlaying(false)}
        onError={() => {
          setError("Audio could not be loaded. Retry audio.");
          setLoading(false);
          setPlaying(false);
        }}
      />
    </Context.Provider>
  );
}
export function Transport() {
  const a = useAudio();
  const volumeDetails = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (
        volumeDetails.current?.open &&
        !volumeDetails.current.contains(event.target as Node)
      ) {
        volumeDetails.current.open = false;
      }
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, []);
  if (!a.track) return null;
  const status = a.error
    ? "Audio unavailable"
    : a.loading
      ? "Loading audio…"
      : a.playing
        ? "Playing"
        : "Paused";
  const projectTitle =
    a.track.projectTitle?.trim() ||
    (a.track.kind === "preview" ? "Melody review" : "Your project");
  return (
    <footer className="transport" aria-label="Audio player">
      <div className="transport-identity">
        <span className="track-symbol">
          <AudioLines size={24} />
        </span>
        <div>
          <strong>
            {projectTitle}
            <span className="transport-desktop"> · {a.track.title}</span>
          </strong>
          <span className="transport-description">
            {a.error || a.loading ? status : a.track.description}
          </span>
          <span className="transport-mobile-status">
            {a.track.title} · {status}
          </span>
        </div>
      </div>
      <div className="transport-buttons">
        <Button
          className="round-play"
          aria-label={a.playing ? "Pause playback" : "Play playback"}
          disabled={a.loading || !!a.error}
          onClick={a.toggle}
        >
          {a.playing ? (
            <Pause fill="currentColor" />
          ) : (
            <Play fill="currentColor" />
          )}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Restart recording"
          onClick={a.restart}
          disabled={a.loading}
        >
          <RotateCcw />
        </Button>
      </div>
      <div className="transport-progress">
        <span>{clock(a.position)}</span>
        <Slider
          className="playback-slider"
          aria-label="Playback position"
          aria-valuetext={`${clock(a.position)} of ${clock(a.duration)}`}
          min={0}
          max={a.duration || 1}
          step={0.1}
          value={[Math.min(a.position, a.duration || 0)]}
          disabled={a.loading || !!a.error}
          onValueChange={([value]) => a.seek(value)}
        />
        <span>{clock(a.duration)}</span>
      </div>
      <div className="transport-volume transport-volume-desktop">
        <Button
          variant="ghost"
          size="icon"
          aria-label={a.volume ? "Mute audio" : "Unmute audio"}
          onClick={() => a.setVolume(a.volume ? 0 : 0.7)}
        >
          {a.volume ? <Volume2 /> : <VolumeX />}
        </Button>
        <Slider
          aria-label="Volume"
          aria-valuetext={`${Math.round(a.volume * 100)} percent`}
          min={0}
          max={1}
          step={0.05}
          value={[a.volume]}
          onValueChange={([value]) => a.setVolume(value)}
        />
      </div>
      <details
        className="transport-volume-mobile"
        ref={volumeDetails}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            if (volumeDetails.current) {
              volumeDetails.current.open = false;
              volumeDetails.current.querySelector("summary")?.focus();
            }
          }
        }}
      >
        <summary aria-label="Volume controls">
          {a.volume ? (
            <Volume2 aria-hidden="true" />
          ) : (
            <VolumeX aria-hidden="true" />
          )}
        </summary>
        <div
          className="mobile-volume-panel"
          role="group"
          aria-label="Volume controls"
        >
          <div className="mobile-volume-heading">
            <strong>Volume</strong>
            <span>{Math.round(a.volume * 100)}%</span>
          </div>
          <Slider
            aria-label="Volume"
            aria-valuetext={`${Math.round(a.volume * 100)} percent`}
            min={0}
            max={1}
            step={0.05}
            value={[a.volume]}
            onValueChange={([value]) => a.setVolume(value)}
          />
          <Button
            variant="ghost"
            onClick={() => a.setVolume(a.volume ? 0 : 0.7)}
          >
            {a.volume ? "Mute audio" : "Unmute audio"}
          </Button>
        </div>
      </details>
      {a.error && (
        <div className="transport-error" role="alert">
          {a.error}
          <button onClick={a.retry}>Retry audio</button>
        </div>
      )}
    </footer>
  );
}
const peaksCache = new Map<string, Promise<number[]>>();
function peaks(url: string) {
  if (!peaksCache.has(url)) {
    peaksCache.set(
      url,
      (async () => {
        const response = await fetch(url);
        if (!response.ok) throw new Error("Waveform unavailable");
        const data = await response.arrayBuffer();
        const context = new AudioContext();
        try {
          const buffer = await context.decodeAudioData(data);
          const values = buffer.getChannelData(0),
            count = 180,
            block = Math.max(1, Math.floor(values.length / count));
          const result = Array.from({ length: count }, (_, i) => {
            let max = 0;
            for (
              let j = i * block;
              j < Math.min((i + 1) * block, values.length);
              j++
            )
              max = Math.max(max, Math.abs(values[j]));
            return max;
          });
          const max = Math.max(...result, 0.01);
          return result.map((v) => v / max);
        } finally {
          void context.close();
        }
      })().catch(() => []),
    );
  }
  return peaksCache.get(url)!;
}
export function Waveform({
  track,
  compact = false,
}: {
  track: Track;
  compact?: boolean;
}) {
  const [values, setValues] = useState<number[]>([]),
    a = useAudio();
  useEffect(() => {
    let active = true;
    setValues([]);
    void peaks(track.url).then((v) => {
      if (active) setValues(v);
    });
    return () => {
      active = false;
    };
  }, [track.url]);
  const current = a.track?.id === track.id;
  return (
    <div
      className={`waveform ${compact ? "waveform-compact" : ""}`}
      aria-label={`Waveform for ${track.title}`}
    >
      {values.length ? (
        <svg
          viewBox="0 0 820 156"
          preserveAspectRatio="none"
          role="img"
          aria-label="Audio amplitude waveform"
        >
          {values.map((v, i) => (
            <line
              key={i}
              x1={(i * 820) / values.length + 2}
              x2={(i * 820) / values.length + 2}
              y1={78 - Math.max(1, v * 65)}
              y2={78 + Math.max(1, v * 65)}
              stroke="currentColor"
              strokeWidth="1.7"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {current && (
            <g>
              <line
                x1={(a.position / (a.duration || 1)) * 820 + 2}
                x2={(a.position / (a.duration || 1)) * 820 + 2}
                y1="3"
                y2="150"
                stroke="currentColor"
                strokeWidth="1.5"
                vectorEffect="non-scaling-stroke"
              />
              <circle
                cx={(a.position / (a.duration || 1)) * 820 + 2}
                cy="150"
                r="4"
                fill="currentColor"
              />
            </g>
          )}
        </svg>
      ) : (
        <p className="subtle">{values.length === 0 ? "Audio waveform" : ""}</p>
      )}
    </div>
  );
}

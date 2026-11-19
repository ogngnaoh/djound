import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Bookmark,
  Check,
  Download,
  Pause,
  Play,
  RotateCcw,
  Send,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { pendingCommand } from "../recovery";
import { useAudio } from "../studio/Player";
import {
  mixRequest,
  level,
  formatTime,
  MixError,
  type Mix,
  type Reply,
  type Selection,
  type Take,
} from "./api";
import { Passage } from "./Passage";
import { LevelChoice } from "./LevelChoice";

type EditRequest = Selection & {
  text: string;
  basisTakeId: string;
  referenceTakeId?: string;
  passageTrimDb?: number;
};
type PendingKeep = { key: string; takeId: string; sha256: string };

export default function MixEditor() {
  const { id } = useParams();
  return id ? <Editor key={id} id={id} /> : null;
}
function Editor({ id }: { id: string }) {
  const client = useQueryClient(),
    key = ["finished-mix", id],
    url = "/" + id;
  const query = useQuery({
    queryKey: key,
    queryFn: () => mixRequest<Mix>(url),
    refetchInterval: (q) =>
      ["PREPARING", "DECODING"].includes(q.state.data?.preparation ?? "")
        ? 350
        : false,
  });
  const mix = query.data;
  const accept = useCallback(
    (s: Mix) =>
      client.setQueryData<Mix>(["finished-mix", id], (old) =>
        old && old.revision > s.revision ? old : s,
      ),
    [client, id],
  );
  const player = useRef<HTMLAudioElement>(null);
  const lastPlaybackTime = useRef<number | null>(null);
  const latest = useRef<Mix | undefined>(undefined);
  latest.current = mix;
  const initialized = useRef(false),
    mounted = useRef(true);
  const legacyClear = useAudio().clear;
  const [active, setActive] = useState("original"),
    activeRef = useRef("original");
  const [heard, setHeard] = useState(""),
    heardRef = useRef("");
  const ack = useRef<Promise<void>>(Promise.resolve());
  const [candidate, setCandidate] = useState<Take | null>(null);
  const [position, setPosition] = useState(0),
    [playing, setPlaying] = useState(false),
    [loop, setLoop] = useState(true);
  const [text, setText] = useState(""),
    [busy, setBusy] = useState(false),
    [saving, setSaving] = useState(false);
  const [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const [alternative, setAlternative] = useState<{
    request: EditRequest;
    reply: Reply;
  } | null>(null);
  const [recovery, setRecovery] = useState<PendingKeep | null>(null);
  const editSequence = useRef(0),
    selectionSequence = useRef(0);
  const localSelection = useRef<Selection | null>(null);
  const [retryEdit, setRetryEdit] = useState<{
    body: EditRequest;
    key: string;
  } | null>(null);
  const slot = "mix-keep:" + id;

  function report(e: unknown) {
    if (mounted.current)
      setError(
        e instanceof Error ? e.message : "Could not complete this action.",
      );
  }
  useEffect(() => {
    mounted.current = true;
    legacyClear();
    void pendingCommand<PendingKeep>(slot)
      .then((c) => {
        if (mounted.current && c) setRecovery(c);
      })
      .catch(report);
    return () => {
      mounted.current = false;
      editSequence.current++;
      selectionSequence.current++;
    };
  }, [legacyClear, slot]);
  const audition = useCallback(
    (
      s: Mix,
      take: Take | null,
      start = true,
      range: Selection | null = s.selection,
      forceStart = false,
    ) => {
      const a = player.current;
      if (!a) return;
      const target = take?.id ?? "original",
        src = take?.audioUrl ?? s.sourceUrl ?? s.importUrl;
      const from = range ? range.startFrame / s.sampleRate : 0,
        to = range ? range.endFrame / s.sampleRate : s.durationMs / 1000;
      const offset =
        !forceStart &&
        a.currentTime >= Math.max(0, from - 1) &&
        a.currentTime < to + 1
          ? a.currentTime
          : Math.max(0, from - 1);
      if (take && take.id !== s.selectionBaseId) setCandidate(take);
      lastPlaybackTime.current = null;
      activeRef.current = target;
      setActive(target);
      heardRef.current = "";
      setHeard("");
      if (a.getAttribute("src") !== src) {
        a.src = src;
        a.load();
      }
      a.currentTime = offset;
      if (start)
        void a.play().catch((e) => {
          if (e.name !== "AbortError") report(e);
        });
    },
    [],
  );
  useEffect(() => {
    if (!mix || !player.current) return;
    if (!initialized.current) {
      initialized.current = true;
      localSelection.current = mix.selection;
      const resumeId = mix.takes.some((t) => t.kept)
        ? mix.keptTakeId
        : mix.heardTakeId;
      const saved = mix.takes.find((t) => t.id === resumeId);
      const belongsToSelection =
        saved &&
        (saved.id === mix.selectionBaseId ||
          (saved.selectionBaseId === mix.selectionBaseId &&
            saved.startFrame === mix.selection?.startFrame &&
            saved.endFrame === mix.selection?.endFrame));
      const resume =
        resumeId === "original" || belongsToSelection
          ? saved
          : mix.takes.find((t) => t.id === mix.selectionBaseId);
      audition(mix, resume ?? null, false);
    } else if (
      activeRef.current === "original" &&
      mix.sourceUrl &&
      player.current.getAttribute("src") === mix.importUrl
    ) {
      audition(mix, null, !player.current.paused);
    }
  }, [mix, audition]);

  function onClock(ended = false) {
    const a = player.current,
      s = latest.current;
    if (!a || !s || (a.paused && !ended) || a.seeking || a.readyState < 2)
      return;
    const pos = a.currentTime;
    const previous = lastPlaybackTime.current;
    lastPlaybackTime.current = pos;
    setPosition(pos);
    const range = localSelection.current ?? s.selection;
    const from = range ? range.startFrame / s.sampleRate : 0,
      to = range ? range.endFrame / s.sampleRate : s.durationMs / 1000;
    if (loop && range && pos >= to + 1 && !ended) {
      a.currentTime = Math.max(0, from - 1);
    }
    const target = activeRef.current;
    if (
      heardRef.current === target ||
      pos <= 0 ||
      (target !== "original" &&
        !(previous !== null && pos > previous && previous < to && pos > from))
    )
      return;
    heardRef.current = target;
    setHeard(target);
    // Serialize audible transitions so a slow acknowledgement cannot overwrite a later audition.
    ack.current = ack.current
      .catch(() => {})
      .then(async () => {
        const state = await mixRequest<Mix>(url + "/heard", "PUT", {
          takeId: target,
        });
        if (mounted.current) accept(state);
      });
    void ack.current.catch((e) => {
      if (heardRef.current === target) {
        heardRef.current = "";
        setHeard("");
      }
      report(e);
    });
  }

  async function select(range: Selection, take?: Take) {
    if (!mix) return;
    const seq = ++selectionSequence.current;
    editSequence.current++;
    setRetryEdit(null);
    setAlternative(null);
    setMessage("");
    setError("");
    setBusy(false);
    localSelection.current = range;
    try {
      const s = await mixRequest<Mix>(url + "/selection", "PUT", {
        ...range,
        basisTakeId: take?.id,
      });
      if (!mounted.current || seq !== selectionSequence.current) return;
      accept(s);
      const base =
        take ?? s.takes.find((t) => t.id === s.selectionBaseId) ?? null;
      if (!base) setCandidate(null);
      audition(s, base, true, range, true);
    } catch (e) {
      if (seq === selectionSequence.current) {
        localSelection.current = mix.selection;
        report(e);
      }
    }
  }
  function changeAudition(t: Take | null) {
    if (!mix) return;
    editSequence.current++;
    setBusy(false);
    audition(mix, t);
  }
  async function choose(t: Take) {
    if (!mix) return;
    if (
      !mix.selection ||
      t.startFrame !== mix.selection.startFrame ||
      t.endFrame !== mix.selection.endFrame ||
      t.selectionBaseId !== mix.selectionBaseId
    ) {
      await select({ startFrame: t.startFrame, endFrame: t.endFrame }, t);
    } else changeAudition(t);
  }
  async function submit(
    request?: EditRequest,
    requestKey = crypto.randomUUID() as string,
  ) {
    if (!mix?.selection) return;
    const body = request ?? {
      text: text.trim(),
      basisTakeId: heardRef.current,
      ...mix.selection,
    };
    if (!body.text || !body.basisTakeId) return;
    setRetryEdit(null);
    const seq = ++editSequence.current;
    setBusy(true);
    setMessage("");
    setError("");
    setAlternative(null);
    try {
      await ack.current;
      const reply = await mixRequest<Reply>(
        url + "/edits",
        "POST",
        body,
        requestKey,
      );
      if (!mounted.current || seq !== editSequence.current) {
        if (reply.take)
          void client.invalidateQueries({ queryKey: ["finished-mix", id] });
        return;
      }
      setMessage(reply.message);
      if (reply.take) {
        const take = reply.take;
        client.setQueryData<Mix>(key, (s) =>
          s && !s.takes.some((t) => t.id === take.id)
            ? { ...s, takes: [...s.takes, take] }
            : s,
        );
        setText("");
        audition(mix, take);
        void query.refetch();
      } else setAlternative({ reply, request: body });
    } catch (e) {
      if (seq === editSequence.current) {
        if (!(e instanceof MixError) || e.status >= 500)
          setRetryEdit({ body, key: requestKey });
        report(e);
        void query.refetch();
      }
    } finally {
      if (mounted.current && seq === editSequence.current) setBusy(false);
    }
  }
  async function keep(command?: PendingKeep) {
    if (!mix || saving) return;
    const target = mix.takes.find((t) => t.id === heardRef.current);
    const c =
      command ??
      (target
        ? { key: crypto.randomUUID(), takeId: target.id, sha256: target.sha256 }
        : null);
    if (!c) return;
    setSaving(true);
    setError("");
    try {
      await pendingCommand(slot, c);
      setRecovery(c);
      await ack.current;
      const state = await mixRequest<Mix>(
        url + "/keep",
        "POST",
        { takeId: c.takeId, sha256: c.sha256 },
        c.key,
      );
      await pendingCommand(slot, null, c.key);
      if (mounted.current) {
        accept(state);
        setRecovery(null);
        setMessage(
          "Kept " +
            (state.takes.find((t) => t.id === c.takeId)?.label ?? "take") +
            ".",
        );
      }
    } catch (e) {
      if (e instanceof MixError && e.status >= 400 && e.status < 500) {
        await pendingCommand(slot, null, c.key);
        if (mounted.current) setRecovery(null);
      }
      report(e);
    } finally {
      if (mounted.current) setSaving(false);
    }
  }
  async function undo() {
    if (!mix) return;
    setError("");
    setSaving(true);
    try {
      const state = await mixRequest<Mix>(url + "/undo", "POST", {
        takeId: mix.keptTakeId,
      });
      accept(state);
      localSelection.current = state.selection;
      const parent = state.takes.find((t) => t.id === state.keptTakeId) ?? null;
      audition(state, parent);
      setMessage(
        "Returned to " + (parent ? "Take " + parent.number : "Original") + ".",
      );
    } catch (e) {
      report(e);
    } finally {
      if (mounted.current) setSaving(false);
    }
  }
  if (query.isPending)
    return (
      <p className="standalone" role="status">
        Opening mix…
      </p>
    );
  if (!mix)
    return (
      <p role="alert" className="standalone">
        {query.error?.message}
        <Link to="/mixes"> Open mixes</Link>
      </p>
    );
  const heardTake = mix.takes.find((t) => t.id === heard);
  const kept = mix.takes.find((t) => t.id === mix.keptTakeId && t.kept);
  const basis = mix.takes.find((t) => t.id === mix.selectionBaseId) ?? null;
  const ready = mix.preparation === "READY";
  const frozen = mix.selection;
  return (
    <div className="mix-editor">
      <header className="mix-heading">
        <Link to="/mixes">
          <ArrowLeft size={16} />
          Your mixes
        </Link>
        <h1>{mix.title.replace(/\.mp3$/i, "")}</h1>
        <span className="mix-metadata">
          Stereo MP3 <span>·</span> {formatTime(mix.durationMs / 1000)}{" "}
          <span>·</span> Local project
        </span>
      </header>
      <audio
        data-mix-player=""
        data-take-id={active}
        ref={player}
        preload="auto"
        onTimeUpdate={() => onClock()}
        onSeeking={() => {
          lastPlaybackTime.current = null;
        }}
        onSeeked={(e) => {
          lastPlaybackTime.current = e.currentTarget.currentTime;
        }}
        onPlaying={(e) => {
          lastPlaybackTime.current = e.currentTarget.currentTime;
          setPlaying(true);
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          onClock(true);
          if (loop && frozen && player.current) {
            player.current.currentTime = Math.max(
              0,
              frozen.startFrame / mix.sampleRate - 1,
            );
            void player.current.play().catch(report);
          } else setPlaying(false);
        }}
        onError={() =>
          setError("Could not play this recording. Reopen the take to retry.")
        }
      />
      <div className="mix-edit-grid">
        <section className="mix-main">
          <Passage
            mix={mix}
            position={position}
            onSelect={(r) => void select(r)}
            onSeek={(sec) => {
              if (player.current) {
                player.current.currentTime = sec;
                setPosition(sec);
              }
            }}
          />
          <div className="mix-transport">
            <Button
              variant="outline"
              aria-label={playing ? "Pause mix" : "Play mix"}
              onClick={() => {
                const a = player.current;
                if (!a) return;
                if (a.paused) void a.play().catch(report);
                else a.pause();
              }}
            >
              {playing ? <Pause /> : <Play />}
            </Button>
            <Button
              variant="outline"
              aria-pressed={active === (basis?.id ?? "original")}
              onClick={() => changeAudition(basis)}
            >
              {basis ? "A · Take " + basis.number : "A · Original"}
            </Button>
            {candidate && (
              <Button
                variant="outline"
                aria-pressed={active === candidate.id}
                onClick={() => changeAudition(candidate)}
              >
                B · Take {candidate.number}
              </Button>
            )}
            <label>
              <input
                type="checkbox"
                checked={loop}
                onChange={(e) => setLoop(e.target.checked)}
              />{" "}
              Loop passage
            </label>
          </div>
          <p role="status" className="mix-status">
            {mix.preparation === "DECODING"
              ? "Preparing the original waveform…"
              : mix.preparation === "PREPARING"
                ? "Separating vocals and drums on this Mac…"
                : mix.preparation === "FAILED"
                  ? mix.error
                  : ready
                    ? "Passage ready for edits."
                    : "Select a passage to begin editing."}
          </p>
          {mix.preparation === "FAILED" && (
            <Button
              variant="outline"
              onClick={() => {
                if (frozen) void select(frozen);
                else
                  void mixRequest<Mix>(url + "/retry", "POST")
                    .then(accept)
                    .catch(report);
              }}
            >
              Retry preparation
            </Button>
          )}
          <section className="mix-conversation">
            <h2>Describe your edit</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void submit();
              }}
            >
              <textarea
                aria-label="Edit request"
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (
                    (e.metaKey || e.ctrlKey) &&
                    e.key === "Enter" &&
                    ready &&
                    heard &&
                    !busy &&
                    text.trim()
                  ) {
                    e.preventDefault();
                    void submit();
                  }
                }}
                placeholder="Bring the vocal forward here…"
                rows={2}
              />
              <div>
                <span>
                  {heardTake
                    ? "Editing from Take " + heardTake.number
                    : heard === "original"
                      ? "Editing from Original"
                      : "Play a take to use it as the basis."}
                </span>
                <Button disabled={!ready || !heard || !text.trim() || busy}>
                  <Send size={16} />
                  {busy ? "Preparing preview…" : "Preview edit"}
                </Button>
              </div>
            </form>
            {message && (
              <p role="status" className="mix-reply">
                {message}
              </p>
            )}
            {alternative?.reply.control &&
              alternative.reply.suggestedGainDb != null &&
              alternative.reply.suggestedGainDb !== 0 && (
                <Button
                  variant="outline"
                  onClick={() =>
                    void submit({
                      ...alternative.request,
                      text:
                        "Set " +
                        alternative.reply.control +
                        " to " +
                        alternative.reply.suggestedGainDb +
                        " dB",
                      referenceTakeId: undefined,
                    })
                  }
                >
                  Try {alternative.reply.control} at{" "}
                  {level(alternative.reply.suggestedGainDb)}
                </Button>
              )}
            {alternative?.reply.suggestedTrimDb != null && (
              <Button
                variant="outline"
                onClick={() =>
                  void submit({
                    ...alternative.request,
                    passageTrimDb: alternative.reply.suggestedTrimDb!,
                  })
                }
              >
                Preview with passage trim (
                {level(alternative.reply.suggestedTrimDb)})
              </Button>
            )}
            {alternative?.reply.status === "CLARIFY" &&
              !alternative.reply.choices.length && (
                <LevelChoice
                  key={alternative.request.basisTakeId}
                  basis={
                    alternative.request.basisTakeId === mix.selectionBaseId
                      ? undefined
                      : mix.takes.find(
                          (t) => t.id === alternative.request.basisTakeId,
                        )
                  }
                  enabled={
                    ready && !busy && heard === alternative.request.basisTakeId
                  }
                  onConfirm={(control, gain) =>
                    void submit({
                      ...alternative.request,
                      text: "Set the " + control + " to " + gain + " dB.",
                      referenceTakeId: undefined,
                    })
                  }
                />
              )}
            {alternative?.reply.choices.map((t) => (
              <div key={t.id} className="mix-choice">
                <Button variant="ghost" onClick={() => void choose(t)}>
                  Listen to Take {t.number}
                </Button>
                <Button
                  variant="outline"
                  onClick={() =>
                    void submit({
                      ...alternative.request,
                      referenceTakeId: t.id,
                    })
                  }
                >
                  Use {alternative.reply.control} from Take {t.number}
                </Button>
              </div>
            ))}
            {retryEdit && (
              <Button
                variant="outline"
                onClick={() => void submit(retryEdit.body, retryEdit.key)}
              >
                Retry this preview
              </Button>
            )}
            {error && (
              <p role="alert" className="notice error">
                {error}
              </p>
            )}
          </section>
        </section>
        <aside className="mix-takes">
          <div className="mix-take-heading">
            <h2>Takes</h2>
            {mix.takes.length > 0 && <span>{mix.takes.length}</span>}
          </div>
          <div className="mix-save">
            <Button
              disabled={!heardTake || saving || !!recovery}
              onClick={() => void keep()}
            >
              <Check />
              Keep heard take
            </Button>
            {recovery && !saving && (
              <Button variant="outline" onClick={() => void keep(recovery)}>
                Recover saved take
              </Button>
            )}
            <Button
              variant="outline"
              disabled={!kept || saving}
              onClick={() => void undo()}
            >
              <RotateCcw />
              Undo keep
            </Button>
            {kept && (
              <a href={kept.audioUrl.replace(/\/audio$/, "/export")} download>
                <Download size={16} />
                Export kept WAV
              </a>
            )}
          </div>
          {mix.takes.length === 0 && <p>Your previews will appear here.</p>}
          {basis && (
            <button
              className="text-action"
              onClick={() => changeAudition(null)}
            >
              Play original recording
            </button>
          )}
          {[...mix.takes]
            .sort((a, b) => b.number - a.number)
            .map((t) => (
              <article key={t.id} className={active === t.id ? "current" : ""}>
                <button
                  aria-label={"Play Take " + t.number}
                  onClick={() => void choose(t)}
                >
                  <Play size={14} />
                  <strong>Take {t.number}</strong>
                  {t.kept && <Bookmark size={14} />}
                </button>
                <span>{t.text}</span>
                <div className="mix-knobs">
                  <small>Vocals {level(t.vocalsDb)}</small>
                  <small>Drums {level(t.drumsDb)}</small>
                </div>
                <small>
                  {t.passageTrimDb
                    ? "Passage " + level(t.passageTrimDb) + " · "
                    : ""}
                  {formatTime(t.startFrame / mix.sampleRate)}–
                  {formatTime(t.endFrame / mix.sampleRate)}
                </small>
                {t.sourceTakeId && (
                  <small>
                    Copied from Take{" "}
                    {mix.takes.find((o) => o.id === t.sourceTakeId)?.number}
                  </small>
                )}
              </article>
            ))}
        </aside>
      </div>
    </div>
  );
}

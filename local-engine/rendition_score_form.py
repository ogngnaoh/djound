"""Omit an authored introduction or coda on its score clock, preserving the performed body."""
import os
from pathlib import Path
import re
import tempfile
import wave


REMOVE = re.compile(r"^(?:(?:can|could|would) (?:we|you) |(?:please|let['’]s) )")
ACTION = r"(?:lose|skip|drop|remove|cut(?: off| out)?|take (?:off|out)|get rid of)"
EDGES = {
    "intro": r"(?:(?:band(?:-only)? |instrumental )?(?:intro(?:duction)?|lead[ -]in)|"
             r"(?:extra )?(?:bit|part|bars) (?:at the (?:start|beginning)|before (?:the )?(?:tune|melody) (?:comes in|starts)))",
    "outro": r"(?:(?:band(?:-only)? |instrumental )?(?:outro|coda)|"
             r"(?:extra )?(?:bit|part|bars) (?:at the end|(?:where|when) the band "
             r"(?:carries on|keeps playing|plays on) (?:at the end|after (?:the )?(?:tune|melody)(?: ends)?)))",
}


def requested_cut(direction, plan):
    """A clear removal instruction may include praise or a compatible statement of the desired result."""
    from rendition_score_plan import _marked

    edge = None
    finishes = False
    protected = set()
    for sentence in re.split(r"[.!?;]|(?:,\s*|\s+and\s+)(?=keep\b)", direction.strip().casefold()):
        words = REMOVE.sub("", sentence.strip(), count=1) if sentence.strip() else ""
        if not words:
            continue
        if re.fullmatch(r"(?:i (?:really )?(?:like|love)|keep) (?:the |this |that )?"
                        r"(?:beginning|ending|rest|groove|start|take|band|performance|everything(?: else)?)"
                        r"(?: and everything leading up to it| (?:just |exactly )?(?:as it is|the same|unchanged))?", words):
            if re.search(r"\b(?:beginning|start)\b", words):
                protected.add("intro")
            if "ending" in words:
                protected.add("outro")
            continue
        finish = re.fullmatch(r"(?:i (?:want|would like) (?:it|this|the song|the take) to )?"
                              r"(?:end|finish) with (.+)", words)
        if finish:
            part = finish[1]
            if (part not in ("the melody", "the theme", "the tune", "the lead", "the final note")
                    and not re.fullmatch(r"(?:the |a )?@" + str(plan["leadProgram"]) + "@", _marked(part))):
                return None
            finishes = True
            continue
        found = next((k for k, name in EDGES.items()
                      if re.fullmatch(ACTION + r" (?:the |that |this |any )?" + name, words)), None)
        if not found or (edge and found != edge):
            return None
        edge = found
    return None if edge in protected or (finishes and edge != "outro") else edge


def omit_edge(plan, edge):
    from rendition_score_plan import ArrangementClarification

    omitted = set(plan.get("omittedForm", []))
    bars = plan["introBars" if edge == "intro" else "endingBars"]
    if edge in omitted or not bars or not (plan["roles"] or plan.get("keepEmptyBars")):
        raise ArrangementClarification(f"This take already plays without an added {edge}.")
    return dict(plan, omittedForm=sorted(omitted | {edge}))


def score_window(plan, mapping):
    """Map from the full authored score to the heard portion; edges bracket zero source-time holds."""
    start, end = 0, mapping[-1][0]
    omitted = plan.get("omittedForm", ())
    if "intro" in omitted and mapping[1][1] == 0:
        start = mapping[1][0]
    if "outro" in omitted and plan["endingBars"] and mapping[-2][1] == mapping[-1][1]:
        end = mapping[-2][0]
    heard = [[x - start, y] for x, y in mapping if start <= x <= end]
    return start, end, heard


def cut_wav(source, output, start, end):
    """Copy PCM unchanged away from a ten-millisecond anti-click fade at each new boundary."""
    import numpy as np

    with tempfile.NamedTemporaryFile(dir=Path(output).parent) as staged:
        with wave.open(str(source), "rb") as original, wave.open(staged, "wb") as out:
            if original.getsampwidth() != 2 or not 0 <= start < end <= original.getnframes():
                raise ValueError("The authored section lies outside the saved performance.")
            out.setparams(original.getparams())
            original.setpos(start)
            fade = min(original.getframerate() // 100, (end - start) // 2)
            for at in range(start, end, 32768):
                raw = original.readframes(min(32768, end - at))
                frames = np.frombuffer(raw, dtype="<i2").reshape(-1, original.getnchannels())
                if fade and ((start and at < start + fade) or (end < original.getnframes() and at + len(frames) > end - fade)):
                    index = np.arange(at, at + len(frames))
                    gain = np.ones(len(frames))
                    if start:
                        gain = np.minimum(gain, (index - start) / max(1, fade - 1))
                    if end < original.getnframes():
                        gain = np.minimum(gain, (end - 1 - index) / max(1, fade - 1))
                    raw = np.rint(frames * gain[:, None]).astype("<i2").tobytes()
                out.writeframesraw(raw)
        staged.flush()
        os.link(staged.name, output)

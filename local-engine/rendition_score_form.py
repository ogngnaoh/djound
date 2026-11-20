"""Omit an authored introduction or coda on its score clock, preserving the performed body."""
import math
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


def requested_restore(direction, plan):
    """Restore a particular saved section without reinterpreting a second removed edge."""
    if not plan.get("omittedForm"):
        return None
    words = direction.strip(" .!?").casefold()
    clauses = re.split(r"[,.;!?]\s*|\s+(?:and|but)\s+(?=(?:leave|keep)\b)", words)
    words = REMOVE.sub("", clauses[0], count=1)
    edge = r"(?:the |my |our |its )?(?:original |previous |earlier |old |removed )?(intro(?:duction)?|lead[ -]in|outro|coda)"
    match = (re.fullmatch(r"(?:restore|reinstate|bring back|put back|add back|recover) " + edge, words)
             or re.fullmatch(r"(?:bring|put|add|give me) " + edge + r" back", words))
    if not match:
        return None
    found = "outro" if match[1] in ("outro", "coda") else "intro"
    for other in clauses[1:]:
        keep = re.fullmatch(r"(?:and |but )?(?:keep|leave) " + edge + r" (?:out|off|cut|removed|alone|as it is)", other)
        if not keep or ("outro" if keep[1] in ("outro", "coda") else "intro") == found:
            return None
    return found if found in plan["omittedForm"] else None


def restore_edge(plan, edge):
    plan = dict(plan)
    remaining = [name for name in plan["omittedForm"] if name != edge]
    if remaining:
        plan["omittedForm"] = remaining
    else:
        plan.pop("omittedForm")
    if edge == "outro":
        plan.pop("outroCutVersion", None)
        plan.pop("outroReleaseFrames", None)
    return plan


def newly_written_edges(direction):
    """A newly requested introduction or coda supersedes that edge's saved omission."""
    edges = set()
    for clause in re.split(r"[.;!?]|\b(?:and|but|while)\b", direction.casefold()):
        if re.search(r"\b(?:no|without|don't|never|not|keep|leave)\b", clause):
            continue
        if not re.search(r"\b(?:add|write|compose|create|give|include|bring back|restore|lengthen|extend|longer)\b", clause):
            continue
        for word in re.findall(r"\b(?:intro(?:duction)?|outro|coda)\b", clause):
            edges.add("outro" if word in ("outro", "coda") else "intro")
    return edges


def omit_edge(plan, edge):
    from rendition_score_plan import ArrangementClarification

    omitted = set(plan.get("omittedForm", []))
    bars = plan["introBars" if edge == "intro" else "endingBars"]
    if edge in omitted or not bars or not (plan["roles"] or plan.get("keepEmptyBars")):
        raise ArrangementClarification(f"This take already plays without an added {edge}.")
    revised = dict(plan, omittedForm=sorted(omitted | {edge}))
    if edge == "outro":
        revised["outroCutVersion"] = 2
    return revised


def measure_outro(plan, tracks, mapping):
    """The last melody event can sound past the chart's coda downbeat after expressive timing."""
    if plan.get("outroCutVersion") != 2 or "outro" not in plan.get("omittedForm", ()):
        return
    from rendition_score_render import RATE

    body = mapping[-2][0]
    # Leave the performer the same 450 ms instrument release already used by a full score.
    last_release = max((n[1] + .45 for n in tracks[0]["notes"]), default=0.)
    stop = min(mapping[-1][0], math.ceil(last_release * RATE))
    plan["outroReleaseFrames"] = max(0, stop - body)


def score_window(plan, mapping):
    """Map from the full authored score to the heard portion; edges bracket zero source-time holds."""
    start, end = 0, mapping[-1][0]
    omitted = plan.get("omittedForm", ())
    if "intro" in omitted and mapping[1][1] == 0:
        start = mapping[1][0]
    if "outro" in omitted and plan["endingBars"] and mapping[-2][1] == mapping[-1][1]:
        extra = plan.get("outroReleaseFrames", 0) if plan.get("outroCutVersion") == 2 else 0
        end = min(mapping[-1][0], mapping[-2][0] + extra)
    heard = [[x - start, y] for x, y in mapping if start <= x <= end]
    if heard[-1][0] < end - start:
        heard.append([end - start, mapping[-1][1]])
    return start, end, heard


def cut_wav(source, output, start, end, *, solo=None, solo_at=None, clock_offset=0):
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
                if solo is not None and at + len(frames) > solo_at - fade:
                    solo.setpos(at + clock_offset)
                    isolated = np.frombuffer(solo.readframes(len(frames)), "<i2").reshape(frames.shape)
                    amount = np.clip((np.arange(at, at + len(frames)) - solo_at + fade) / max(1, fade), 0., 1.)
                    frames = np.rint(frames * (1 - amount[:, None]) + isolated * amount[:, None]).astype("<i2")
                    raw = frames.tobytes()
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


def render_saved(tracks, seconds, plan, destination, deadline, *, lead_only=False):
    from rendition_score_render import render_score

    master = plan.get("scoreMasterGain")
    gains = plan.get("scorePartGains")
    if lead_only:
        if gains is None:
            # Older master-only receipts applied the lead trim after summing the band.
            # Recover its actual multiplier before isolating that same player.
            with tempfile.TemporaryDirectory(dir=Path(destination).parent) as tmp:
                receipt = {}
                render_score(tracks, seconds, Path(tmp) / "reference.wav", deadline=deadline,
                             room=plan.get("room", .44), master_gain=master, receipt=receipt)
                master, gains = receipt["scoreMasterGain"], receipt["scorePartGains"]
        tracks = [tracks[0], *(dict(part, notes=[]) for part in tracks[1:])]
    render_score(tracks, seconds, destination, deadline=deadline, room=plan.get("room", .44),
                 master_gain=master, part_gains=gains)


def export_window(source, output, plan, tracks, seconds, mapping, deadline, *, clock_offset=0):
    """Copy the heard score, letting the melody release without adding another coda attack."""
    from rendition_score_render import RATE

    start, end, _ = score_window(plan, mapping)
    boundary = mapping[-2][0]
    collision = None
    if plan.get("outroCutVersion") == 2 and "outro" in plan.get("omittedForm", ()) and end > boundary:
        collision = min((round(at * RATE) for part in tracks[1:] for at, _, _, _ in part["notes"]
                         if boundary <= round(at * RATE) < end), default=None)
    if collision is None:
        cut_wav(source, output, start - clock_offset, end - clock_offset)
    else:
        with tempfile.TemporaryDirectory(dir=Path(output).parent) as tmp:
            isolated = Path(tmp) / "closing-lead.wav"
            render_saved(tracks, seconds, plan, isolated, deadline, lead_only=True)
            with wave.open(str(isolated), "rb") as solo:
                cut_wav(source, output, start-clock_offset, end-clock_offset, solo=solo,
                        solo_at=collision-clock_offset, clock_offset=clock_offset)

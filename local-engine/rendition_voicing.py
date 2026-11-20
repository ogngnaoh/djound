"""Select one guide sound from the GM bank. The music model receives the full arrangement request."""

from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from common import HERE, HOME

# Aliases for common instrument names absent from the official patch names.
# Roles and ensemble size are interpreted independently of genre.
ALIASES = {
    "piano": 0,
    "acoustic piano": 0,
    "grand piano": 0,
    "pianist": 0,
    "keyboard": 4,
    "electric piano": 4,
    "organ": 16,
    "guitar": 25,
    "acoustic guitar": 25,
    "nylon guitar": 24,
    "electric guitar": 27,
    "upright bass": 32,
    "double bass": 43,
    "bass guitar": 33,
    "electric bass": 33,
    "bass": 32,
    "cello": 42,
    "cellist": 42,
    "violinist": 40,
    "harp": 46,
    "strings": 48,
    "string ensemble": 48,
    "brass section": 61,
    "sax": 66,
    "saxophone": 66,
    "alto saxophone": 65,
    "tenor saxophone": 66,
    "bells": 14,
    "vibraphonist": 11,
    "vibes": 11,
    "drums": 118,
    "synth": 80,
}
INSTRUMENT = r"@(\d+)@"
SOLO = re.compile(
    r"\b(?:solo|unaccompanied|just|only)\s+(?:a |the |one )?"
    + INSTRUMENT
    + r"|"
    + INSTRUMENT
    + r"\s+(?:alone|only|on its own|by itself)\b"
)
LEAD = re.compile(
    INSTRUMENT + r"\s+(?:(?:takes?|carr(?:y|ies|ying)|play(?:s|ing)?|states?|sings?|rephrases?|has|gets?)\s+"
    r"(?:(?:the|my|our|source|original|main|lead|familiar)\s+)*(?:theme|tune|melody|lead|motif|line|hook)\b"
    r"|(?:in|on)\s+(?:the\s+)?(?:lead|melody)\b|leads?\b)"
    r"|(?:\b(?:lead|melody|theme|tune|line|hook)\s+(?:to|on|for|with|played by)\s+(?:the |a )?)" + INSTRUMENT
)
GIVE_LEAD = re.compile(
    r"\b(?:give|hand|assign)\s+(?:the |a )?"
    + INSTRUMENT
    + r"\s+(?:(?:the|my|our|source|original|main|lead|familiar)\s+)*(?:theme|tune|melody|motif|line|hook)\b"
)
NEGATED = re.compile(
    r"\b(?:no|not|never|without|except|replace|instead of|drop|omit|remove|avoid)\b|n['’]t\b", re.I
)
ENSEMBLE = re.compile(
    r"\b(?:duet|duo|trio|quartet|quintet|band|orchestra|ensemble|backing|accompanied|accompanies)\b"
)
ROLES = """An arranger will receive the complete musical request. Your only task is to identify which instrument carries the tune.
The listener's latest request takes precedence. Accompanists do not carry the tune.
Choose from the supplied instruments and answer only with its exact name. Example: Cello."""
SIZE = """Classify the requested overall instrumental arrangement. Reply with one word: ONE or GROUP.
ONE means the listener wants only one instrument or performer throughout, without backing players.
GROUP means accompaniment, a duet, an ensemble, additional layers, or no explicit request for just one instrument.
A solo performed OVER other instruments counts as GROUP. An unaccompanied solo instrument counts as ONE.
Use the listener's latest request and the proposed arrangement to decide."""


@lru_cache(maxsize=1)
def _catalog():
    from pretty_midi import INSTRUMENT_MAP

    names = {name.lower(): program for program, name in enumerate(INSTRUMENT_MAP)}
    names.update(ALIASES)
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(?!\w)"
    )
    return list(INSTRUMENT_MAP), names, pattern


def _direction(text):
    _, names, pattern = _catalog()
    return pattern.sub(lambda match: f"@{names[match.group()]}@", text.casefold().replace("@", " "))


def _programs(text):
    return {int(x) for x in re.findall(INSTRUMENT, _direction(text))}


def _role_names(marked):
    roles = {int(next(x for x in match.groups() if x is not None)) for match in LEAD.finditer(marked)}
    roles.update(int(match[1]) for match in GIVE_LEAD.finditer(marked))
    # In a descriptive clause the melody and its accompaniment are often named separately:
    # "the ... intones the theme above ...". The verb and the adjectives can vary.
    for clause in re.split(r"[.;!?]", marked):
        parts = re.split(
            r"\b(?:over|above|against|backed by|supported by|accompanied by)\b", clause, maxsplit=1
        )
        if len(parts) == 2 and re.search(r"\b(?:melody|tune|theme|hook|motif|lead)\b", parts[0]):
            candidates = set(map(int, re.findall(INSTRUMENT, parts[0])))
            if len(candidates) == 1:
                roles.update(candidates)
    return roles


def _excluded(program, direction):
    for clause in re.split(r"[,;.!?]|\bbut\b", direction, flags=re.I):
        for negative in NEGATED.finditer(clause):
            if program in _programs(clause[negative.end() :]):
                return True
    return False


def guide_named(program: int, direction: str) -> bool:
    """Whether the text explicitly names this guide sound as an unambiguous musical choice."""
    if type(program) is not int or not 0 <= program < 128 or not isinstance(direction, str):
        return False
    if _excluded(program, direction) or program not in _programs(direction):
        return False
    affirmative = "; ".join(
        c for c in re.split(r"[,;.!?]|\bbut\b", direction, flags=re.I) if not NEGATED.search(c)
    )
    marked = _direction(affirmative)
    roles = _role_names(marked)
    return roles == {program} if roles else _programs(affirmative) == {program}


# Base can emit instrument families. Map a named backing instrument only where that
# family describes it; a valid GM patch for the guide need not have a usable Base lane.
def _acoustic_lane(program, text):
    if 0 <= program <= 3 or program in (6, 7, 16, 17, 19, 20, 21, 23):
        return "keyboard"
    if 8 <= program <= 15 or program in (47, 108, 112, 113, 114, 115, 116):
        return "percussion"
    if program in (24, 25):
        return "guitar"
    if program in (32, 43):
        return "bass"
    if 40 <= program <= 42 or program in (44, 45, 46, 110):
        return "strings"
    if 56 <= program <= 60:
        return "brass"
    if 64 <= program <= 76 or program in (22, 77, 79):
        return "woodwinds"
    if program == 118 and re.search(r"\b(?:drums|drummer|drum kit)\b", text, re.I):
        return "drums"
    return None


DUO = re.compile(r"\b(?:duet|duo|two (?:players|instruments|performers)|just (?:the |these )?two)\b")
EXTRA_CAST = re.compile(
    r"\b(?:trio|quartet|quintet|sextet|septet|octet|orchestra|ensemble|band|section|"
    r"voices?|vocals?|singers?|singing voice|choir|rapping|spoken|sing along|"
    r"another|additional|extra|third|plus|etc)\b",
    re.I,
)
HISTORICAL = re.compile(
    r"\b(?:previous|earlier|former|used to|last take|old arrangement|"
    r"in take \d+|was playing|were playing|reminds? me of)\b",
    re.I,
)
SUPPORT = re.compile(
    INSTRUMENT + r"\s+(?:(?:plays?|provides?|adds?|supplies)\s+(?:(?:the|soft|simple|"
    r"rhythmic|quiet)\s+)*)?(?:accompaniment|backing|chords|comping|underneath|beneath)\b"
    r"|" + INSTRUMENT + r"\s+(?:backs?|supports?|accompan(?:y|ies))\s+"
    r"(?:it|the\b|my\b|our\b|@" + r"\d+@)"
    r"|(?:backed|supported|accompanied)\s+by\s+(?:(?:a|an|the|fingerpicked|bowed|"
    r"plucked|strummed|soft|muted|warm)\s+)*" + INSTRUMENT
)


def accompaniment_lane(program: int, direction: str) -> str | None:
    """Resolve one explicitly named support for a two-instrument arrangement, else defer to the music model."""
    if type(program) is not int or not 0 <= program <= 127 or not isinstance(direction, str):
        return None
    text = direction.casefold()
    if NEGATED.search(text) or EXTRA_CAST.search(text) or HISTORICAL.search(text):
        return None
    marked = _direction(text)
    instruments = set(map(int, re.findall(INSTRUMENT, marked)))
    if len(instruments) != 2 or program not in instruments:
        return None
    # Unsupported names do not become invisible third players in an otherwise known pair.
    for extra in re.finditer(r"@\d+@\s*(?:,?\s*and|&|\+)\s+([^.;!?]+)", marked):
        if not re.search(INSTRUMENT, extra[1]):
            return None
    for addition in re.finditer(r"\b(?:add|bring in|introduce|include)\s+([^,.;!?]+)", marked):
        if not re.search(INSTRUMENT, addition[1]):
            return None
    other = next(iter(instruments - {program}))
    leads = _role_names(marked)
    support = {
        int(next(value for value in match.groups() if value is not None)) for match in SUPPORT.finditer(marked)
    }
    for clause in re.split(r"[.;!?]", marked):
        left, *right = re.split(
            r"\b(?:with|over|above|backed by|supported by|accompanied by)\b", clause, maxsplit=1
        )
        if right and set(re.findall(INSTRUMENT, left)) == {str(program)}:
            support.update(map(int, re.findall(INSTRUMENT, right[0])))
    if leads and leads != {program} or support and support != {other}:
        return None
    # A duet named by itself leaves the roles open. With no explicit "duet", require
    # named lead and backing roles, rather than treating any two mentions as a lineup.
    if not ((DUO.search(marked) and (leads or support)) or (leads and support)):
        return None
    return _acoustic_lane(other, text)


def preserve_register(direction: str) -> bool:
    """Recognize explicit pitch placement, independently of mood, energy and volume."""
    if not isinstance(direction, str):
        return False
    if re.search(
        r"\b(?:any octave|(?:change|choose|adjust) the (?:octave|register) freely|"
        r"(?:don't|do not) (?:keep|preserve|lock) .{0,24}(?:octave|register))\b",
        direction,
        re.I,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:octaves?|register|concert pitch|original pitch|same pitch|middle c)\b"
            r"|(?<!\w)[A-G](?:#|♯|♭|b)?[1-8](?!\w)",
            direction,
            re.I,
        )
    )


def _explicit(text):
    if not text or NEGATED.search(text):
        return None
    marked = _direction(text)
    programs = set(map(int, re.findall(INSTRUMENT, marked)))
    if not programs:
        return None
    solo = SOLO.search(marked)
    if solo and len(programs) == 1 and not ENSEMBLE.search(marked):
        return next(iter(programs)), True
    roles = _role_names(marked)
    if len(roles) == 1 and not solo:
        return roles.pop(), False
    if re.fullmatch(r"\s*(?:(?:a|the|an)\s+)?" + INSTRUMENT + r"[\s.!?]*", marked):
        return next(iter(programs)), False
    return None


def _size_hint(text):
    # A lead instrument can still be accompanied. Prefer an explicit ensemble, and
    # limit exclusions to their clause ("no strings, just the oboe on its own").
    if NEGATED.search(text):
        text = re.split(r"[,;]", text.rstrip(".!?"))[-1]
        if NEGATED.search(text):
            return None
    marked = _direction(text)
    programs = set(re.findall(INSTRUMENT, marked))
    if ENSEMBLE.search(marked) or (
        len(programs) > 1
        and re.search(
            r"\b(?:with|and|over|above|under|underneath|beneath|behind|while|supports?|answers?)\b", marked
        )
    ):
        return False
    if len(programs) <= 1 and (
        SOLO.search(marked) or re.search(r"\b(?:unaccompanied|one performer|one player|alone)\b", marked)
    ):
        return True
    return None


def _infer(request):
    import mlx.core as mx
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler

    spec = json.loads((HERE / "assets.json").read_text())
    weights = HOME / spec["modelDirectory"]
    if not (weights / "model.safetensors").is_file():
        raise RuntimeError("Set up the local language planner for guide voicing.")
    mx.set_memory_limit(2 * 1024**3)
    mx.set_cache_limit(128 * 1024**2)
    model, tokenizer = load(str(weights), tokenizer_config={"trust_remote_code": False})
    labels, _, _ = _catalog()
    context = json.dumps(request, ensure_ascii=False)

    def ask(system, text, max_tokens):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        return generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            sampler=make_sampler(temp=0),
            prefill_step_size=128,
            verbose=False,
        ).strip()

    mentioned = {
        int(x) for x in re.findall(INSTRUMENT, _direction(request["listener"] + " " + request["arrangement"]))
    }
    # Candidate names are drawn from the listener and arrangement, including every GM patch.
    # If none is explicit, let the planner name a fitting instrument for an unfamiliar musical direction.
    if len(mentioned) == 1 and not NEGATED.search(request["listener"]):
        program = next(iter(mentioned))
    else:
        choices = (
            ", ".join(labels[i] for i in sorted(mentioned)) if mentioned else "Any General MIDI instrument"
        )
        answer = ask(
            ROLES, context + "\nInstruments: " + choices + "\nWhich instrument plays the main tune?", 20
        )
        named = re.findall(INSTRUMENT, _direction(answer.splitlines()[0] if answer else ""))
        program = next((int(x) for x in named if not mentioned or int(x) in mentioned), 0)
    for direction in (request["listener"], request["arrangement"]):
        size = _size_hint(direction)
        if size is not None:
            return program, size
    size = ask(SIZE, context + "\nOne instrument throughout, or a group?", 6)
    return program, bool(re.match(r"ONE\b", size, re.I))


def choose_voicing(caption: str, prompt: str = "", *, deadline: float | None = None) -> tuple[int, bool]:
    """Return a zero-based GM program and whether this take calls for an unaccompanied arrangement."""
    if not isinstance(caption, str) or not isinstance(prompt, str) or not (caption.strip() or prompt.strip()):
        raise ValueError("Describe the instrumental arrangement.")
    if deadline is None:
        deadline = time.monotonic() + 25
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise TimeoutError("The guide voicing request timed out.")
    caption, prompt = caption.strip()[:4000], prompt.strip()[:2000]
    # The literal listener request, when specific, outranks an inferred description of the accompaniment.
    choice = _explicit(prompt)
    if choice is not None:
        return choice
    # Resolve the director's description after the literal listener. A named backing
    # instrument in the request does not override an explicitly identified lead in the caption.
    choice = _explicit(caption)
    if choice is not None and not _excluded(choice[0], prompt):
        requested_size = _size_hint(prompt)
        return choice if requested_size is None else (choice[0], requested_size)
    env = {
        **os.environ,
        "DJOUND_LOCAL_ENGINE_HOME": str(HOME),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    try:
        child = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve())],
            input=json.dumps({"listener": prompt, "arrangement": caption}),
            text=True,
            capture_output=True,
            timeout=max(0.001, min(25, deadline - time.monotonic())),
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError("Choosing the melody guide instrument exceeded its time limit.") from None
    if child.returncode != 0:
        raise RuntimeError("The local language planner could not choose a guide instrument.")
    try:
        program, solo = json.loads(child.stdout)
    except (TypeError, ValueError):
        return 0, False
    if type(program) is not int or not 0 <= program <= 127 or type(solo) is not bool:
        return 0, False
    return program, solo


if __name__ == "__main__":
    print(json.dumps(_infer(json.load(sys.stdin))))

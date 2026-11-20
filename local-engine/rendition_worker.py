"""Plan and perform one instrumental revision of a saved recording."""
import argparse
import gc
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import wave

from planner import decode_plan
from rendition_progress import report_stage

STYLE_SUGGESTIONS = {
    "jazz trio": "An intimate instrumental jazz trio. Expressive acoustic piano rephrases the source melody with upright bass and brushed drums.",
    "solo piano": "A complete solo acoustic piano performance. One pianist carries the source melody, including the original sung hook, with expressive accompaniment.",
    "lo-fi hip-hop": "Instrumental lo-fi hip-hop. A warm keyboard carries the source melody over a relaxed, dusty drum groove.",
    "cinematic strings": "An instrumental string ensemble performs the source tune, with a clear lyrical lead, swelling harmonies and a musical ending.",
}

ARRANGEMENT_DIRECTION = (
    "An instrumental cover of the supplied recording. State the main tune clearly, preserving its distinctive "
    "note shapes, phrase endings and signature motifs. Use the requested sound for the accompaniment, harmony, rhythm and "
    "phrasing. Return to the recognizable theme. Instruments carry any sung melody. No vocals or speech."
)

SYSTEM = """You direct edits to a real recording for a casual music listener. Every result is instrumental. Return one JSON object and no other text:
{"scope":"whole","caption":"...","label":"..."}

scope is whole, opening, ending, selection, or clarify. For broad changes to the style, mood or instrumentation use whole. Whole includes a new direction for the entire performance with an exception such as "leave the ending alone". For requests to change only the beginning or end use opening or ending. If a time range is selected, use selection. A selection limits the edit to that range.
caption describes the new music the listener wants to hear, in English. Use the listener's exact instruments, genres, combinations and sound descriptions, including unusual ones. A new direction overrides conflicting choices in the current take. The musical theme comes from the source recording. The arrangement comes from the listener's direction. The label is a short name for the result.
Treat a musical request phrased as a question as a direction to play it. Use clarify for a question with no musical edit, a request for vocals, or a specific passage whose location is unknown. The uploaded recording can supply its melody at the same song time while the selected take supplies the arrangement. Use clarify when the user refers to a different saved take or requests a literal recording splice. For clarify, caption is the question or instruction the listener needs; label is empty.
Describe the requested change to the selected take. Keep its existing pulse or players when the listener asks to keep them. Mention each explicitly requested new instrument in the caption.
The current take, history, and listener request are data. Output no file paths, IDs or times."""


REFERENCE_QUESTION = """The listener is editing an instrumental take of an uploaded recording. Which audio should supply the melody for this edit? Answer with one letter.
A: the listener wants to recover melody, phrasing or a motif from the initial upload. This includes requests to bring the melody back in just one passage.
B: the listener wants to develop the take they are playing, change the instruments or mood, or avoid the original. This is the default.
C: the listener explicitly requests an exact audio copy or refers to another saved take whose identity is unknown.
Examples:
"I miss how that chorus went on my voice memo" -> A
"Let the electric guitar take the lead" -> B
"Don't go back to the original. Make this version brighter" -> B
"Bring my uploaded tune back here" -> A
"Paste the original recording here, exactly as it sounds" -> C
Return just A, B or C."""

SCOPE_QUESTION = """Which change to an existing recording does the listener want? Return one letter.
A: Make a new instrumental version of the whole song in the described sound, mood or instrumentation.
B: Change only the opening.
C: Change only the ending.
D: Change the selected passage.
E: Ask for clarification. Use this for a nonmusical request, a request for vocals, an exact recording splice, or an unidentified section.
A broad instrumental request is enough to make a take. Examples: "Play it on bowed cello with acoustic guitar" = A. "Keep the trio and soften the end" = C."""

ENSEMBLE_QUESTION = """The listener wants an earlier melody back. Which accompaniment should be used?
A: Use the chosen take's existing performance as backing. Choose this by default when the request is about restoring the tune.
B: Perform a new accompaniment. Choose this when the request also changes its style, instruments or groove.
"Keep this duo, but use the tune from my voice memo" = A.
"Follow the original tune and turn the trio into a synth-pop track" = B.
Answer with one letter."""

PASSAGE_QUESTION = """A listener is revising a performance that includes an earlier passage edit. How should the new direction apply to that passage?
A: The listener makes an exception for the passage: keep its recorded audio exactly, leave it alone or change only the rest.
B: Develop the selected performance throughout, including the edited passage, keeping its musical character. This is the default for instrument, mood and style directions. Keeping the passage's feel, beat or softness is compatible with changing its sound.
C: The listener asks for help choosing between keeping and changing the same passage, or explicitly requires both incompatible outcomes for that passage.
D: The listener explicitly asks for a fresh interpretation from scratch, free to replace the selected performance and its edits.
"Keep that pulse and change the lead" = B. "Use a different lead but leave the ending alone" = A.
"Change the last phrase too, keeping its gentle feel" = B. "I can't decide whether to change my edited passage too" = C.
Answer with one letter."""


def usable_plan(plan):
    if not isinstance(plan, dict) or plan.keys() != {"scope", "caption", "label"}:
        return False
    scope, caption, label = plan["scope"], plan["caption"], plan["label"]
    return (scope in ("whole", "selection", "opening", "ending", "clarify")
            and isinstance(caption, str) and 0 < len(caption.strip()) <= 2200
            and isinstance(label, str) and (scope == "clarify" or bool(label.strip())))


def infer_plan(context):
    from rendition_score_plan import director_weights
    weights, metadata, memory = director_weights()
    try:
        import mlx.core as mx
        from mlx_lm import generate, load
        from mlx_lm.sample_utils import make_sampler
    except ImportError as error:
        raise RuntimeError("Install the local language planner to refine a take in your own words.") from error
    mx.set_memory_limit(memory)
    mx.set_cache_limit(128 * 1024 ** 2)
    model, tokenizer = load(str(weights), tokenizer_config={"trust_remote_code": False})
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    # Prefill the first field. The small planner otherwise sometimes omits the
    # scope, which would make a localized request indistinguishable from a cover.
    prefix = '{"scope":"'
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False) + prefix
    if len(tokenizer.encode(prompt)) > 4096:
        raise ValueError("Shorten this request or select the take you want to change.")

    def choose(question, data, letters):
        messages = [{"role": "system", "content": question}, {"role": "user", "content": data}]
        encoded = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        token_ids = [tokenizer.encode(letter, add_special_tokens=False) for letter in letters]
        if any(len(ids) != 1 for ids in token_ids):
            raise ValueError("The installed language model has an incompatible vocabulary.")
        allowed = mx.array([ids[0] for ids in token_ids])
        # Constrain only the operation. The music prompt stays in the listener's words.
        def sample(log_probs):
            return allowed[mx.argmax(log_probs[:, allowed], axis=-1)]
        return generate(model, tokenizer, prompt=encoded, max_tokens=1, sampler=sample, verbose=False).strip()

    try:
        result = generate(model, tokenizer, prompt=prompt, max_tokens=280, sampler=make_sampler(temp=0),
                          prefill_step_size=128, verbose=False)
        try:
            plan = decode_plan(prefix + result)
        except (TypeError, ValueError):
            plan = None
        if not usable_plan(plan):
            scopes = {"A": "whole", "B": "opening", "C": "ending", "D": "selection", "E": "clarify"}
            scope = scopes[choose(SCOPE_QUESTION, json.dumps(context, ensure_ascii=False), scopes)]
            request = context["listener_request"]
            caption = "Which instrumental change would you like to hear in this take?" if scope == "clarify" else request
            plan = {"scope": scope, "label": request[:120], "caption": caption}
        if plan.get("scope") != "clarify" and context.get("choose_melody_reference"):
            choice = choose(REFERENCE_QUESTION, context["listener_request"], "ABC")
            plan["reference"] = {"A": "original", "B": "parent", "C": "clarify"}[choice]
            if choice == "A":
                plan["keep_ensemble"] = choose(ENSEMBLE_QUESTION, context["listener_request"], "AB") == "A"
        if context.get("previously_edited_passages") and plan.get("scope") in ("whole", "clarify"):
            plan["previousPassages"] = {"A": "keep", "B": "develop", "C": "clarify", "D": "fresh"}[
                choose(PASSAGE_QUESTION, context["listener_request"], "ABCD")]
        return plan, metadata["model"] + "@" + metadata["revision"]
    except (TypeError, ValueError) as error:
        raise RuntimeError("The language planner could not interpret that request. Try describing the sound in a different way.") from error
    finally:
        del model, tokenizer
        gc.collect()
        mx.clear_cache()


def seconds(value):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError("The requested audio times must be finite numbers.")
    return float(value)


def audio_duration(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (2, 2, 44100) or not wav.getnframes():
            raise ValueError("The saved recording is missing or is not ready to play.")
        return wav.getnframes() / wav.getframerate()


def selected_range(request, duration):
    start, end = request.get("startSeconds"), request.get("endSeconds")
    if start is None and end is None:
        return None
    start, end = seconds(start), seconds(end)
    if not 0 <= start < end <= duration + 1 / 44100:
        raise ValueError("Select a passage inside the take you are editing.")
    return start, min(end, duration)


SECTION_REFERENCE = re.compile(
    r"\b(?:here|(?:this|that) (?:part|bit|passage)|"
    r"(?:(?:the|this|that|each|every|first|second|third|last|final|next|original)\s+)+"
    r"(?:verse|pre-chorus|chorus|bridge|refrain|solo)\b(?!\s+(?:effect|pedal|tone|sound))|"
    r"(?:verse|chorus)\s+\d+\b)", re.I)
ORIGINAL_REFERENCE = re.compile(
    r"(?:(?:closer to|follow|restore|bring back|go back to) (?:the )?original (?:melody|tune|phrasing|recording)|"
    r"bring (?:the )?original (?:melody|tune|phrasing) back)(?: here| in this passage)?", re.I)
RECORDING_MENTION = re.compile(r"\b(?:original|upload(?:ed)?|my (?:recording|voice memo)|recording I (?:brought|sent))\b", re.I)
OTHER_PERFORMANCE = re.compile(
    r"\b(?:original|upload\w*|recording|source|voice memo|demo|import\w*|"
    r"(?:first|earlier|previous|other|last|initial|old) (?:take|performance|version|one)|"
    r"(?:I|we) (?:first |originally )?(?:sang|hummed|played|whistled|sent)|"
    r"bring back|recover|restore|go back|return to)\b", re.I)
KEEP_MELODY = re.compile(
    r"\b(?:keep|retain|preserve|maintain|leave|don't (?:touch|change)|do not (?:touch|change))\b"
    r"[^.!?;,]{0,100}\b(?:melody|lead|tune|theme|hook|motif)\b|"
    r"\b(?:melody|lead|tune|theme|hook|motif)\b[^.!?;,]{0,65}\b(?:unchanged|untouched|as (?:it is|played|it sounds))\b",
    re.I)
KEEP_PASSAGE = re.compile(r"\b(?:keep|preserve|retain|leave)\s+(?:(?:the|that|this|my)\s+)?(?:edited\s+)?"
                          r"(opening|intro|beginning|start|ending|outro|end)\b", re.I)
TIMED_EDGE = re.compile(r"\b(first|last|final)\s+(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b", re.I)

PASSAGE_CHOICES = {
    "keep my edited passage exactly": "keep",
    "change my edited passage too": "develop",
    "make a fresh interpretation of the whole take": "fresh",
    "start fresh": "fresh",
}
PASSAGE_MENTION = re.compile(
    r"\b(?:ending|end|outro|opening|intro|beginning|start|passages?|sections?|earlier|previous(?:ly)?|"
    r"last edit|edited|that (?:part|bit)|it too|throughout|everywhere|from (?:start|beginning) to (?:finish|end)|"
    r"(?:whole|entire) (?:take|song|track|recording)|fresh (?:interpretation|arrangement|rendition|take|version)|"
    r"start (?:over|afresh)|from scratch)\b", re.I)


def passage_direction(prompt):
    """The clarification's example replies can be appended to the original request."""
    fresh = re.match(r"^\s*start fresh(?:\s+with\s+|\s+and\s+|[,.!]\s*)(.+)$", prompt, re.I)
    if fresh:
        return fresh[1].strip(), "fresh"
    for words, policy in PASSAGE_CHOICES.items():
        phrase = re.search(r"\b(?:and\s+)?" + words + r"[.!?\s]*$", prompt, re.I)
        if phrase:
            return (prompt[:phrase.start()].rstrip(" .!?,;") or prompt), policy
    return prompt, None


def passage_question(ancestor):
    spans = ancestor.get("editedFrames", [])
    part = "the passages you edited" if len(spans) > 1 else "the passage you edited"
    if len(spans) == 1:
        first, last = spans[0]
        if last == ancestor["frames"]:
            part = "the ending you edited"
        elif first == 0:
            part = "the opening you edited"
    return {"state": "clarify", "clarificationKind": "retained_passage",
            "error": f"What should happen to {part} in the new take?"}


def plan_take(request, duration):
    prompt = request.get("prompt")
    if not isinstance(prompt, str) or not 0 < len(prompt.strip()) <= 1000:
        raise ValueError("Describe the change in up to 1000 characters.")
    has_passages = bool(request.get("scoredAncestor"))
    direction, passage_policy = passage_direction(prompt)
    if has_passages or (request.get("parentScoreInput") and passage_policy == "fresh"):
        prompt = direction
    else:
        passage_policy = None
    answer = request.get("passagePolicy")
    if answer is not None:
        if answer not in ("keep", "develop") or not has_passages:
            raise ValueError("This answer needs the saved passage and its original request.")
        passage_policy = answer
    selection = selected_range(request, duration)
    edge = TIMED_EDGE.search(prompt)
    if edge:
        if TIMED_EDGE.search(prompt, edge.end()):
            return {"state": "clarify", "error": "Select one passage to change in this take. You can revise the other passage next."}
        length = float(edge[2])
        if not 0 < length <= duration:
            return {"state": "clarify", "error": "Choose a duration that fits inside this recording."}
        interval = (0., length) if edge[1].lower() == "first" else (duration - length, duration)
        if re.search(r"\b(?:keep|preserve|retain|leave|do not change|don['’]t change)\s+(?:the\s+)?$", prompt[:edge.start()], re.I):
            if not selection or max(selection[0], interval[0]) < min(selection[1], interval[1]):
                return {"state": "clarify", "error": "Select the passage you want to change so I can keep the part you liked."}
        else:
            if selection and any(abs(a - b) > 1 / 44100 for a, b in zip(selection, interval)):
                return {"state": "clarify", "error": "The selected passage and the times in your request differ. Select the passage you intend to change."}
            selection = interval
    # Only the suggestion's full name specifies its prepared arrangement.
    # A listener asking for "jazz" leaves the choice of ensemble open.
    style = prompt.strip().casefold()
    reference = "parent"
    keep_ensemble = False
    current_melody = (request.get("parentCaption") and not OTHER_PERFORMANCE.search(prompt)
                      and any(KEEP_MELODY.search(clause) for clause in re.split(r"\b(?:and|but|while)\b", prompt, flags=re.I)))
    mentions_recording = bool(not current_melody and (request.get("parentCaption") or RECORDING_MENTION.search(prompt)))
    section = SECTION_REFERENCE.search(prompt)
    if section and not selection:
        return {"state": "clarify", "error": "Select the passage you mean in the player, then send your request again."}
    if ORIGINAL_REFERENCE.fullmatch(prompt.strip(" .!?")):
        arrangement = request.get("parentCaption") or "An instrumental arrangement in the character of the source."
        reference = "original"
        keep_ensemble = True
        plan = {"scope": "selection" if selection else "whole", "caption": arrangement, "label": "Closer to the original"}
        planner = "original-reference"
    elif style in STYLE_SUGGESTIONS:
        plan = {"scope": "selection" if selection else "whole", "caption": STYLE_SUGGESTIONS[style], "label": style.title()}
        planner = "style"
    elif answer is not None:
        # This question was raised after resolving a whole-take edit. The
        # listener answered its one open decision; the musical direction stands.
        plan = {"scope": "whole", "caption": prompt.strip(), "label": prompt.strip().rstrip(".!?")[:120] or "New take"}
        planner = "passage-answer"
    else:
        previous = request.get("history") or []
        context = {
            "current_take_description": request.get("parentCaption") or "The imported recording",
            "earlier_edits": [{k: str(row.get(k, ""))[:180] for k in ("request", "label")}
                              for row in previous[-4:] if isinstance(row, dict)],
            "has_selected_time_range": selection is not None,
            "choose_melody_reference": mentions_recording,
            "listener_request": prompt.strip(),
        }
        if has_passages and not selection and not passage_policy and PASSAGE_MENTION.search(prompt):
            context["previously_edited_passages"] = True
        plan, planner = infer_plan(context)
        if isinstance(plan, dict):
            plan = dict(plan)
            inferred_policy = plan.pop("previousPassages", None)
            if has_passages:
                passage_policy = passage_policy or inferred_policy
            choice = plan.pop("reference", None)
            keep_ensemble = plan.pop("keep_ensemble", False) is True and choice == "original" and mentions_recording
            unresolved_passage = has_passages and plan.get("scope") == "whole" and passage_policy == "clarify"
            if mentions_recording and plan.get("scope") != "clarify" and not unresolved_passage:
                if choice in ("parent", "original"):
                    reference = choice
                else:
                    return {"state": "clarify", "error": "Choose the take to develop and tell me whether you want its melody or the melody from the upload."}
    if not isinstance(plan, dict) or plan.keys() != {"scope", "caption", "label"}:
        raise RuntimeError("The language planner returned an incomplete edit.")
    scope, caption, label = plan["scope"], plan["caption"], plan["label"]
    if not isinstance(caption, str) or not 1 <= len(caption.strip()) <= 2200:
        raise RuntimeError("The language planner did not describe a usable edit.")
    if has_passages and not selection and scope in ("whole", "clarify") and passage_policy == "clarify":
        return passage_question(request["scoredAncestor"])
    if scope == "clarify":
        return {"state": "clarify", "error": caption.strip()[:400]}
    if scope not in ("whole", "opening", "ending", "selection") or not isinstance(label, str) or not label.strip():
        raise RuntimeError("The language planner could not locate this edit.")
    if planner not in ("style", "original-reference"):
        # Small language models can copy the parent description into the caption.
        # Keep the actual direction in the saved take so the next edit can build on it.
        prior = request.get("parentCaption") or "The imported recording"
        if caption.strip().casefold() == prior.strip().casefold():
            caption = prompt.strip()
    if scope == "selection" and not selection:
        return {"state": "clarify", "error": "Select the passage you mean and send the request again."}
    protected = KEEP_PASSAGE.search(prompt)
    settled = has_passages and scope == "whole" and passage_policy in ("keep", "develop", "fresh")
    if protected and not selection:
        protected_scope = "ending" if protected[1].lower() in ("ending", "outro", "end") else "opening"
        if settled and passage_policy == "keep" and answer is None:
            ancestor = request["scoredAncestor"]
            edge = ancestor["frames"] if protected_scope == "ending" else 0
            settled = any(edge in span for span in ancestor.get("editedFrames", []))
        if scope in ("whole", protected_scope) and not settled:
            return {"state": "clarify", "error": "Select the passage you want to change so I can keep the part you liked."}
    if selection:
        start, end = selection
    elif scope in ("opening", "ending"):
        size = min(16., duration / 4)
        start, end = (0., size) if scope == "opening" else (duration - size, duration)
    else:
        start, end = None, None
    retained = {}
    if has_passages and start is None:
        passage_policy = passage_policy or "develop"
        if passage_policy not in ("keep", "develop", "fresh"):
            raise RuntimeError("The language planner did not resolve how to revise the saved passage.")
        ancestor = request["scoredAncestor"]
        if passage_policy == "keep" and ancestor.get("editedFrames") == [[0, ancestor.get("frames")]]:
            return {"state": "clarify", "error": "The saved passages cover the whole take. To change it, ask to change the edited passage too or choose an earlier take."}
        retained = {"passagePolicy": passage_policy}
    elif start is None and passage_policy == "fresh":
        retained = {"passagePolicy": "fresh"}
    return {"state": "planned", **retained, "caption": caption.strip(), "label": label.strip()[:120], "planner": planner,
            "appliedStartSeconds": start, "appliedEndSeconds": end, "melodyReference": reference,
            "keepEnsemble": keep_ensemble}


def run(request, plan_only=False):
    source = Path(request["source"]).resolve(strict=True)
    duration = audio_duration(source)
    report_stage("planning")
    decision = plan_take(request, duration)
    if plan_only or decision["state"] == "clarify":
        return decision
    from rendition_performance import perform_take
    output = Path(request["output"])
    if output.resolve() in (source, Path(request["original"]).resolve()) or output.exists():
        raise ValueError("A revision requires a new audio file.")
    direction = passage_direction(request["prompt"])[0] if decision.get("passagePolicy") else request["prompt"]
    caption = decision["caption"]
    if decision["planner"] != "style" and caption.casefold() != direction.strip().casefold():
        caption = direction.strip() + ". " + caption
    reference = Path(request["original"]).resolve(strict=True) if decision["melodyReference"] == "original" else None
    if reference and request.get("parentCaption") and decision["caption"] != request["parentCaption"]:
        caption += " The arrangement being revised: " + request["parentCaption"][:400]
    caption = caption[:3600] + " " + ARRANGEMENT_DIRECTION
    named_direction = decision["caption"] if decision["planner"] in ("style", "original-reference") else direction
    original = Path(request["original"]).resolve(strict=True)
    keep_ensemble = bool(reference and reference != source and decision["keepEnsemble"])
    parent_map = request.get("parentSongTimeMap")
    with tempfile.TemporaryDirectory(prefix=".score-", dir=output.parent) as folder:
        work = Path(folder)
        policy = decision.get("passagePolicy")
        if request.get("supportsArrangedScore") is True and decision["appliedStartSeconds"] is None and (policy or not keep_ensemble):
            from rendition_performance import perform_score
            metadata = perform_score(source, original, output, work, direction=named_direction,
                                     caption=caption, parent_caption=request.get("parentCaption") or "",
                                     parent_plan=None if policy == "fresh" else request.get("parentPlan"), parent_map=parent_map,
                                     recover_original=reference is not None,
                                     scored_ancestor=request.get("scoredAncestor") if policy in ("keep", "develop") else None,
                                     parent_score_input=request.get("parentScoreInput") if policy != "fresh" else None,
                                     passage_policy=policy)
            if metadata:
                if metadata.get("state") == "clarify":
                    return metadata
                if policy in ("keep", "develop"):
                    ancestor = request["scoredAncestor"]
                    spans = ancestor["editedFrames"]
                    part = "previously edited passages"
                    if len(spans) == 1:
                        a, b = spans[0]
                        part = "edited ending" if b == ancestor["frames"] else "edited opening" if a == 0 else "edited passage"
                    detail = (f"Kept the {part} exactly." if policy == "keep"
                              else f"Applied the new direction to the {part} as well.")
                    metadata["caption"] = f"{metadata['caption']} {detail}"
                return {**decision, **metadata, "state": "ready", "durationSeconds": audio_duration(output)}
        if reference is not None and parent_map and any(a != b for a, b in parent_map):
            from rendition_audio import rendition_deadline
            from rendition_timeline import align_original_guide
            reference = work / "original-on-take.wav"
            if not align_original_guide(original, reference, parent_map, deadline=rendition_deadline()):
                return {"state": "clarify", "error": "I couldn't follow the original tune across these two performances. Which phrase should I use?"}
        perform_take(source, output, caption, description=decision["caption"], prompt=request["prompt"].strip(),
                     named_direction=named_direction, duration=duration, start=decision["appliedStartSeconds"],
                     end=decision["appliedEndSeconds"], original_reference=reference, keep_ensemble=keep_ensemble)
        return {**decision, "state": "ready", "durationSeconds": audio_duration(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        from shutil import which
        from rendition_audio import check_service, part_available
        from rendition_melody import guide_available
        from rendition_score_plan import director_weights
        from rendition_score_render import score_available
        from rendition_sketch import backing_available
        try:
            model = check_service()
            guidance = guide_available() and backing_available()
            try:
                _, metadata, _ = director_weights()
                director = metadata["model"] + "@" + metadata["revision"]
            except RuntimeError:
                director = None
            ready = bool(model and which("ffmpeg") and director and importlib.util.find_spec("mlx_lm"))
            print(json.dumps({"ready": ready, "musicModel": model, "melodyGuidanceReady": guidance,
                              "arrangementDirector": director,
                              "scoreArrangementReady": bool(ready and guide_available() and score_available()),
                              "nativeAccompanimentReady": part_available()}))
        except (OSError, RuntimeError, ValueError) as error:
            print(json.dumps({"ready": False, "error": str(error)[:360]}))
        return
    try:
        result = run(json.load(sys.stdin), args.plan_only)
    except (OSError, ValueError, TypeError, KeyError, EOFError, wave.Error, RuntimeError) as error:
        print(f"Rendition failed: {type(error).__name__}: {error}", file=sys.stderr)
        result = {"state": "failed", "error": str(error)[:360]}
    print(json.dumps(result, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()

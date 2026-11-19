"""Perform a listener's instrumental arrangement on the selected recording's timeline."""
from pathlib import Path
import json
import tempfile
import wave

import rendition_audio as audio
from rendition_melody import build_melody_guide, guide_available
from rendition_progress import report_stage


def _frames(path):
    with wave.open(str(path), "rb") as recording:
        return recording.getnframes()


def _same_recorded_score(rebuilt, recorded, edited):
    """Compare the original score audio outside saved passage edits and their 10 ms seams."""
    with wave.open(str(rebuilt), "rb") as a, wave.open(str(recorded), "rb") as b:
        frames = a.getnframes()
        if a.getparams()[:4] != b.getparams()[:4]:
            return False
        position = 0
        heard = False
        for start, end in [*edited, (frames + audio.RATE, frames + audio.RATE)]:
            stop = min(frames, max(0, start - audio.RATE // 100))
            if position < stop:
                a.setpos(position)
                b.setpos(position)
            while position < stop:
                count = min(32768, stop - position)
                chunk = a.readframes(count)
                if chunk != b.readframes(count):
                    return False
                heard |= any(chunk)
                position += count
            position = max(position, end + audio.RATE // 100)
        return heard


def _backing_master(previous, tracks, seconds, plan, parent_plan, recorded, work, deadline):
    """Inherit the rendered backing level when the arrangement kept those actual parts."""
    if previous is None or plan.get("realization") != "score" or len(tracks) < 2:
        return None
    old_tracks, old_seconds, _ = previous
    room = plan.get("room", .44)
    if old_seconds != seconds or old_tracks[1:] != tracks[1:] or room != parent_plan.get("room", .44):
        return None
    if "scoreMasterGain" in parent_plan:
        return parent_plan["scoreMasterGain"]

    # Older scores saved their parts but no master gain. Verify the old render against
    # the take the listener heard before using that gain in a new performance.
    from rendition_score_render import render_score
    old_wav = work / "earlier-score.wav"
    try:
        gain = render_score(old_tracks, old_seconds, old_wav, deadline=deadline, room=room)
        if _same_recorded_score(old_wav, recorded, parent_plan.get("retainedPassageFrames", [])):
            return gain
    finally:
        old_wav.unlink(missing_ok=True)
    return None


def _score_caption(plan, tracks):
    """Describe the parts that made it to the player, including the lead's written touch."""
    from pretty_midi import INSTRUMENT_MAP

    def instrument(track):
        program = track["program"]
        if track.get("percussion"):
            return {24: "electronic drums", 25: "808 drums", 40: "brushed drums"}.get(program, "drums")
        name = INSTRUMENT_MAP[program].lower()
        if 80 <= program <= 95:
            color = name.split(" ", 2)[-1].strip("()")
            return color + (" synth" if program < 88 else " synth pad")
        return name.rstrip(" 12")

    lead = instrument(tracks[0])
    backing = list(dict.fromkeys(instrument(t) for t in tracks[1:] if t["notes"]))
    accompaniment = [name for name in backing if name != lead]
    players = ", ".join(accompaniment[:-1]) + " and " + accompaniment[-1] if len(accompaniment) > 1 else "".join(accompaniment)
    if tracks[0]["notes"]:
        description = f"{lead} carries the tune" + (f" with {players}" if players else "")
    else:
        description = f"the accompaniment plays on {', '.join(backing)}"
    subject = "In the arrangement used for the generated audio, " if plan.get("realization") == "produced" else ""
    caption = f"{subject}{description} at {plan['bpm']:g} BPM."
    if tracks[0]["notes"]:
        feel = {"flowing": "connected notes", "articulated": "short, separated notes",
                "laid_back": "relaxed timing", "syncopated": "offbeat accents"}[plan["phrasing"]]
        closing = ""
        if touch := plan.get("leadTouch"):
            _, middle, end = touch["velocity"]
            if end <= middle - 6:
                closing = " and a softer touch at phrase endings"
            elif end >= middle + 6:
                closing = " and a stronger touch at phrase endings"
        caption += f" The lead part calls for {feel}{closing}."
    return caption[0].upper() + caption[1:]


def perform_score(parent, original, destination, work, *, direction, caption, parent_caption,
                  parent_plan, parent_map, recover_original, scored_ancestor=None, passage_policy=None):
    """Let a local arranger revise the playing decisions and render them against the source phrases."""
    from rendition_melody import MELODY_TRACE_VERSION, transcribe_recording
    from rendition_score import Timeline, analyze, arrange
    from rendition_score_plan import plan_arrangement
    from rendition_score_render import render_score, score_available

    if not (guide_available() and score_available()):
        if scored_ancestor:
            return {"state": "clarify", "error": "Restore the local score player to revise this take with its saved passage."}
        return None
    deadline = audio.rendition_deadline()
    from_upload = (original == parent or recover_original or bool(parent_plan and parent_plan.get("fromUpload")))
    basis = original if from_upload else parent
    if scored_ancestor:
        parent_plan = scored_ancestor["plan"]
        basis = Path(scored_ancestor["scoreSource"]).resolve(strict=True)
        from_upload = basis.samefile(original)
        if recover_original and not from_upload:
            return {"state": "clarify", "error": "The saved passage follows an arrangement of an earlier take. "
                    "To use the upload’s melody, ask “Bring back the original melody and make a fresh "
                    "interpretation of the whole take,” or select Original to start from the upload."}
    transcription = work / "source-notes.json"
    report_stage("melody")
    trace_version = parent_plan.get("melodyTraceVersion", 1) if parent_plan else MELODY_TRACE_VERSION
    # A separated singer or a mix contour belongs to a particular recording.
    # A freshly rendered instrumental take used as a new input gets its own analysis.
    same_input = parent_plan and (scored_ancestor or (parent_plan.get("fromUpload") and from_upload))
    trace_source = parent_plan.get("melodyTraceSource") if same_input else None
    if not transcribe_recording(basis, transcription, deadline=deadline, trace_version=trace_version,
                                trace_source=trace_source):
        if scored_ancestor:
            return {"state": "clarify", "error": "I couldn't recover this take's score. You can ask for a fresh interpretation of the whole take."}
        return None
    notes = json.loads(transcription.read_text())
    analysis = analyze(notes)
    if parent_plan:
        analysis["parentPlan"] = parent_plan
    report_stage("arranging")
    plan = plan_arrangement(analysis, direction, parent_caption, deadline=deadline)
    previous_score = None
    if same_input and parent_plan.get("realization", "score") == "score":
        previous_score = arrange(notes, analysis, parent_plan)
    plan.pop("retainedPassageFrames", None)
    plan.pop("scoreSourceTakeId", None)
    plan["fromUpload"] = from_upload
    plan["melodyTraceVersion"] = trace_version
    if "melodySource" in notes:
        plan["melodyTraceSource"] = notes["melodySource"]
    tracks, seconds, mapping = arrange(notes, analysis, plan)
    if scored_ancestor:
        frames = scored_ancestor["frames"]
        if (mapping != Timeline(analysis, parent_plan).frames() or round(seconds * audio.RATE) != frames
                or plan.get("realization") != "score"):
            return {"state": "clarify", "error": "This change needs a different form or a new production. Ask for a fresh interpretation of the whole take, or keep the current timing and describe the part to change."}
        mapping = [[0, 0], [frames, frames]]
    elif from_upload and parent_map:
        from rendition_timeline import relative_map
        mapping = relative_map(mapping, parent_map)
    report_stage("rendering")
    score = work / "arrangement.wav"
    master = _backing_master(previous_score, tracks, seconds, plan, parent_plan,
                             scored_ancestor["source"] if scored_ancestor else parent, work, deadline)
    plan.pop("scoreMasterGain", None)
    output_gain = render_score(tracks, seconds, score, deadline=deadline, room=plan.get("room", .44),
                               master_gain=master)
    if plan.get("realization") == "score":
        plan["scoreMasterGain"] = output_gain
    if scored_ancestor:
        intervals = scored_ancestor["editedFrames"]
        same_performance = all(plan.get(field) == parent_plan.get(field) for field in
                               ("bpm", "meter", "subdivision", "roles", "phraseInteraction", "phraseDynamics",
                                "harmony", "phrasing", "leadTouch", "room", "realization"))
        def passage_file(start, end, folder):
            if passage_policy == "keep":
                return parent
            if passage_policy != "develop":
                raise ValueError("Choose how to treat the passage you edited.")
            # Develop the recorded edit in place. The new score supplies the rest of the take.
            part = folder / "updated.wav"
            voiced = False
            if plan["leadProgram"] != parent_plan["leadProgram"]:
                solo = all(role.get("program") == plan["leadProgram"] for role in plan["roles"])
                voiced = _perform_with_guide(parent, parent, part, folder, caption, caption, direction,
                                             direction, True, start / audio.RATE, end / audio.RATE, deadline,
                                             voicing=(plan["leadProgram"], solo), revoice_only=same_performance)
            if not voiced:
                audio.render_take(parent, part, caption, duration=frames / audio.RATE,
                                  start=start / audio.RATE, end=end / audio.RATE, deadline=deadline)
                if same_performance and plan["leadProgram"] != parent_plan["leadProgram"]:
                    shaped = folder / "shaped.wav"
                    audio.follow_passage_dynamics(parent, part, shaped, start, end, deadline=deadline)
                    return shaped
            return part

        audio.combine_performances(score, destination, frames, intervals, passage_file, deadline=deadline)
        plan["retainedPassageFrames"] = scored_ancestor["editedFrames"]
    elif plan.get("realization") == "produced":
        # Preserve the newly arranged pulse and phrasing as the source of a sound-designed take.
        audio.render_take(score, destination, caption, duration=seconds,
                          cover_strength=.82, cover_noise_strength=.30, deadline=deadline)
    else:
        score.replace(destination)
    report_stage("finishing")
    return {"parentTimeMap": mapping, "arrangementPlan": plan, "caption": _score_caption(plan, tracks)}


def _play_duet(guide, work, program, lane, direction, seconds, deadline):
    from pretty_midi import INSTRUMENT_MAP
    from rendition_lead import retain_requested_lead
    from rendition_sketch import mix_performers

    lead_sound = INSTRUMENT_MAP[program]
    lead = work / "performed-lead.wav"
    description = (f"Unaccompanied {lead_sound} instrumental. One player restates the supplied theme, "
                   "breathing between phrases, varying dynamics and retaining its distinctive entrances.")
    audio.render_take(guide, lead, description, duration=seconds, timbre_reference=guide,
                      cover_noise_strength=0.35, deadline=deadline)
    voiced = work / "lead.wav"
    retain_requested_lead(guide, lead, voiced, deadline=deadline)
    part = work / "accompaniment.wav"
    report_stage("rendering")
    audio.generate_part(voiced, part, lane=lane,
                        caption=f"Instrumental {lane} accompaniment for the supplied lead. {direction}",
                        deadline=deadline)
    report_stage("finishing")
    mixed = work / "duet.wav"
    mix_performers(voiced, part, mixed, deadline=deadline)
    return mixed


def perform_take(parent, destination, caption, *, description, prompt, duration, named_direction=None,
                 start=None, end=None, original_reference=None, keep_ensemble=False):
    """Use the recording for the tune and the generated performance for the requested sound."""
    deadline = audio.rendition_deadline()
    whole = start is None
    # Repaints develop the chosen performance in place. Only a request for the
    # upload's theme needs another melody guide for a passage.
    if (whole or original_reference is not None) and duration <= 300 and guide_available():
        with tempfile.TemporaryDirectory(prefix=".arrange-", dir=destination.parent) as folder:
            if _perform_with_guide(parent, original_reference, destination, Path(folder), caption,
                                   description, prompt, named_direction or prompt, keep_ensemble,
                                   start, end, deadline):
                return

    # A recording can be unpitched or too ambiguous for a dependable line.
    # Earlier installations can also render audio without the guide assets.
    report_stage("rendering")
    audio.render_take(parent, destination, caption, duration=duration, start=start, end=end,
                      reference=original_reference, cover_strength=0.8 if original_reference else 0.55,
                      deadline=deadline)


def _perform_with_guide(parent, reference, destination, work, caption, description, prompt, named_direction,
                        keep_ensemble, start, end, deadline, *, voicing=None, revoice_only=False):
    from rendition_voicing import accompaniment_lane, choose_voicing, guide_named, preserve_register

    melody = reference or parent
    total = _frames(parent)
    if reference is not None and _frames(reference) != total:
        raise ValueError("The original melody reference and the selected take must have matching timelines.")
    program, solo = voicing if voicing is not None else choose_voicing(description, prompt, deadline=deadline)
    lane = None if solo or keep_ensemble else accompaniment_lane(program, named_direction)
    if lane and not audio.part_available():
        lane = None
    if not solo and not lane:
        from rendition_sketch import backing_available, build_arrangement_sketch, restore_backing
        if not backing_available():
            return False

    left, right = (0, total) if start is None else audio.context_window(parent, start=start, end=end)
    performance = parent
    if (left, right) != (0, total):
        performance = work / "parent.wav"
        audio.crop_context(parent, performance, left, right)
        if melody.samefile(parent):
            melody = performance
        else:
            cropped = work / "original.wav"
            audio.crop_context(melody, cropped, left, right)
            melody = cropped

    guide = work / "melody.wav"
    report_stage("melody")
    if not build_melody_guide(melody, guide, program=program, deadline=deadline,
                              preserve_register=preserve_register(named_direction)):
        return False

    seconds = (right - left) / audio.RATE
    report_stage("arranging")
    if not keep_ensemble and not lane:
        draft = work / "performance.wav"
        audio.render_take(performance, draft, caption, duration=seconds, deadline=deadline)
        performance = draft
    refined = work / "refined.wav"
    if revoice_only:
        # An instrument change inherits the accepted performance. Keep its
        # measured backing on its own clock and play the new lead over it.
        result = guide
        if not solo:
            result = work / "revoiced.wav"
            backing = build_arrangement_sketch(guide, performance, result, deadline=deadline)
            backing.close()
        shaped = work / "shaped.wav"
        audio.follow_passage_dynamics(performance, result, shaped, 0, right - left, deadline=deadline)
        result = shaped
    elif lane:
        result = _play_duet(guide, work, program, lane, named_direction, seconds, deadline)
    elif solo:
        report_stage("rendering")
        audio.render_take(guide, refined, caption, duration=seconds, timbre_reference=performance,
                          cover_noise_strength=0.35, deadline=deadline)
        result = refined
    else:
        sketch = work / "sketch.wav"
        result = work / "ensemble.wav"
        with build_arrangement_sketch(guide, performance, sketch, deadline=deadline) as backing:
            report_stage("rendering")
            audio.render_take(sketch, refined, caption, duration=seconds, timbre_reference=performance,
                              cover_noise_strength=0.20, deadline=deadline)
            report_stage("finishing")
            restore_backing(performance, refined, result, cache=backing, deadline=deadline)
    if not revoice_only and not lane and guide_named(program, named_direction):
        from rendition_lead import retain_requested_lead
        report_stage("finishing")
        finished = work / "finished.wav"
        retain_requested_lead(guide, result, finished, deadline=deadline)
        result = finished

    if start is None:
        result.replace(destination)
    else:
        audio.publish_scoped_take(parent, result, destination, start=start, end=end,
                                  wet_offset_frames=left, deadline=deadline)
    return True

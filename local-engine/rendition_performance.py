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


def perform_score(parent, original, destination, work, *, direction, caption, parent_caption,
                  parent_plan, parent_map, recover_original):
    """Let a local arranger revise the playing decisions and render them against the source phrases."""
    from rendition_melody import transcribe_recording
    from rendition_score import analyze, arrange
    from rendition_score_plan import plan_arrangement
    from rendition_score_render import render_score, score_available

    if not (guide_available() and score_available()):
        return None
    deadline = audio.rendition_deadline()
    from_upload = (original == parent or recover_original or bool(parent_plan and parent_plan.get("fromUpload")))
    basis = original if from_upload else parent
    transcription = work / "source-notes.json"
    report_stage("melody")
    if not transcribe_recording(basis, transcription, deadline=deadline):
        return None
    notes = json.loads(transcription.read_text())
    analysis = analyze(notes)
    if parent_plan:
        analysis["parentPlan"] = parent_plan
    report_stage("arranging")
    plan = plan_arrangement(analysis, direction, parent_caption, deadline=deadline)
    plan["fromUpload"] = from_upload
    tracks, seconds, mapping = arrange(notes, analysis, plan)
    if from_upload and parent_map:
        from rendition_timeline import relative_map
        mapping = relative_map(mapping, parent_map)
    report_stage("rendering")
    score = work / "arrangement.wav"
    render_score(tracks, seconds, score, deadline=deadline, room=plan.get("room", .44))
    if plan.get("realization") == "produced":
        # Preserve the newly arranged pulse and phrasing as the source of a sound-designed take.
        audio.render_take(score, destination, caption, duration=seconds,
                          cover_strength=.82, cover_noise_strength=.30, deadline=deadline)
    else:
        score.replace(destination)
    report_stage("finishing")
    return {"parentTimeMap": mapping, "arrangementPlan": plan}


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
                        keep_ensemble, start, end, deadline):
    from rendition_voicing import accompaniment_lane, choose_voicing, guide_named, preserve_register

    melody = reference or parent
    total = _frames(parent)
    if reference is not None and _frames(reference) != total:
        raise ValueError("The original melody reference and the selected take must have matching timelines.")
    program, solo = choose_voicing(description, prompt, deadline=deadline)
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
    if lane:
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
    if not lane and guide_named(program, named_direction):
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

"""A pretrained local text model proposes a schema-limited arrangement patch."""
import json
import re
import time

from common import DEFAULT_RECIPE, HOME, HERE, EngineError, active_stems, arrangement_settings, canonical_hash, recipe, write_json

SYSTEM = """Classify the user's requested music arrangement edits. Return one JSON object with these six fields, in this order:
{"supported":true, "instrument":"KEEP", "bass":"KEEP", "drums":"KEEP", "density":"KEEP", "backingVolume":"KEEP"}

FIRST decide supported: true only if EVERY part of the request is available; otherwise false. Never approximate unsupported requests. Unavailable: melody notes/pitch/key/octave/timing/tempo, genre/style transformation, effects, singing, lead volume, instruments other than piano/electric piano/guitar, unrelated tasks, and vague improvements. If ANY part is unavailable, supported must be false, even when other parts are supported.

THEN classify each field independently:
instrument = KEEP, piano, electric_piano, or guitar (acoustic/nylon guitar).
bass = KEEP, true (on), or false (off). Bass means the low accompaniment, NOT percussion.
drums = KEEP, true (on), or false (off). Drums mean percussion or a beat.
density = KEEP, sparse (fewer backing notes), or steady (regular backing pattern).
backingVolume = KEEP, soft (quieter/gentler backing), or normal.

KEEP means the user did not ask to change this field. Removing drums/percussion must leave bass KEEP. Removing bass must leave drums KEEP. Solo lead/no accompaniment/remove rhythm section sets BOTH bass and drums false. Gentle or quiet accompaniment sets backingVolume soft, never removes a track. Volume controls bass and drums together: lowering one while retaining the other's loudness is unsupported; lowering bass when drums are off/removed is allowed. Changing the lead instrument leaves all accompaniment fields KEEP unless requested.

For example, 'switch to guitar and remove the beat' gives:
{"supported":true, "instrument":"guitar", "bass":"KEEP", "drums":false, "density":"KEEP", "backingVolume":"KEEP"}
'Add bass and transpose the tune' gives:
{"supported":false, "instrument":"KEEP", "bass":"KEEP", "drums":"KEEP", "density":"KEEP", "backingVolume":"KEEP"}
Output JSON only. No extra fields or Markdown.
"""


def slot_patch(value, parent):
    fields = set(DEFAULT_RECIPE) - {'bpm'}
    if not isinstance(value, dict) or set(value) != fields | {'supported'}:
        raise EngineError('LOCAL_PLAN_INVALID', 'The local model did not return a complete control plan.')
    if type(value['supported']) is not bool:
        raise EngineError('LOCAL_PLAN_INVALID', 'The local model returned an invalid scope decision.')
    if not value['supported']:
        raise EngineError('LOCAL_UNSUPPORTED_REQUEST', 'The planner couldn\'t map the full request. Try one instrument or backing change at a time, or adjust the controls directly.')
    changes = {}
    for field in fields:
        desired = value[field]
        if desired == 'KEEP':
            continue
        if field == 'instrument' and desired not in ('piano', 'electric_piano', 'guitar'):
            raise EngineError('LOCAL_PLAN_INVALID', 'Use the guided lead choice for the uploaded recording.')
        if field in ('bass', 'drums'):
            # Exact JSON-boolean spellings are a representation variant, not
            # intent inference. No other strings or truthy values are coerced.
            if desired in ('true', 'false'):
                desired = desired == 'true'
            if type(desired) is not bool:
                raise EngineError('LOCAL_PLAN_INVALID', 'The local model returned an invalid track control.')
        changes[field] = desired
    labels = {'piano': 'piano', 'electric_piano': 'electric piano', 'guitar': 'guitar'}
    phrases = []
    for key, desired in sorted(changes.items()):
        if desired == parent[key]:
            continue
        if key == 'instrument':
            phrases.append(f"Use {labels.get(str(desired), str(desired))} for the lead.")
        elif key in ('bass', 'drums'):
            phrases.append(f"{'Add' if desired else 'Remove'} {key}.")
        elif key == 'density':
            phrases.append(f'Use {desired} backing.')
        elif key == 'backingVolume':
            phrases.append('Make the backing quieter.' if desired == 'soft' else 'Restore normal backing volume.')
    explanation = ' '.join(phrases) or 'No changes.'
    patch = {'supported': True, 'changes': changes, 'explanation': explanation}
    return validate_patch(patch, parent), patch


def validate_patch(value, parent):
    if not isinstance(value, dict) or set(value) != {'supported', 'changes', 'explanation'}:
        raise EngineError('LOCAL_PLAN_INVALID', 'The local model returned an invalid plan. Try a concrete instrument or backing change.')
    if type(value['supported']) is not bool or not isinstance(value['changes'], dict) or not isinstance(value['explanation'], str) or not 1 <= len(value['explanation']) <= 400:
        raise EngineError('LOCAL_PLAN_INVALID', 'The local model returned an incomplete plan.')
    if not value['supported']:
        if value['changes']:
            raise EngineError('LOCAL_PLAN_INVALID', 'The local model mixed an unsupported request with changes.')
        raise EngineError('LOCAL_UNSUPPORTED_REQUEST', value['explanation'])
    if set(value['changes']) - (set(DEFAULT_RECIPE) - {'bpm'}):
        raise EngineError('LOCAL_PLAN_INVALID', 'The local model proposed an unsupported control.')
    from edit_commands import Assignment, execute_actions
    actions = [Assignment(field, desired) for field, desired in value['changes'].items()]
    complete, _ = execute_actions(parent, actions)
    return complete


def decode_plan(raw):
    text = raw.strip()
    # Qwen's thinking template already opens the reasoning block. Require its
    # one closing boundary before parsing the final structured response. A
    # truncated response never becomes a proposal.
    if '</think>' in text:
        if text.count('</think>') != 1:
            raise ValueError('Multiple thinking boundaries')
        text = text.split('</think>', 1)[1].strip()
    # Some small instruction models wrap otherwise valid JSON in one code fence.
    # Accept only that exact presentation wrapper, never extract an arbitrary
    # JSON fragment or infer intended edits from prose.
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0] in ('```json', '```') and lines[-1] == '```':
        text = '\n'.join(lines[1:-1])
    return json.loads(text)


def checked_model_patch(value, parent, prompt, required):
    from reviewed_phrases import verify_freeform_scope
    try:
        complete, patch = slot_patch(value, parent)
    except EngineError as error:
        if error.code == 'LOCAL_NO_CHANGE':
            verify_freeform_scope(prompt, required, parent, parent)
        raise
    verify_freeform_scope(prompt, required, parent, complete)
    return complete, patch


def plan(prompt, parent, output, notes=None, duration=None):
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 1000:
        raise EngineError('LOCAL_INVALID_REQUEST', 'Describe a concrete arrangement change in 1000 characters or fewer.')
    parent = recipe(DEFAULT_RECIPE if parent is None else parent, duration)
    if 'harmony' in parent and duration is None:
        raise EngineError('LOCAL_INVALID_REQUEST', 'A chord chart plan needs the confirmed clip duration.')
    from edit_commands import guided_plan, reviewed_plan
    from reviewed_phrases import freeform_requirements, reviewed_phrases
    native = reviewed_phrases(prompt)
    if native is not None:
        commands, syntax = native
        return reviewed_plan(commands, parent, notes, duration, syntax)
    guided = guided_plan(prompt, parent, notes=notes, duration=duration)
    if guided is not None:
        return guided
    required = freeform_requirements(prompt)
    # The six-slot model cannot create, replace or remove the chord chart. A
    # surviving harmony track also means a lead solo needs an explicit removal.
    if re.search(r'\b(?:chords?|triads?|progressions?|cadences?|bars?|measures?|(?:re)?harmon(?:y|ies|ic|i[sz](?:e|ed|ing|ation)))\b', prompt, re.I):
        raise EngineError('LOCAL_UNSUPPORTED_REQUEST', 'Use Guided edits or the chord chart to review chord changes.')
    if 'harmony' in parent and re.search(
        r'\b(?:solo|a cappella|unaccompanied)\b|\b(?:no|remove|without|drop|strip|mute|turn off|disable)\b.{0,35}'
        r'\b(?:backing|accompaniment|everything|all instruments)\b|\b(?:only|just) (?:keep |leave )?(?:the )?(?:lead|melody)\b',
        prompt, re.I):
        raise EngineError('LOCAL_UNSUPPORTED_REQUEST', 'Review the chord chart, bass and drums individually to remove accompaniment.')
    if re.search(r'\b(?:warm(?:er)?|bright(?:er|en)?|tone)\b', prompt, re.I):
        raise EngineError('LOCAL_UNSUPPORTED_REQUEST', 'Review a complete tone change such as “warm the bass”, or combine tone and other changes in Guided edits.')
    from stereo_scope import requests_stereo_change
    if requests_stereo_change(prompt):
        raise EngineError('LOCAL_UNSUPPORTED_REQUEST', 'Use the track mix controls for stereo positioning. You can review the arrangement and mix together before creating a version.')
    import mlx.core as mx
    from mlx_lm import load, generate
    from mlx_lm.sample_utils import make_sampler
    mx.set_memory_limit(2 * 1024 ** 3)
    mx.set_cache_limit(128 * 1024 ** 2)
    mx.random.seed(0)
    started = time.monotonic()
    metadata = json.loads((HERE / 'assets.json').read_text())
    print('Loading the pinned local planner.', flush=True)
    model, tokenizer = load(str(HOME / metadata['modelDirectory']), tokenizer_config={'trust_remote_code': False})
    system = SYSTEM
    if 'chords' in active_stems(parent):
        system += ('\nThe selected version also has a chord track. backingVolume affects all backing, '
                   'including these chords. Return supported=false for any request to change only one track’s level.')
    messages = [{'role': 'system', 'content': system}]
    messages.append({'role': 'user', 'content': json.dumps({'current_arrangement': arrangement_settings(parent), 'request': prompt}, ensure_ascii=False)})
    encoded = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    if len(tokenizer.encode(encoded)) > 3072:
        raise EngineError('LOCAL_INVALID_REQUEST', 'The request is too long for this bounded local planner.')
    # Greedy non-thinking decoding for deterministic control classification.
    # Invalid/truncated output is rejected; there is no repair inference or retry.
    print('Planning with bounded prompt prefill.', flush=True)
    raw = generate(model, tokenizer, prompt=encoded, max_tokens=256,
                   sampler=make_sampler(temp=0), prefill_step_size=128, verbose=False)
    write_json(output / 'planner-output.json', {'text': raw, 'parentRecipe': parent, 'prompt': prompt,
                                               'peakMlxBytes': mx.get_peak_memory(), 'seconds': time.monotonic() - started})
    try:
        value = decode_plan(raw)
    except (ValueError, TypeError) as error:
        raise EngineError('LOCAL_PLAN_INVALID', 'The local model did not return a valid plan. Try a concrete instrument or backing change.') from error
    complete, value = checked_model_patch(value, parent, prompt, required)
    metadata = json.loads((HERE / 'assets.json').read_text())
    return {'recipe': complete, 'explanation': value['explanation'],
            'provenance': {'model': metadata['model'], 'revision': metadata['revision'], 'runtime': 'MLX LM 0.32.2',
                           'thinking': False, 'temperature': 0, 'seed': 0,
                           'maxTokens': 256, 'inferenceAttempts': 1,
                           'prefillStepSize': 128,
                           'parentRecipeSha256': canonical_hash(parent), 'changes': value['changes'],
                           'seconds': time.monotonic() - started, 'peakMlxBytes': mx.get_peak_memory()}}

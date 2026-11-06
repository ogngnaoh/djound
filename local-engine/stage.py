"""Short-lived offline child. The supervisor owns locks, deadlines and cancellation."""
import json
import os
from pathlib import Path
import socket
import sys
import traceback

from common import DEFAULT_RECIPE, EngineError, data_root, inside, notes, recipe, write_json


def blocked(*args, **kwargs):
    raise EngineError('LOCAL_NETWORK_BLOCKED', 'Network access is disabled during local inference.')


def main():
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    request_path, output = Path(sys.argv[1]), Path(sys.argv[2])
    request = json.loads(request_path.read_text())
    operation = request['operation']
    result = {'schemaVersion': 1, 'operation': operation}
    try:
        if operation == 'plan':
            from planner import plan
            fields = plan(request.get('prompt'), request.get('parentRecipe'), output,
                          notes=request.get('notes'), duration=request.get('duration'))
            if 'duration' in request:
                fields['recipe'] = recipe(fields['recipe'], request['duration'])
        else:
            from audio import render, transcribe
            if operation == 'transcribe':
                fields = transcribe(inside(request['sourceAudio'], data_root(), True), output)
            else:
                duration = request['duration']
                validated = notes(request['notes'], duration)
                specification = DEFAULT_RECIPE if operation == 'preview' else recipe(request['recipe'], duration)
                source = (inside(request['sourceAudio'], data_root(), True)
                          if specification['instrument'] == 'recorded' else None)
                fields = render(validated, duration, specification, output, source=source, source_sha=request.get('sourceSha256'))
        result.update(status='succeeded', **fields)
    except EngineError as error:
        result.update(status='failed', error={'code': error.code, 'message': error.message})
    except Exception:
        traceback.print_exc()
        result.update(status='failed', error={'code': 'LOCAL_ENGINE_FAILED',
                                            'message': 'The local engine could not finish this operation. Review the input and try a new request.'})
    write_json(output / 'stage-result.json', result)
    return 0 if result['status'] == 'succeeded' else 1


if __name__ == '__main__':
    raise SystemExit(main())

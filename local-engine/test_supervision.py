"""Supervisor control-path tests without loading models or audio dependencies."""
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common import EngineError
import worker


class Supervision(unittest.TestCase):
    def test_memory_monitor_failure_stops_child(self):
        for monitor in (SimpleNamespace(returncode=1, stdout=''),
                        SimpleNamespace(returncode=0, stdout='unrecognized output')):
            with self.subTest(monitor=monitor), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                child = Mock(pid=123456)
                child.poll.return_value = None
                ps = SimpleNamespace(returncode=0, stdout='123456 2000')
                with patch.object(worker.subprocess, 'Popen', return_value=child), \
                        patch.object(worker.subprocess, 'run', side_effect=[ps, monitor]), \
                        patch.object(worker, 'stop') as stop:
                    with self.assertRaises(EngineError) as failure:
                        worker.supervise(directory / 'request.json', directory, 'plan')
                    self.assertEqual(failure.exception.code, 'LOCAL_RESOURCE_MONITOR_FAILED')
                    stop.assert_called_once_with(child)

    def test_parent_loss_stops_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            child = Mock(pid=123456)
            child.poll.return_value = None
            with patch.object(worker.subprocess, 'Popen', return_value=child), \
                    patch.object(worker.os, 'getppid', return_value=worker.OWNER_PID + 1), \
                    patch.object(worker, 'stop') as stop:
                with self.assertRaises(EngineError) as failure:
                    worker.supervise(directory / 'request.json', directory, 'render')
                self.assertEqual(failure.exception.code, 'LOCAL_CANCELLED')
                stop.assert_called_once_with(child)

    def test_boolean_schema_version_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            request = Path(temporary) / 'request.json'
            request.write_text(json.dumps({'schemaVersion': True, 'operation': 'plan', 'prompt': 'Use guitar.'}))
            with self.assertRaises(EngineError) as failure:
                worker.request_values(request)
            self.assertEqual(failure.exception.code, 'LOCAL_INVALID_REQUEST')

    def test_model_readiness_follows_the_guided_prefix(self):
        for prompt in ['edit: add drums', '  EDit: bad command']:
            self.assertFalse(worker.needs_freeform({'operation': 'plan', 'prompt': prompt}))
        for prompt in ['add drums', 'editing: add drums', 'use the words edit: here']:
            self.assertTrue(worker.needs_freeform({'operation': 'plan', 'prompt': prompt}))
        for operation in ['transcribe', 'render', 'preview']:
            self.assertFalse(worker.needs_freeform({'operation': operation}))

    def test_audio_readiness_is_independent_of_optional_planner_weights(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            wheel = home / 'downloads/basic_pitch-0.4.0-py2.py3-none-any.whl'
            wheel.parent.mkdir()
            with worker.zipfile.ZipFile(wheel, 'w') as package:
                package.writestr('transcriber.py', 'pinned')
            (home / 'basic-pitch').mkdir()
            (home / 'basic-pitch/transcriber.py').write_text('pinned')
            (home / 'setup.json').write_text('{"rendererSha256":"hash","rendererSourceSha256":"hash"}')
            with patch.object(worker, 'HOME', home), patch.object(worker.sys, 'platform', 'darwin'), \
                    patch.object(worker.os, 'uname', return_value=SimpleNamespace(machine='arm64')), \
                    patch.object(worker, 'digest', return_value='hash'), patch.object(worker.shutil, 'which', return_value='ffmpeg'), \
                    patch.object(worker, 'verify_dependencies') as pins, \
                    patch.object(worker, 'verify', side_effect=lambda path, item: 'qwen35' not in path.parts) as assets:
                core = worker.readiness(check_freeform=False)
                self.assertTrue(core['ready'])
                pins.assert_called_once_with('requirements-audio.lock')
                self.assertFalse(any('qwen35' in call.args[0].parts for call in assets.call_args_list))
                status = worker.readiness()
                self.assertTrue(status['ready'])
                self.assertFalse(status['freeformAvailable'])
                self.assertIn('guided edit', status['freeformUnavailableReason'])
                (home / 'basic-pitch/transcriber.py').write_text('modified')
                self.assertFalse(worker.readiness(check_freeform=False)['ready'])

    def test_terminate_escalates_and_reaps_process_group(self):
        child = Mock(pid=123456)
        child.wait.side_effect = [subprocess.TimeoutExpired('child', 1), 0]
        with patch.object(worker.os, 'killpg') as kill:
            worker.stop(child)
            self.assertEqual([call.args for call in kill.call_args_list],
                             [(123456, worker.signal.SIGTERM), (123456, worker.signal.SIGKILL)])
            self.assertEqual(child.wait.call_count, 2)


if __name__ == '__main__':
    unittest.main()

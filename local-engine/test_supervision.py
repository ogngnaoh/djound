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

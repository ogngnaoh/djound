"""Offline tests only. Every provider response and credential is synthetic."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "free_provider", Path(__file__).resolve().parents[1] / "scripts/check-free-provider.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)
TOKEN = "hf_" + "syntheticOnly" * 3
USER = "djound-test"


def responses():
    return {
        "identity": {"type": "user", "id": "a" * 24, "name": USER,
                     "isPro": False, "canPay": False, "orgs": []},
        "quota": {"base": 300, "current": 90, "resetsAt": "2026-11-05T12:00:00Z",
                  "overquotaUsed": 0},
        "space": {"id": "facebook/MelodyFlow", "private": False, "disabled": False,
                  "sdk": "gradio", "sha": checker.SPACE_REVISION,
                  "runtime": {"stage": "RUNNING", "sha": checker.SPACE_REVISION,
                              "hardware": {"current": "zero-a10g", "requested": "zero-a10g"}}},
    }


class PolicyTests(unittest.TestCase):
    def run_check(self, data):
        calls = []
        def fetch(kind, token):
            calls.append(kind)
            self.assertEqual(TOKEN, token)
            return copy.deepcopy(data[kind])
        return checker.check(USER, TOKEN, fetch), calls

    def assert_blocked(self, data, code):
        with self.assertRaises(checker.CheckFailure) as caught:
            self.run_check(data)
        self.assertEqual(code, caught.exception.code)
        self.assertEqual(2, caught.exception.exit_code)

    def test_snapshot_is_not_generation_permission_and_contains_no_identity_or_token(self):
        summary, calls = self.run_check(responses())
        self.assertEqual(["identity", "quota", "space"], calls)
        self.assertEqual("READY_SNAPSHOT", summary["status"])
        self.assertFalse(summary["generation_authorized"])
        self.assertEqual(90, summary["gpu_seconds_remaining"])
        serialized = json.dumps(summary)
        self.assertNotIn(USER, serialized)
        self.assertNotIn(TOKEN, serialized)

    def test_identity_must_match_personal_account(self):
        for field, value in (("name", "someone-else"), ("type", "org"), ("id", "invalid")):
            with self.subTest(field=field):
                data = responses()
                data["identity"][field] = value
                self.assert_blocked(data, "PERSONAL_IDENTITY_MISMATCH")

    def test_paid_unknown_and_org_accounts_fail_before_quota(self):
        for field, value in (("isPro", True), ("isPro", None), ("isPro", 0),
                             ("canPay", True), ("canPay", None), ("orgs", [{"plan": "team"}]),
                             ("orgs", None)):
            with self.subTest(field=field, value=value):
                data = responses()
                data["identity"][field] = value
                called = []
                def fetch(kind, token):
                    called.append(kind)
                    return data[kind]
                with self.assertRaises(checker.CheckFailure):
                    checker.check(USER, TOKEN, fetch)
                self.assertEqual(["identity"], called)

    def test_unknown_or_nonfree_quota_cannot_pass(self):
        for field, value, code in (
            ("base", 2400, "FREE_QUOTA_NOT_VERIFIED"),
            ("current", 301, "FREE_QUOTA_NOT_VERIFIED"),
            ("current", 29.9, "FREE_GPU_QUOTA_INSUFFICIENT"),
            ("current", True, "QUOTA_SCHEMA_UNVERIFIED"),
            ("current", float("nan"), "QUOTA_SCHEMA_UNVERIFIED"),
            ("current", -1, "QUOTA_SCHEMA_UNVERIFIED"),
            ("overquotaUsed", 1, "FREE_QUOTA_NOT_VERIFIED"),
            ("resetsAt", "2026-11-05T12:00:00", "QUOTA_SCHEMA_UNVERIFIED"),
        ):
            with self.subTest(field=field, value=value):
                data = responses()
                data["quota"][field] = value
                self.assert_blocked(data, code)
        data = responses()
        del data["quota"]["overquotaUsed"]
        self.assert_blocked(data, "QUOTA_SCHEMA_UNVERIFIED")

    def test_raw_api_fields_not_sdk_field_names(self):
        data = responses()
        data["quota"]["remaining"] = data["quota"].pop("current")
        self.assert_blocked(data, "QUOTA_SCHEMA_UNVERIFIED")

    def test_run_window_is_separate_and_must_have_capacity(self):
        data = responses()
        data["quota"]["resetsAt"] = None
        data["quota"]["runs"] = {"used": 1, "limit": 2, "remaining": 1,
                                 "resetsAt": "2026-11-06T12:00:00Z"}
        summary, _ = self.run_check(data)
        self.assertIsNone(summary["gpu_resets_at"])
        self.assertEqual(1, summary["runs"]["remaining"])
        data["quota"]["runs"].update(used=2, remaining=0)
        self.assert_blocked(data, "FREE_RUN_QUOTA_EXHAUSTED")
        data["quota"]["runs"].update(remaining=1)
        self.assert_blocked(data, "RUN_QUOTA_SCHEMA_UNVERIFIED")

    def test_space_revision_and_runtime_cannot_drift_silently(self):
        for target, field, value in (("space", "private", True), ("space", "sha", "new"),
                                     ("runtime", "stage", "BUILDING"),
                                     ("runtime", "hardware", {"current": "t4-small"})):
            data = responses()
            selected = data["space"] if target == "space" else data["space"]["runtime"]
            selected[field] = value
            self.assert_blocked(data, "APPROVED_SPACE_CONFIGURATION_NOT_VERIFIED")


class CredentialTests(unittest.TestCase):
    def test_private_file_only_and_no_environment_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text(TOKEN + "\n")
            path.chmod(0o600)
            with patch.dict(os.environ, {"HF_TOKEN": "do-not-use"}):
                self.assertEqual(TOKEN, checker.read_token(path))
            path.chmod(0o644)
            with self.assertRaises(checker.CheckFailure):
                checker.read_token(path)
            path.chmod(0o600)
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(checker.CheckFailure):
                checker.read_token(link)
            with self.assertRaises(checker.CheckFailure):
                checker.read_token(directory)

    def test_header_injection_and_oversized_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            for value in (TOKEN + "\nX-Header: bad", "hf_" + "a" * 600, "not-a-token"):
                path.write_text(value)
                path.chmod(0o600)
                with self.assertRaises(checker.CheckFailure):
                    checker.read_token(path)

    def test_cli_errors_are_redacted_json(self):
        output = io.StringIO()
        with patch.object(checker, "read_token", side_effect=RuntimeError(TOKEN)), \
                contextlib.redirect_stdout(output):
            code = checker.main(["--token-file", "/unused", "--expected-user", USER])
        self.assertEqual(3, code)
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertEqual("CHECK_FAILED", json.loads(output.getvalue())["reason"])

    def test_help_never_reads_credentials(self):
        with patch.object(checker, "read_token") as read, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                checker.main(["--help"])
            self.assertEqual(0, caught.exception.code)
            read.assert_not_called()


class FakeSocket:
    def settimeout(self, timeout):
        if timeout <= 0:
            raise AssertionError("Unbounded timeout")


class FakeResponse:
    def __init__(self, status=200, body=b'{"ok":true}'):
        self.status = status
        self.body = io.BytesIO(body)

    def read1(self, size):
        return self.body.read(size)


class FakeConnection:
    def __init__(self, response):
        self.sock = FakeSocket()
        self.response = response
        self.requests = []
        self.closed = False

    def connect(self):
        pass

    def request(self, method, path, headers):
        self.requests.append((method, path, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class TransportTests(unittest.TestCase):
    def test_only_fixed_https_gets_and_no_token_on_public_metadata(self):
        for kind in checker.PATHS:
            connection = FakeConnection(FakeResponse())
            with patch.object(checker.http.client, "HTTPSConnection", return_value=connection) as factory:
                checker.get_public_api(kind, TOKEN)
            factory.assert_called_once_with("huggingface.co", timeout=10)
            self.assertEqual(1, len(connection.requests))
            method, path, headers = connection.requests[0]
            self.assertEqual("GET", method)
            self.assertEqual(checker.PATHS[kind], path)
            self.assertEqual(kind != "space", "Authorization" in headers)
            self.assertTrue(connection.closed)

    def test_redirects_errors_and_auth_rejection_never_retry(self):
        for status in (301, 302, 307, 401, 403, 429, 500):
            connection = FakeConnection(FakeResponse(status, TOKEN.encode()))
            with patch.object(checker.http.client, "HTTPSConnection", return_value=connection):
                with self.assertRaises(checker.CheckFailure) as caught:
                    checker.get_public_api("identity", TOKEN)
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertEqual(1, len(connection.requests))
            self.assertTrue(connection.closed)

    def test_bounded_body_and_strict_json(self):
        for body in (b"a" * (checker.MAX_RESPONSE + 1), b'{"a":1,"a":2}',
                     b'{"current":NaN}', b"[]", b"not json"):
            connection = FakeConnection(FakeResponse(body=body))
            with patch.object(checker.http.client, "HTTPSConnection", return_value=connection):
                with self.assertRaises(checker.CheckFailure):
                    checker.get_public_api("quota", TOKEN)
            self.assertTrue(connection.closed)

    def test_elapsed_deadline_stops_before_request(self):
        connection = FakeConnection(FakeResponse())
        with patch.object(checker.http.client, "HTTPSConnection", return_value=connection), \
                patch.object(checker.time, "monotonic", side_effect=[0, 11]):
            with self.assertRaises(checker.CheckFailure) as caught:
                checker.get_public_api("quota", TOKEN)
        self.assertEqual("PROVIDER_TIMEOUT", caught.exception.code)
        self.assertEqual([], connection.requests)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()

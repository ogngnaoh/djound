#!/usr/bin/env python3
"""Read-only MelodyFlow free-account readiness snapshot; never uploads or generates audio."""

import argparse
from datetime import datetime, timezone
import http.client
import json
import math
import os
import re
import signal
import stat
import sys
import time


HOST = "huggingface.co"
PATHS = {
    "identity": "/api/whoami-v2",
    "quota": "/api/spaces/zero-gpu/quota",
    "space": "/api/spaces/facebook/MelodyFlow",
}
SPACE_REVISION = "19da0cf4e7a8a33cb2f59cbc2f3c946ee60dc04e"
MAX_RESPONSE = 64 * 1024
REQUEST_SECONDS = 10
NOTICE = (
    "This is a readiness snapshot, not a reservation or a never-bill guarantee. "
    "Account, quota and service state can change. Djound does not yet use this token; "
    "no upload, generation or local allowance grant was performed."
)


class CheckFailure(Exception):
    def __init__(self, code, exit_code=3):
        self.code = code
        self.exit_code = exit_code


def read_token(path):
    """Read only the explicitly selected, owner-private regular file; never discover credentials."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "getuid"):
        raise CheckFailure("SECURE_TOKEN_FILE_UNSUPPORTED")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 512):
            raise CheckFailure("TOKEN_FILE_NOT_PRIVATE_REGULAR_FILE")
        raw = os.read(descriptor, 513)
        if len(raw) > 512:
            raise CheckFailure("TOKEN_FILE_INVALID")
        token = raw.decode("ascii").strip()
        if not re.fullmatch(r"hf_[A-Za-z0-9]{20,256}", token):
            raise CheckFailure("TOKEN_FILE_INVALID")
        return token
    except (OSError, UnicodeError):
        raise CheckFailure("TOKEN_FILE_UNREADABLE_OR_INVALID") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def parse_json(raw):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=object_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise CheckFailure("INVALID_PROVIDER_RESPONSE") from None


def get_public_api(kind, token):
    """Fixed HTTPS GET only; no redirects, proxy discovery, cookies, SDK or retries."""
    if kind not in PATHS:
        raise CheckFailure("UNSUPPORTED_ENDPOINT")
    connection = http.client.HTTPSConnection(HOST, timeout=REQUEST_SECONDS)
    started = time.monotonic()
    headers = {"Accept": "application/json", "User-Agent": "Djound-read-only-readiness/1"}
    if kind != "space":
        headers["Authorization"] = "Bearer " + token
    try:
        connection.connect()
        sock = connection.sock

        def remaining():
            seconds = REQUEST_SECONDS - (time.monotonic() - started)
            if seconds <= 0:
                raise CheckFailure("PROVIDER_TIMEOUT")
            sock.settimeout(seconds)

        remaining()
        connection.request("GET", PATHS[kind], headers=headers)
        remaining()
        response = connection.getresponse()
        if response.status in (401, 403):
            raise CheckFailure("TOKEN_OR_BILLING_READ_PERMISSION_REJECTED")
        if response.status != 200:
            # Do not follow Location or expose upstream bodies/reason strings.
            raise CheckFailure("PROVIDER_HTTP_REJECTED")
        body = bytearray()
        while True:
            remaining()
            chunk = response.read1(min(8192, MAX_RESPONSE + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE:
                raise CheckFailure("PROVIDER_RESPONSE_TOO_LARGE")
        return parse_json(body)
    except CheckFailure:
        raise
    except (OSError, http.client.HTTPException, ValueError):
        raise CheckFailure("PROVIDER_CONNECTION_FAILED") from None
    finally:
        connection.close()


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise CheckFailure("QUOTA_SCHEMA_UNVERIFIED", 2)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise CheckFailure("QUOTA_SCHEMA_UNVERIFIED", 2) from None


def check(expected_user, token, fetch=get_public_api):
    identity = fetch("identity", token)
    if not isinstance(identity, dict) or identity.get("type") != "user" \
            or identity.get("name") != expected_user \
            or not isinstance(identity.get("id"), str) \
            or not re.fullmatch(r"[0-9a-f]{24}", identity["id"]):
        raise CheckFailure("PERSONAL_IDENTITY_MISMATCH", 2)
    if identity.get("isPro") is not False or identity.get("canPay") is not False \
            or identity.get("orgs") != []:
        raise CheckFailure("FREE_PERSONAL_ACCOUNT_NOT_VERIFIED", 2)

    quota = fetch("quota", token)
    if not isinstance(quota, dict) or not all(key in quota for key in (
            "base", "current", "resetsAt", "overquotaUsed")) \
            or not all(number(quota[key]) for key in ("base", "current", "overquotaUsed")):
        raise CheckFailure("QUOTA_SCHEMA_UNVERIFIED", 2)
    if quota["base"] != 300 or quota["current"] > quota["base"] or quota["overquotaUsed"] != 0:
        raise CheckFailure("FREE_QUOTA_NOT_VERIFIED", 2)
    reset = timestamp(quota["resetsAt"])
    runs_summary = None
    if "runs" in quota:
        runs = quota["runs"]
        if not isinstance(runs, dict) or not all(key in runs for key in (
                "used", "limit", "remaining", "resetsAt")) \
                or not all(number(runs[key]) and float(runs[key]).is_integer()
                           for key in ("used", "limit", "remaining")) \
                or runs["limit"] < 1 or runs["used"] + runs["remaining"] != runs["limit"]:
            raise CheckFailure("RUN_QUOTA_SCHEMA_UNVERIFIED", 2)
        runs_summary = {"remaining": runs["remaining"], "resets_at": timestamp(runs["resetsAt"])}
        if runs["remaining"] < 1:
            raise CheckFailure("FREE_RUN_QUOTA_EXHAUSTED", 2)
    if quota["current"] < 30:
        raise CheckFailure("FREE_GPU_QUOTA_INSUFFICIENT", 2)

    space = fetch("space", token)
    runtime = space.get("runtime") if isinstance(space, dict) else None
    if not isinstance(runtime, dict) or space.get("id") != "facebook/MelodyFlow" \
            or space.get("private") is not False or space.get("disabled") is not False \
            or space.get("sdk") != "gradio" or space.get("sha") != SPACE_REVISION \
            or runtime.get("sha") != SPACE_REVISION or runtime.get("stage") != "RUNNING" \
            or runtime.get("hardware") != {"current": "zero-a10g", "requested": "zero-a10g"}:
        raise CheckFailure("APPROVED_SPACE_CONFIGURATION_NOT_VERIFIED", 2)
    return {"status": "READY_SNAPSHOT", "account": "verified_free_personal",
            "gpu_seconds_remaining": quota["current"], "gpu_resets_at": reset,
            "runs": runs_summary, "space": "facebook/MelodyFlow",
            "generation_authorized": False, "notice": NOTICE}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Token: dedicated free personal account, no billing/subscription/organization membership; "
        "fine-grained Billing > Read billing usage and payment method status permission. "
        "Configure the token in an owner-only local file, never in chat or a command argument. "
        "This checker does not configure the app. Exit 0: ready snapshot; 2: policy blocked; "
        "3: configuration or network check unavailable. " + NOTICE))
    parser.add_argument("--token-file", required=True, help="Explicit private token file (mode 600 or 400).")
    parser.add_argument("--expected-user", required=True, help="Expected HF personal username, not display name.")
    args = parser.parse_args(argv)
    previous_handler = None
    previous_timer = None
    try:
        # A total wall-clock bound also covers DNS resolution, which socket timeouts
        # alone do not bound. This CLI already requires a Unix private-file contract.
        if not hasattr(signal, "setitimer"):
            raise CheckFailure("BOUNDED_NETWORK_CHECK_UNSUPPORTED")
        def timed_out(_signum, _frame):
            raise CheckFailure("CHECK_TIMEOUT")
        previous_handler = signal.signal(signal.SIGALRM, timed_out)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, 35)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", args.expected_user):
            raise CheckFailure("EXPECTED_USER_INVALID")
        result = check(args.expected_user, read_token(args.token_file))
        code = 0
    except CheckFailure as failure:
        code = failure.exit_code
        result = {"status": "BLOCKED" if code == 2 else "UNAVAILABLE",
                  "reason": failure.code, "generation_authorized": False, "notice": NOTICE}
    except Exception:
        # Never print an exception that might include a URL/header/token or account response.
        code = 3
        result = {"status": "UNAVAILABLE", "reason": "CHECK_FAILED",
                  "generation_authorized": False, "notice": NOTICE}
    finally:
        if previous_timer is not None:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        if previous_handler is not None:
            signal.signal(signal.SIGALRM, previous_handler)
    print(json.dumps(result, indent=2, allow_nan=False))
    return code


if __name__ == "__main__":
    sys.exit(main())

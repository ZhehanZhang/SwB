"""Inbound webhook verification: signature schemes, provider presets, and the replay key each one yields.

Every check runs on the raw request body before anything parses it. Comparisons are constant time. A scheme that
carries a signed timestamp rejects requests outside its tolerance, and a scheme that carries a signed event id or a
signed body returns a replay key, so the block can refuse a second delivery of the same request.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from ...signing import HEADER_NAME_RE

SCHEMES = ("stripe", "linear", "sentry", "standard", "hmac-sha256", "hmac-sha1", "bearer", "path")
ALIASES = {"svix": "standard", "standard-webhooks": "standard"}
PRESETS = ("stripe", "linear", "sentry", "standard")
TOLERANCE_S = {"stripe": 300, "linear": 60, "standard": 300}
MAX_TOLERANCE_S = 3600
ENCODINGS = ("hex", "base64")
DEFAULT_HEADER = "X-Signature-256"
RESERVED_HEADERS = frozenset({"content-length", "content-type", "host", "cookie", "transfer-encoding", "connection"})
PREFIX_RE = re.compile(r"[!-~]{0,32}")
DELIVERY_RE = re.compile(r"[!-~]{1,128}")
MAX_SIGNATURES = 8


class VerifyError(Exception):
    """A request that fails verification; reason is a fixed phrase for the audit row."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Verified:
    replay_key: str | None = None
    delivery: str | None = None
    timestamp: int | None = None


def scheme(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"verify must be one of {', '.join(SCHEMES)}")
    name = ALIASES.get(value.strip().lower(), value.strip().lower())
    if name not in SCHEMES:
        raise ValueError(f"verify must be one of {', '.join(SCHEMES)}")
    return name


def settings(verify: str, *, header: Any = None, prefix: Any = None, encoding: Any = None, tolerance_s: Any = None) -> dict[str, Any]:
    """Validate the options a scheme takes. Presets fix their own header and prefix."""
    out: dict[str, Any] = {}
    if verify in ("hmac-sha256", "hmac-sha1"):
        header = DEFAULT_HEADER if header is None else header
        if not isinstance(header, str) or not HEADER_NAME_RE.fullmatch(header) or header.lower() in RESERVED_HEADERS:
            raise ValueError("header must be a valid HTTP header name")
        prefix = "" if prefix is None else prefix
        if not isinstance(prefix, str) or not PREFIX_RE.fullmatch(prefix):
            raise ValueError("prefix must be up to 32 visible characters")
        encoding = "hex" if encoding is None else encoding
        if encoding not in ENCODINGS:
            raise ValueError("encoding must be hex or base64")
        out.update(header=header, prefix=prefix, encoding=encoding)
    elif any(value is not None for value in (header, prefix, encoding)):
        raise ValueError(f"{verify} sets its own header; header, prefix and encoding apply to hmac-sha256 and hmac-sha1")
    if verify in TOLERANCE_S:
        tolerance = TOLERANCE_S[verify] if tolerance_s is None else tolerance_s
        if type(tolerance) is not int or not 1 <= tolerance <= MAX_TOLERANCE_S:
            raise ValueError(f"tolerance_s must be a whole number of seconds from 1 to {MAX_TOLERANCE_S}")
        out["tolerance_s"] = tolerance
    elif tolerance_s is not None:
        raise ValueError("tolerance_s applies to stripe, linear and standard")
    return out


def _digest(algorithm: str, key: bytes, message: bytes) -> bytes:
    return hmac.digest(key, message, algorithm)


def _same(given: str | bytes, expected: str | bytes) -> bool:
    a = given.encode("latin-1", "replace") if isinstance(given, str) else given
    b = expected.encode("latin-1", "replace") if isinstance(expected, str) else expected
    return hmac.compare_digest(a, b)


def _timestamp(value: str | None, now: float, tolerance: int, *, millis: bool = False) -> int:
    if value is None or not re.fullmatch(r"[0-9]{1,16}", value):
        raise VerifyError("missing timestamp")
    stamp = int(value)
    seconds = stamp / 1000 if millis else stamp
    if abs(now - seconds) > tolerance:
        raise VerifyError("timestamp outside tolerance")
    return stamp


def _body_key(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _json_field(body: bytes, *path: str) -> Any:
    try:
        value: Any = json.loads(body)
    except (ValueError, RecursionError):
        return None
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _delivery(value: Any) -> str | None:
    return value if isinstance(value, str) and DELIVERY_RE.fullmatch(value) else None


def stripe(secret: bytes, body: bytes, headers: Mapping[str, str], now: float, tolerance: int) -> Verified:
    raw = headers.get("stripe-signature")
    if not raw:
        raise VerifyError("missing signature")
    stamp, signatures = None, []
    for item in raw.split(",")[:32]:
        key, _, value = item.strip().partition("=")
        if key == "t":
            stamp = value
        elif key == "v1" and value:
            signatures.append(value)
    if not signatures:
        raise VerifyError("missing signature")
    expected = _digest("sha256", secret, f"{stamp}.".encode() + body).hex() if stamp is not None else ""
    if stamp is None or not any(_same(sig, expected) for sig in signatures[:MAX_SIGNATURES]):
        raise VerifyError("invalid signature")
    t = _timestamp(stamp, now, tolerance)
    event = _delivery(_json_field(body, "id"))
    return Verified(f"stripe:{event}" if event else f"stripe:{t}:{_body_key(body)}", event, t)


def linear(secret: bytes, body: bytes, headers: Mapping[str, str], now: float, tolerance: int) -> Verified:
    given = headers.get("linear-signature")
    if not given:
        raise VerifyError("missing signature")
    if not _same(given.strip().lower(), _digest("sha256", secret, body).hex()):
        raise VerifyError("invalid signature")
    stamp = _json_field(body, "webhookTimestamp")
    t = _timestamp(str(stamp) if type(stamp) is int else None, now, tolerance, millis=True)
    return Verified(_body_key(body), _delivery(headers.get("linear-delivery")), t // 1000)


def sentry(secret: bytes, body: bytes, headers: Mapping[str, str], now: float, tolerance: int) -> Verified:
    given = headers.get("sentry-hook-signature")
    if not given:
        raise VerifyError("missing signature")
    if not _same(given.strip().lower(), _digest("sha256", secret, body).hex()):
        raise VerifyError("invalid signature")
    return Verified(_body_key(body), _delivery(headers.get("request-id")))


def standard_key(secret: str) -> bytes:
    """A Standard Webhooks (Svix) secret is whsec_ plus base64; a bare value is used as raw key bytes."""
    if secret.startswith("whsec_"):
        try:
            return base64.b64decode(secret.removeprefix("whsec_"), validate=True)
        except (binascii.Error, ValueError):
            raise VerifyError("unusable secret") from None
    return secret.encode()


def standard_signature(key: bytes, msg_id: str, timestamp: int, body: bytes) -> str:
    return "v1," + base64.b64encode(_digest("sha256", key, f"{msg_id}.{timestamp}.".encode() + body)).decode()


def standard(secret: bytes, body: bytes, headers: Mapping[str, str], now: float, tolerance: int) -> Verified:
    prefix = "webhook" if headers.get("webhook-signature") else "svix"
    msg_id, stamp, raw = (headers.get(f"{prefix}-{part}") for part in ("id", "timestamp", "signature"))
    if not raw:
        raise VerifyError("missing signature")
    if msg_id is None or not DELIVERY_RE.fullmatch(msg_id):
        raise VerifyError("missing delivery id")
    if stamp is None or not re.fullmatch(r"[0-9]{1,16}", stamp):
        raise VerifyError("missing timestamp")
    expected = standard_signature(secret, msg_id, int(stamp), body)
    signatures = [sig for sig in raw.split(" ") if sig.startswith("v1,")][:MAX_SIGNATURES]
    if not any(_same(sig, expected) for sig in signatures):
        raise VerifyError("invalid signature")
    t = _timestamp(stamp, now, tolerance)
    return Verified(f"standard:{msg_id}", msg_id, t)


def generic(algorithm: str, secret: bytes, body: bytes, headers: Mapping[str, str], opts: Mapping[str, Any]) -> Verified:
    given = headers.get(opts["header"].lower())
    if not given:
        raise VerifyError("missing signature")
    digest = _digest(algorithm, secret, body)
    encoded = digest.hex() if opts["encoding"] == "hex" else base64.b64encode(digest).decode()
    value = given.strip()
    prefix = opts["prefix"]
    if prefix and not value.lower().startswith(prefix.lower()):
        raise VerifyError("invalid signature")
    value = value[len(prefix):]
    if not _same(value.lower() if opts["encoding"] == "hex" else value, encoded):
        raise VerifyError("invalid signature")
    return Verified(_body_key(body))


def bearer(secret: bytes, headers: Mapping[str, str]) -> Verified:
    auth = headers.get("authorization", "")
    if auth[:7].lower() != "bearer " or not auth[7:].strip():
        raise VerifyError("missing bearer token")
    if not _same(auth[7:].strip().encode(), secret):
        raise VerifyError("invalid bearer token")
    return Verified()


def path(secret: bytes, given: str | None) -> Verified:
    if not given or not _same(given.encode(), secret):
        raise VerifyError("unknown webhook")
    return Verified()


def check(verify: str, opts: Mapping[str, Any], secret: str, body: bytes, headers: Mapping[str, str], now: float,
          *, path_secret: str | None = None) -> Verified:
    """Verify one request with one secret, or raise VerifyError."""
    if verify == "path":
        return path(secret.encode(), path_secret)
    if path_secret is not None:
        raise VerifyError("unknown webhook")
    if verify == "bearer":
        return bearer(secret.encode(), headers)
    if verify == "standard":
        return standard(standard_key(secret), body, headers, now, opts["tolerance_s"])
    if verify == "stripe":
        return stripe(secret.encode(), body, headers, now, opts["tolerance_s"])
    if verify == "linear":
        return linear(secret.encode(), body, headers, now, opts["tolerance_s"])
    if verify == "sentry":
        return sentry(secret.encode(), body, headers, now, 0)
    return generic("sha1" if verify == "hmac-sha1" else "sha256", secret.encode(), body, headers, opts)


def verify_any(verify: str, opts: Mapping[str, Any], secrets: list[str], body: bytes, headers: Mapping[str, str], now: float,
               *, path_secret: str | None = None) -> Verified:
    """Try the current secret, then the previous one while its rotation grace lasts. The first failure reason wins."""
    first: VerifyError | None = None
    for secret in secrets:
        try:
            return check(verify, opts, secret, body, headers, now, path_secret=path_secret)
        except VerifyError as exc:
            first = first or exc
            if exc.reason in ("missing signature", "missing bearer token", "missing timestamp", "missing delivery id",
                              "timestamp outside tolerance"):
                break
    raise first or VerifyError("invalid signature")


def sign(verify: str, opts: Mapping[str, Any], secret: str, body: bytes, now: int, *, msg_id: str = "msg_test") -> dict[str, str]:
    """Headers a correct sender would attach, for webhook_test."""
    key = secret.encode()
    if verify == "stripe":
        return {"Stripe-Signature": f"t={now},v1={_digest('sha256', key, f'{now}.'.encode() + body).hex()}"}
    if verify == "linear":
        return {"Linear-Signature": _digest("sha256", key, body).hex(), "Linear-Delivery": msg_id}
    if verify == "sentry":
        return {"Sentry-Hook-Signature": _digest("sha256", key, body).hex(), "Sentry-Hook-Timestamp": str(now),
                "Sentry-Hook-Resource": "event_alert", "Request-ID": msg_id}
    if verify == "standard":
        return {"webhook-id": msg_id, "webhook-timestamp": str(now),
                "webhook-signature": standard_signature(standard_key(secret), msg_id, now, body)}
    if verify == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if verify in ("hmac-sha256", "hmac-sha1"):
        digest = _digest("sha1" if verify == "hmac-sha1" else "sha256", key, body)
        encoded = digest.hex() if opts["encoding"] == "hex" else base64.b64encode(digest).decode()
        return {opts["header"]: opts["prefix"] + encoded}
    return {}

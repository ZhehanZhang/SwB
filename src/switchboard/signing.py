import hmac
import re

STRIPE_SIGNATURE_HEADER = "Stripe-Signature"
HEADER_NAME_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def stripe_signature(secret: str | bytes, body: bytes, timestamp: int) -> str:
    key = secret.encode() if isinstance(secret, str) else secret
    message = f"{timestamp}.".encode() + body
    digest = hmac.digest(key, message, "sha256").hex()
    return f"t={timestamp},v1={digest}"

import re

BRIDGE_PATH = "/inbox/muse/bridge"
TOKEN_ENV = "MUSE_BRIDGE_TOKEN"
TOKEN = re.compile(r"[A-Za-z0-9_-]{43,128}")
CLAIM_ID = re.compile(r"[a-f0-9]{32}")
MESSAGE_LIMIT = 20_000
WAIT_SECONDS = 120
DELIVERY_TIMEOUT = 300
POLL_SECONDS = 5
NO_REPLY = "no_reply"
UNKNOWN = "delivery_unknown"

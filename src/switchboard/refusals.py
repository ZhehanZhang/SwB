OWNER_REQUIRED = "Use the owner's sign-in to call this tool."
CONVERSATION_REQUIRED = "Choose a conversation for your own agent."
SCOPE_REQUIRED = "Choose a project granted to this key."
TARGET_REQUIRED = "Choose a target granted to this key."
MODE_REQUIRED = "Use an action allowed by the conversation mode."
CONFIRMATION_REQUIRED = "Confirm a general memory write with the owner."
GRANT_REQUIRED = "Use an account granted this action."

SIGN_IN_FAILURES = {
    "/auth/setup": frozenset({"setup_failed", "setup_closed", "csrf"}),
    "/auth/link": frozenset({"link_used", "link_expired", "link_invalid", "csrf"}),
    "/auth/passkey/login/options": frozenset({"passkey_failed", "csrf"}),
    "/auth/passkey/login/verify": frozenset({"passkey_failed", "csrf"}),
    "/auth/passkey/register/options": frozenset({"passkey_failed"}),
    "/auth/passkey/register/verify": frozenset({"passkey_failed", "csrf"}),
    "/auth/oidc/callback": frozenset({"oidc_failed"}),
}


class PolicyRefusal(Exception):
    def __init__(self, reason: str, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason)

from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from ...claims import ClaimInputError, Field
from .. import BlockError

if TYPE_CHECKING:
    from ...core import Switchboard

APP_ID_ENV = "GITHUB_APP_ID"
INSTALLATION_ID_ENV = "GITHUB_INSTALLATION_ID"
PRIVATE_KEY_ENV = "GITHUB_APP_PRIVATE_KEY"
TOKEN_ENV = "GITHUB_TOKEN"
API_URL = "https://api.github.com"
ID_RE = re.compile(r"[1-9][0-9]{0,18}")
MAX_ID = (1 << 63) - 1
REPO_RE = re.compile(r"[A-Za-z0-9-]{1,100}/[A-Za-z0-9_.-]{1,100}")
PEM_RE = re.compile(r"-----BEGIN ((?:RSA )?PRIVATE KEY)-----\s*(.*?)\s*-----END \1-----", re.DOTALL)


def github_app(webhook_secret_env: str | None = None, *, webhook_secret_saved: bool = False) -> tuple[str, str, list[Field]]:
    settings = "https://github.com/settings/apps"
    fields = [Field(APP_ID_ENV, "App ID", "id", "The number at the top of the app's General settings.", link=settings),
              Field(PRIVATE_KEY_ENV, "Private key", "pem",
                    "Under Private keys, Generate a private key, then upload the .pem file it downloads.", link=settings)]
    if webhook_secret_env and not webhook_secret_saved:
        fields.append(Field(webhook_secret_env, "Webhook secret", "webhook_secret", "The secret set under Webhook, if the app sends events.",
                            optional=True, link=settings))
    return "GitHub App", "github", fields


def repo_name(value: Any) -> str | None:
    if isinstance(value, str) and REPO_RE.fullmatch(value) and value.rsplit("/", 1)[1] not in {".", ".."}:
        return value.lower()
    return None


def provider_id(value: Any) -> str | None:
    if type(value) is int or isinstance(value, str):
        value = str(value)
        if ID_RE.fullmatch(value) and int(value) <= MAX_ID:
            return value
    return None


def validate_app_id(value: str) -> None:
    if provider_id(value) is None:
        raise ClaimInputError("Enter a numeric App ID")


def load_rsa_private_key(pem: str) -> RSAPrivateKey:
    try:
        key = load_pem_private_key(pem.encode(), password=None)
        if isinstance(key, RSAPrivateKey):
            return key
    except (ValueError, TypeError, UnsupportedAlgorithm):
        pass
    raise ClaimInputError("Use the .pem file that GitHub downloads.")


def private_key(value: str) -> RSAPrivateKey:
    found = PEM_RE.fullmatch(value.strip().replace("\\n", "\n"))
    if found is None:
        found = PEM_RE.fullmatch(base64.b64decode(value, validate=True).decode().strip())
    if found is None:
        raise ValueError("invalid PEM")
    label, body = found.groups()
    body = "".join(body.split())
    pem = f"-----BEGIN {label}-----\n" + "\n".join(body[i:i + 64] for i in range(0, len(body), 64)) + f"\n-----END {label}-----\n"
    return load_rsa_private_key(pem)


class GitHubAPI:
    def __init__(self, core: Switchboard, app_id: str | None, installation_id: str | None,
                 app_id_env: str, private_key_env: str, token_env: str):
        self.core = core
        self.app_id = app_id
        self.installation_id = installation_id
        self.app_id_env = app_id_env
        self.private_key_env = private_key_env
        self.token_env = token_env
        self.http: httpx.AsyncClient | None = None
        self.tokens: dict[str, tuple[str, float, str, tuple[str, str, str, str]]] = {}
        self.pat_actor: str | None = None
        self._verified_pat = ""
        self._key_source: str | None = None
        self._key: RSAPrivateKey | None = None

    def credentials(self) -> tuple[str, str, str, str]:
        return (self.app_id or self.core.secret(self.app_id_env) or "",
                self.installation_id or self.core.secret(INSTALLATION_ID_ENV) or "",
                self.core.secret(self.private_key_env) or "", self.core.secret(self.token_env) or "")

    def auth_type(self) -> str | None:
        app_id, _, key, token = self.credentials()
        if provider_id(app_id) and self.app_key(key) is not None:
            return "app"
        if token:
            return "pat"
        return None

    def app_key(self, value: str) -> RSAPrivateKey | None:
        if value != self._key_source:
            self._key_source = value
            self._key = None
            if value:
                try:
                    self._key = private_key(value)
                except ValueError:
                    pass
        return self._key

    async def _response(self, method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
        if self.http is None:
            raise BlockError("GitHub API client is not running")
        try:
            response = await self.http.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        except (httpx.HTTPError, ValueError):
            raise BlockError("Could not reach the GitHub API") from None
        if response.status_code == 401:
            raise BlockError("GitHub did not accept the configured credentials (HTTP 401)")
        if response.status_code in {403, 404}:
            raise BlockError(f"GitHub denied access (HTTP {response.status_code}); check repository access and permissions")
        if not response.is_success:
            raise BlockError(f"GitHub rejected the request (HTTP {response.status_code})")
        return response

    @staticmethod
    def _data(response: httpx.Response, list_response: bool = False) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            raise BlockError("GitHub returned an invalid API response") from None
        if list_response and isinstance(data, list):
            return {}
        if not isinstance(data, dict):
            raise BlockError("GitHub returned an invalid API response")
        return data

    async def request(self, method: str, path: str, token: str, *, list_response: bool = False, **kwargs: Any) -> dict[str, Any]:
        return self._data(await self._response(method, path, token, **kwargs), list_response)

    async def verify_pat(self, token: str) -> list[str] | None:
        response = await self._response("GET", "/user", token)
        self.pat_actor = provider_id(self._data(response).get("id"))
        self._verified_pat = token
        raw = response.headers.get("X-OAuth-Scopes")
        return None if raw is None else sorted({scope.strip() for scope in raw.split(",") if scope.strip()})

    def app_jwt(self) -> str:
        app_id, installation_id, key, _ = self.credentials()
        if not provider_id(app_id) or not key or (installation_id and not provider_id(installation_id)):
            raise BlockError("Configure a valid GitHub App ID, private key, and optional installation ID")
        if (parsed_key := self.app_key(key)) is None:
            raise BlockError("The GitHub App private key is invalid")
        try:
            now = int(self.core.clock())
            return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, parsed_key, algorithm="RS256")
        except (binascii.Error, ValueError, TypeError, UnicodeError, jwt.PyJWTError):
            raise BlockError("The GitHub App private key is invalid") from None

    async def installation(self, repo: str | None, app_jwt: str, *, installation_id: str | None = None) -> tuple[str, dict[str, str]]:
        _, pinned_id, _, _ = self.credentials()
        expected_id = installation_id or pinned_id
        path = f"/repos/{repo}/installation" if repo else f"/app/installations/{expected_id}"
        data = await self.request("GET", path, app_jwt)
        ident = provider_id(data.get("id"))
        if not ident or (expected_id and ident != expected_id) or data.get("suspended_at"):
            raise BlockError("GitHub App installation is not authorized for this repository")
        raw = data.get("permissions")
        permissions = {key: value for key, value in raw.items() if value in ("read", "write")} if isinstance(raw, dict) else {}
        return ident, permissions

    async def write(self, repo: str, path: str, data: dict[str, Any], required: tuple[str, ...], *, method: str = "POST") -> dict[str, Any]:
        credentials = self.credentials()
        if self.auth_type() == "app":
            cached = self.tokens.get(repo)
            if cached and cached[1] > self.core.clock() + 60 and cached[2] in required and cached[3] == credentials:
                token = cached[0]
            else:
                app_jwt = self.app_jwt()
                ident, permissions = await self.installation(repo, app_jwt)
                permission = next((key for key in required if permissions.get(key) == "write"), None)
                if not permission:
                    raise BlockError(f"The GitHub App needs {' or '.join(key + ': write' for key in required)} for this repository")
                issued = await self.request("POST", f"/app/installations/{ident}/access_tokens", app_jwt,
                                            json={"repositories": [repo.split("/", 1)[1]], "permissions": {permission: "write"}})
                token = issued.get("token")
                if not isinstance(token, str) or not token:
                    raise BlockError("GitHub did not return an installation access token")
                try:
                    expiration = datetime.fromisoformat(issued["expires_at"]).timestamp()
                except (KeyError, TypeError, ValueError, OverflowError):
                    expiration = 0
                self.tokens[repo] = (token, expiration, permission, credentials)
        elif self.auth_type() == "pat":
            token = credentials[3]
            if self._verified_pat != token:
                self.pat_actor = None
                try:
                    await self.verify_pat(token)
                except BlockError:
                    pass
        else:
            raise BlockError("Connect a GitHub App or a repository-scoped GitHub token before using GitHub writes")
        return await self.request(method, f"/repos/{repo}{path}", token, list_response=path.endswith("/labels"), json=data)

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Callable

import aiohttp
import httpx
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...auth import Principal
from ...claims import NAME_RE as SECRET_NAME_RE
from ...block_env import GITHUB_SECRET as SECRET_ENV
from ...config import ConfigError
from ...guard import client_ip
from ...modes import GITHUB_CREDENTIAL_PROBLEM
from ...signing import HEADER_NAME_RE, STRIPE_SIGNATURE_HEADER, stripe_signature
from ...subject import subject, wake_text
from ...targets import slugify
from ...trust import wrap
from ...visibility import Who
from .. import Action, Block, BlockError, Call, Manifest
from .api import (
    API_URL,
    APP_ID_ENV,
    INSTALLATION_ID_ENV,
    PRIVATE_KEY_ENV,
    TOKEN_ENV,
    GitHubAPI,
    provider_id,
    repo_name,
)

if TYPE_CHECKING:
    from ...core import Switchboard

CHECKS = {"check_run", "check_suite", "workflow_run"}
EVENTS = CHECKS | {"issues", "issue_comment", "pull_request", "pull_request_review",
                   "pull_request_review_comment", "pull_request_review_thread", "push"}
DELIVERY_TTL_S = 86400
MAX_DELIVERIES = 100_000
RETRY_DELAYS = (0.0, 0.5, 1.0, 2.0)
WRITES = ("label", "comment", "open_issue", "link_duplicate", "notify_owner", "reply")
PERMISSION_TTL_S = 60
log = logging.getLogger("switchboard.github")


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8", "replace")


def _excerpt(value: str, budget: int) -> str:
    value = value[:budget]
    while len(_json(value)) > budget + 2:
        value = value[:len(value) // 2]
    return value


def trim(event: str, delivery: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Project the webhook to scalar fields, with a 500-character body and bounded escaped UTF-8 elsewhere."""
    if event not in EVENTS:
        return None
    check = _object(data.get(event)) if event in CHECKS else {}
    if event in CHECKS and check.get("status") != "completed":
        return None
    item = _object(data.get("issue" if event in {"issues", "issue_comment"} else "pull_request"))
    sender = _object(data.get("sender"))
    review = _object(data.get("review")) if event == "pull_request_review" else {}
    comment = _object(data.get("comment")) if event in {"issue_comment", "pull_request_review_comment"} else {}
    if event == "pull_request_review_thread":
        comments = _object(data.get("thread")).get("comments")
        if isinstance(comments, list) and comments:
            comment = _object(comments[0])
    detail = comment or review
    head = _object(item.get("head"))
    push = event == "push"
    commits = data.get("commits") if push else None
    fields = {
        "delivery": delivery, "event": event, "action": data.get("action"),
        "repo": _object(data.get("repository")).get("full_name"), "number": item.get("number", data.get("number")),
        "title": item.get("title") or check.get("display_title") or check.get("name"),
        "url": data.get("compare") if push else detail.get("html_url") or item.get("html_url") or check.get("html_url"),
        "actor": sender.get("login"), "is_bot": sender.get("type") == "Bot" if sender else None,
        "state": check.get("status") if check else item.get("state"), "merged": item.get("merged"), "draft": item.get("draft"),
        "head_sha": data.get("after") if push else head.get("sha") or check.get("head_sha"),
        "base_ref": data.get("base_ref") if push else _object(item.get("base")).get("ref"),
        "head_ref": head.get("ref") or check.get("head_branch") or _object(check.get("check_suite")).get("head_branch"),
        "check": check.get("name"), "conclusion": check.get("conclusion"), "review_state": review.get("state"),
        "ref": data.get("ref") if push else None,
        "commits": data.get("size", len(commits) if isinstance(commits, list) else None) if push else None,
        "body": detail.get("body") if detail.get("body") is not None else item.get("body"),
    }
    out = {}
    for key, value in fields.items():
        if key in {"number", "commits"}:
            if type(value) is int and 0 <= value < 2**64:
                out[key] = value
        elif key in {"is_bot", "merged", "draft"}:
            if isinstance(value, bool):
                out[key] = value
        elif isinstance(value, str):
            limit = {"url": 1024, "delivery": 256}.get(key, 240)
            out[key] = value[:500] if key == "body" else _excerpt(value, limit)
    return out


class _Sink:
    def __init__(self, url: str | None, signature_header: str | None = None, secret: str | None = None):
        self.url = url
        self.signature_header = signature_header
        self.secret = secret
        self.queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue(maxsize=1000)
        self.delivered = self.failed = 0

    def put(self, delivery: str, body: bytes) -> None:
        if not self.url or (self.signature_header and not self.secret):
            self.failed += 1
            return
        try:
            self.queue.put_nowait((delivery, body))
        except asyncio.QueueFull:
            self.failed += 1

    async def run(self, http: aiohttp.ClientSession, clock: Callable[[], float]) -> None:
        while True:
            delivery, body = await self.queue.get()
            try:
                headers = {"Idempotency-Key": delivery, "User-Agent": "Switchboard-GitHub", "Content-Type": "application/json"}
                delivered = False
                for delay in RETRY_DELAYS:
                    if delay:
                        await asyncio.sleep(delay)
                    if self.signature_header and self.secret:
                        headers[self.signature_header] = stripe_signature(self.secret, body, int(clock()))
                    try:
                        async with http.post(self.url, data=body, headers=headers, allow_redirects=False) as response:
                            status = response.status
                    except (aiohttp.ClientConnectionError, TimeoutError):
                        continue
                    except (aiohttp.ClientError, ValueError):
                        break
                    if status < 500:
                        delivered = 200 <= status < 300
                        break
                if delivered:
                    self.delivered += 1
                else:
                    self.failed += 1
            finally:
                self.queue.task_done()


class GitHubBlock(Block):
    connect_kinds = ("github",)
    manifest = Manifest(id="github", title="GitHub", kind="source", level_in="webhook",
                        speaks=("event",), secrets=(SECRET_ENV, APP_ID_ENV, INSTALLATION_ID_ENV, PRIVATE_KEY_ENV, TOKEN_ENV),
                        tools=tuple(f"github_{name}" for name in (*WRITES, "status")))

    def __init__(self, core: Switchboard, config: dict[str, Any]):
        super().__init__(core, config)
        secret_env = config.get("secret_env", SECRET_ENV)
        if not isinstance(secret_env, str):
            raise ConfigError(f"{self.name}.secret_env must name an environment variable")
        self.secret_env = secret_env
        self.secret = (core.secret(secret_env) or "").encode()
        sinks, routes = config.get("sinks") or {}, config.get("routes") or {}
        if not isinstance(sinks, dict) or any(not isinstance(s, dict) for s in sinks.values()):
            raise ConfigError(f"{self.name}.sinks must map names to settings")
        if not isinstance(routes, dict):
            raise ConfigError(f"{self.name}.routes must map repositories to sink lists")
        self.sinks = {}
        for name, sink in sinks.items():
            url_env = sink.get("url_env")
            if not isinstance(name, str) or url_env is not None and not isinstance(url_env, str):
                raise ConfigError(f"{self.name}.sinks needs string names and URL settings")
            url = core.secret(url_env) if url_env else sink.get("url")
            if url is not None and not isinstance(url, str):
                raise ConfigError(f"{self.name}.sinks needs string names and URL settings")
            signature = sink.get("signature")
            if signature not in (None, "stripe"):
                raise ConfigError(f"{self.name}.sinks.{name}.signature must be 'stripe' when set")
            header = secret = None
            if signature == "stripe":
                where = f"{self.name}.sinks.{name}"
                sink_secret_env = sink.get("secret_env")
                if not isinstance(sink_secret_env, str) or not sink_secret_env.strip():
                    raise ConfigError(f"{where}.secret_env is required for Stripe signing")
                header = sink.get("header")
                if header is not None and (not isinstance(header, str) or HEADER_NAME_RE.fullmatch(header) is None
                                           or header.lower() in {"content-type", "idempotency-key", "user-agent"}):
                    raise ConfigError(f"{where}.header must be a valid, non-reserved HTTP header name")
                header = header if header is not None else STRIPE_SIGNATURE_HEADER
                secret = core.secret(sink_secret_env)
            self.sinks[name] = _Sink(url, header, secret)
        self.repo_routes: dict[str, set[str]] = {}
        for repo, names in routes.items():
            if not isinstance(repo, str) or not isinstance(names, list) or any(not isinstance(n, str) or n not in self.sinks for n in names):
                raise ConfigError(f"{self.name}.routes must map repository names to lists of configured sinks")
            self.repo_routes.setdefault(repo.casefold(), set()).update(names)
        self.space_config = config.get("spaces") or {}
        self.repo_spaces: dict[str, tuple[str, str | None]] = {}
        if not isinstance(self.space_config, dict):
            raise ConfigError(f"{self.name}.spaces must map space labels to settings")
        for label, settings in self.space_config.items():
            if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?", label) or not isinstance(settings, dict):
                raise ConfigError(f"{self.name}.spaces must map space labels to settings")
            channels = settings.get("channels") or {}
            if not isinstance(channels, dict):
                raise ConfigError(f"{self.name}.spaces.{label}.channels must map GitHub repositories to modes")
            default_repo = settings.get("repo", label)
            if "repo" in settings and repo_name(default_repo) is None:
                raise ConfigError(f"{self.name}.spaces.{label}.repo must be an owner/repo name")
            entries = {**({default_repo: {}} if repo_name(default_repo) else {}), **channels}
            for repo, settings_for_repo in entries.items():
                key = repo_name(repo)
                if key is None or (key in self.repo_spaces and self.repo_spaces[key][0] != label):
                    raise ConfigError(f"{self.name}.spaces.{label} must use unique owner/repo channel names")
                project = _object(settings_for_repo).get("project", settings.get("project"))
                if project is not None and not isinstance(project, str):
                    raise ConfigError(f"{self.name}.spaces.{label}.project must be a project slug")
                self.repo_spaces[key] = (label, project)
        self.owner_ids = self._owners(config)
        self.space_owners = {space: self._owners(settings) for space, settings in self.space_config.items() if "owner_ids" in settings}
        for field in ("app_id", "installation_id"):
            if config.get(field) is not None and provider_id(config[field]) is None:
                raise ConfigError(f"{self.name}.{field} must be a numeric GitHub ID")
        app_id_env = config.get("app_id_env", APP_ID_ENV)
        private_key_env = config.get("private_key_env", PRIVATE_KEY_ENV)
        token_env = config.get("token_env", TOKEN_ENV)
        if any(not isinstance(name, str) or not SECRET_NAME_RE.fullmatch(name) for name in (app_id_env, private_key_env, token_env)):
            raise ConfigError(f"{self.name}.app_id_env, private_key_env, and token_env must name environment variables")
        if len({app_id_env, private_key_env, secret_env, token_env, INSTALLATION_ID_ENV}) < 5:
            raise ConfigError(f"{self.name} needs distinct environment variables for its GitHub credentials")
        self.api = GitHubAPI(core, provider_id(config.get("app_id")), provider_id(config.get("installation_id")), app_id_env, private_key_env, token_env)
        self.installation: dict[str, str] = {}
        self.installations: dict[str, dict[str, Any]] = {}
        self.missing_installations: dict[str, str] = {}
        self.pat_capabilities: dict[str, Any] | None = None
        self.checked_at: float | None = None
        self.installation_problem: str | None = None
        self._last_check: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._refresh_lock = asyncio.Lock()
        self._senders: OrderedDict[str, list[float]] = OrderedDict()
        self.seen: OrderedDict[str, float] = OrderedDict()
        self.dropped = 0
        self.http: aiohttp.ClientSession | None = None

    def _owners(self, config: dict[str, Any]) -> frozenset[str]:
        values = config.get("owner_ids", [])
        if not isinstance(values, list) or any(provider_id(value) is None for value in values):
            raise ConfigError(f"{self.name}.owner_ids must list numeric GitHub user IDs")
        return frozenset(str(value) for value in values)

    def registered_repositories(self) -> set[str]:
        return (set(self.repo_spaces) | {repo for repo in self.repo_routes if repo_name(repo)}
                | {repo for p in self.core.projects.list() for raw in p.links.get("repos", ()) if (repo := repo_name(raw))})

    def sink_names(self, repo: str) -> set[str]:
        return self.repo_routes.get(repo, set()) | self.repo_routes.get(f"{repo.split('/', 1)[0]}/*", set())

    def legacy_watch(self, repo: str) -> bool:
        return bool(self.sink_names(repo.casefold()))

    def space_for(self, repo: str) -> tuple[str, str | None] | None:
        key = repo_name(repo)
        if key is None:
            return None
        linked = [p.slug for p in self.core.projects.list() if key in p.links.get("repos", ())]
        if key not in self.repo_spaces and key not in self.repo_routes and not self.legacy_watch(key) and not linked:
            return None
        space, project = self.repo_spaces.get(key, (key, None))
        return space, project or (linked[0] if len(linked) == 1 else None)

    def bind(self) -> None:
        if sum(block.manifest.id == self.manifest.id for block in self.core.blocks.values()) > 1:
            raise ConfigError("only one github block can serve /github/webhook")

    def routes(self) -> list[Route]:
        return [Route("/github/webhook", self.webhook, methods=["POST"])]

    async def start(self) -> None:
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        self.api.http = httpx.AsyncClient(base_url=API_URL, timeout=10, transport=self.core.http_transport,
                                          headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10",
                                                   "User-Agent": "Switchboard-GitHub"})
        for sink in self.sinks.values():
            if sink.url:
                self.core.spawn(sink.run(self.http, self.core.clock))

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.close()
        if self.api.http is not None:
            await self.api.http.aclose()

    @property
    def online(self) -> bool:
        return bool(self.secret)

    def problems(self) -> list[str]:
        return [] if self.online else [f"{self.name}: webhook secret is missing"]

    def has_write_credential(self) -> bool:
        return self.api.auth_type() is not None

    def status(self) -> dict[str, Any]:
        credential = self.api.auth_type()
        return {"configured": True, "secret_present": self.online, "routes": len(self.repo_routes), "dropped": self.dropped,
                "sinks": {name: {"delivered": sink.delivered, "failed": sink.failed} for name, sink in self.sinks.items()},
                "credential": credential, "credential_problem": GITHUB_CREDENTIAL_PROBLEM if credential is None else None,
                "installation": dict(self.installation), "checked_at": self.checked_at,
                "installations": self.installations, "missing_installations": self.missing_installations,
                "installation_problem": self.installation_problem, "token_scopes": self.pat_capabilities["scopes"] if self.pat_capabilities else None,
                "modes": self.core.modes.status(block=self.name)}

    async def refresh_installation(self) -> None:
        async with self._refresh_lock:
            auth = self.api.auth_type()
            credentials = self.api.credentials()
            repos = tuple(sorted(self.registered_repositories())) if auth == "app" else ()
            now = self.core.clock()
            if self._last_check == (credentials, repos) and self.checked_at is not None and now - self.checked_at < PERMISSION_TTL_S:
                return
            installations, missing, problem, pat = {}, {}, None, None
            attempted = auth == "pat" or (auth == "app" and bool(repos or credentials[1]))
            if auth == "pat":
                try:
                    scopes = await self.api.verify_pat(credentials[3])
                except BlockError as exc:
                    pat = {"verified": False, "scopes": None, "scopes_exposed": None, "problem": str(exc)}
                else:
                    pat = {"verified": True, "scopes": scopes, "scopes_exposed": scopes is not None, "problem": None}
            elif attempted:
                try:
                    token = self.api.app_jwt()
                except BlockError as exc:
                    problem = str(exc)
                else:
                    limit = asyncio.Semaphore(8)

                    async def lookup(repo: str | None, installation_id: str | None = None) -> tuple[str, dict[str, str]] | str:
                        async with limit:
                            try:
                                return await self.api.installation(repo, token, installation_id=installation_id)
                            except BlockError as exc:
                                return str(exc)

                    candidates = repos or (None,)
                    for repo, result in zip(candidates, await asyncio.gather(*(lookup(repo) for repo in candidates))):
                        if isinstance(result, str):
                            if repo is None:
                                problem = result
                            else:
                                missing[repo] = result
                            continue
                        ident, permissions = result
                        found = installations.setdefault(ident, {"permissions": permissions, "repositories": []})
                        if repo is not None:
                            found["repositories"].append(repo)
                    if repos:
                        ids = tuple(installations)
                        for ident, result in zip(ids, await asyncio.gather(*(lookup(None, ident) for ident in ids))):
                            if isinstance(result, str):
                                problem = result
                                installations[ident]["permissions"] = {}
                            else:
                                installations[ident]["permissions"] = result[1]
            permissions = [row["permissions"] for row in installations.values()]
            self.installation = {key: "write" if all(p[key] == "write" for p in permissions) else "read"
                                 for key in permissions[0] if all(key in p for p in permissions)} if permissions else {}
            self.installations, self.missing_installations = installations, missing
            self.installation_problem = problem or ("GitHub App installation access could not be verified for every repository." if missing else None)
            self.pat_capabilities = pat
            self.checked_at = now if attempted else None
            self._last_check = credentials, repos

    def drop(self, event: str, data: dict[str, Any], reason: str) -> None:
        self.dropped += 1
        name = re.sub(r"[^\w.-]", "?", event[:64], flags=re.ASCII) or "-"
        action = data.get("action")
        if isinstance(action, str) and re.fullmatch(r"[\w.-]{1,64}", action, re.ASCII):
            name += f"/{action}"
        repo = _object(data.get("repository")).get("full_name")
        if not isinstance(repo, str) or not re.fullmatch(r"[\w.-]{1,64}/[\w.-]{1,128}", repo, re.ASCII):
            repo = "-"
        log.info("github: dropped %s for %s: %s", name, repo, reason)

    def next_step(self) -> str | None:
        if not self.online:
            if self.core.secret(self.secret_env):
                return "Restart SwB to apply the saved GitHub webhook secret."
            return "Save the GitHub webhook secret."
        if not self.registered_repositories() and not any(self.repo_routes.values()):
            return "Add a GitHub repository route or space in config/switchboard.yaml and restart SwB."
        return None

    async def connect_steps(self, kind: str, principal: "Principal", project: str | None, choose: dict[str, Any] | None) -> dict[str, Any]:
        present = bool(self.core.secret(self.secret_env))
        done = [f"GitHub webhook secret: {'present' if present else 'missing'}.",
                f"GitHub repository routes: {sum(bool(sinks) for sinks in self.repo_routes.values())}.",
                f"GitHub webhook endpoint: {self.core.base_url}/github/webhook."]
        step = self.next_step()
        steps = []
        if not present:
            steps.append(self.token_step(principal, self.secret_env, step, kind="webhook_secret", label="Webhook secret"))
        elif step:
            steps.append({"by": "person", "do": step})
        credential = (choose or {}).get("credential")
        auth = self.api.auth_type()
        app_id, _, key, _ = self.api.credentials()
        valid_id = provider_id(app_id) is not None
        valid_key = self.api.app_key(key) is not None
        repair_app = (app_id and not valid_id) or (key and not valid_key)
        if auth == "app":
            await self.refresh_installation()
            done.append("GitHub App credentials: present.")
            if self.installation_problem:
                steps.append({"by": "person", "do": self.installation_problem})
            elif self.checked_at is not None and self.installation.get("issues") != "write":
                steps.append({"by": "person", "do": "Grant Issues: write to the GitHub App installation for the triage repositories."})
        elif auth == "pat" and (credential == "pat" or (credential != "app" and not repair_app)):
            await self.refresh_installation()
            pat = self.pat_capabilities
            if pat and pat["verified"]:
                scope_info = (f"Reported scopes: {', '.join(pat['scopes']) or 'none'}." if pat["scopes_exposed"] else
                              "GitHub does not expose scopes for this token.")
                done.append(f"GitHub token verified. {scope_info} GitHub checks repository permissions on every write.")
            elif pat:
                steps.append({"by": "person", "do": pat["problem"]})
        elif credential == "pat":
            steps.append(self.token_step(principal, self.api.token_env, "Save a fine-grained GitHub token for the triage repositories with Issues: write."))
        elif credential == "app" or repair_app or present or self.registered_repositories():
            if not valid_id:
                name = self.api.app_id_env
                instruction = "Save the numeric App ID from your GitHub App settings."
                if self.core.cfg.env.get(name):
                    instruction += f" Clear {name} from the environment and restart SwB to use the saved App ID."
                steps.append(self.token_step(principal, name, instruction, kind="id", label="App ID"))
            if not valid_key:
                name = self.api.private_key_env
                instruction = "Save the .pem file from your GitHub App settings."
                if self.core.cfg.env.get(name):
                    instruction += f" Clear {name} from the environment and restart SwB to use the saved private key."
                steps.append(self.token_step(principal, name, instruction, kind="pem", label="Private key"))
            steps.append({"by": "you", "do": "For a fine-grained personal access token instead, call connect again with credential=pat.",
                          "call": {"tool": "connect", "arguments": {"what": kind, "choose": {"credential": "pat"}}}})
        return {"done": done, "next": steps,
                "installations": self.installations, "missing_installations": self.missing_installations,
                "pat_capabilities": self.pat_capabilities,
                "modes": {**self.core.modes.status(principal, self.name), **self.core.modes.summary(self.name, principal)}}

    def actions(self) -> list[Action]:
        return [Action("status", "GitHub status", "Check the GitHub webhook, repository modes and installation permissions.", self.status_action, read_only=True),
                Action("label", "Label an issue", "Add existing labels to an issue or pull request.", self.label, idempotent=True),
                Action("comment", "Comment on an issue", "Comment on an issue or pull request.", self.comment),
                Action("open_issue", "Open an issue", "Open a new issue on this project's GitHub repository.", self.open_issue),
                Action("link_duplicate", "Resolve a duplicate", "Comment with the canonical issue, add the duplicate label, and close as not planned.",
                       self.link_duplicate, destructive=True),
                Action("notify_owner", "Notify the owner", "Put a triage note and its permalink in the owner's inbox.", self.notify_owner),
                Action("reply", "Reply in a GitHub thread", "Post in the issue or pull request thread that woke this conversation.", self.reply)]

    def mcp_tools(self, mcp: Any, kit: Any) -> None:
        from .mcp_tools import register

        register(self, mcp, kit)

    async def status_action(self, call: Call) -> dict[str, Any]:
        principal = call.principal
        if principal.is_unrestricted_owner:
            await self.refresh_installation()
            return {**self.status(), "pat_capabilities": self.pat_capabilities}
        return {"configured": True, "modes": self.core.modes.status(principal, self.name)}

    def _check(self, call: Call, handle: str | None, action: str, repo: str | None = None) -> dict[str, Any] | None:
        try:
            meta = self.core.modes.check(call.principal, handle, action, repo=repo)
        except (PermissionError, ValueError) as exc:
            raise BlockError(str(exc)) from exc
        if meta is None and (handle or not call.principal.is_unrestricted_owner or (repo and self.space_for(repo) is None)):
            raise BlockError("Use your assist or triage conversation handle; only the owner can write to a registered repository without a handle")
        call.note(ref=meta.get("permalink") if meta else None, target=repo)
        return meta

    async def _write(self, call: Call, action: str, repo: str, path: str, data: dict[str, Any], handle: str | None,
                     *, permissions: tuple[str, ...] = ("issues",), meta: dict[str, Any] | None = None,
                     followups: tuple[tuple[str, str, dict[str, Any]], ...] = ()) -> dict[str, Any]:
        repo = repo_name(repo)
        if not repo:
            raise BlockError("Pass a GitHub repository as owner/repo")
        if meta is None:
            meta = self._check(call, handle, action, repo)
        try:
            response = await self.api.write(repo, path, data, permissions)
            for method, endpoint, payload in followups:
                response = await self.api.write(repo, endpoint, payload, permissions, method=method)
        except BlockError:
            if meta:
                self.core.modes.audit_action(call.principal, meta, action, "error")
            raise
        if meta:
            self.core.modes.audit_action(call.principal, meta, action)
        url = response.get("html_url")
        return {"ok": True, "repo": repo, **{key: response[key] for key in ("id", "number") if type(response.get(key)) is int},
                **({"url": url} if isinstance(url, str) and url.startswith("https://github.com/") else {})}

    @staticmethod
    def _number(number: int) -> int:
        if type(number) is not int or not provider_id(number):
            raise BlockError("Pass a positive GitHub issue or pull request number")
        return number

    @staticmethod
    def _text(value: str, limit: int, field: str = "text") -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise BlockError(f"{field} must contain between 1 and {limit} characters")
        return value.strip()

    def _labels(self, labels: list[str]) -> list[str]:
        if not isinstance(labels, list) or not 1 <= len(labels) <= 20:
            raise BlockError("Pass between 1 and 20 existing label names")
        return [self._text(name, 50, "label") for name in labels]

    async def label(self, call: Call, *, repo: str, number: int, labels: list[str], handle: str | None = None) -> dict[str, Any]:
        return await self._write(call, "label", repo, f"/issues/{self._number(number)}/labels", {"labels": self._labels(labels)}, handle)

    async def comment(self, call: Call, *, repo: str, number: int, body: str, handle: str | None = None) -> dict[str, Any]:
        return await self._write(call, "comment", repo, f"/issues/{self._number(number)}/comments", {"body": self._text(body, 65536, "body")}, handle)

    async def open_issue(self, call: Call, *, repo: str, title: str, body: str = "", labels: list[str] | None = None,
                         handle: str | None = None) -> dict[str, Any]:
        data = {"title": self._text(title, 256, "title"), **({"body": self._text(body, 65536, "body")} if body else {})}
        if labels:
            data["labels"] = self._labels(labels)
        return await self._write(call, "open_issue", repo, "/issues", data, handle)

    async def link_duplicate(self, call: Call, *, repo: str, number: int, duplicate_of: int, handle: str | None = None) -> dict[str, Any]:
        number, duplicate_of = self._number(number), self._number(duplicate_of)
        if number == duplicate_of:
            raise BlockError("An issue cannot duplicate itself")
        path = f"/issues/{number}"
        return await self._write(call, "link_duplicate", repo, f"{path}/comments", {"body": f"Duplicate of #{duplicate_of}"}, handle,
                                 followups=(("POST", f"{path}/labels", {"labels": ["duplicate"]}),
                                            ("PATCH", path, {"state": "closed", "state_reason": "not_planned"})))

    async def notify_owner(self, call: Call, *, handle: str, text: str) -> dict[str, Any]:
        meta = self._check(call, handle, "notify_owner")
        if not meta:
            raise BlockError("Use the triage conversation handle to notify the owner")
        text = self._text(text, 4000)
        note = self.core.inbox.open_handle(owner="owner", destination="mailbox", ref=call.principal.source, project=meta["project"],
                                           title="Triage note", meta={}, status="delivered")
        self.core.inbox.update_handle(note, open=False)
        await self.core.inbox.add(note, "triage_note", {"text": text, "permalink": meta["permalink"], "conversation": handle,
                                                       "block": meta["block"], "space": meta["space"], "channel": meta["channel"]})
        self.core.modes.audit_action(call.principal, meta, "notify_owner")
        return {"ok": True, "handle": note}

    async def reply(self, call: Call, *, handle: str, text: str) -> dict[str, Any]:
        meta = self._check(call, handle, "reply")
        if not meta or meta["block"] != self.name or not meta.get("number"):
            raise BlockError("This conversation did not come from a GitHub issue or pull request thread")
        number, repo = self._number(meta["number"]), meta["repo"]
        limit = self.core.modes.assist_max_chars if meta["mode"] == "assist" else 65536
        if review_comment := meta.get("review_comment"):
            path, permissions = f"/pulls/{number}/comments/{self._number(review_comment)}/replies", ("pull_requests",)
        else:
            path = f"/issues/{number}/comments"
            permissions = ("issues", "pull_requests") if meta["pull_request"] else ("issues",)
        return await self._write(call, "reply", repo, path, {"body": self._text(text, limit)}, handle, permissions=permissions, meta=meta)

    async def webhook(self, request: Request) -> Response:
        if not self.secret:
            return JSONResponse({"error": "webhook secret unavailable"}, 503)
        signature = request.headers.get("x-hub-signature-256")
        if not signature:
            return JSONResponse({"error": "invalid signature"}, 401)
        body = await request.body()
        expected = b"sha256=" + hmac.digest(self.secret, body, "sha256").hex().encode()
        if not hmac.compare_digest(signature.encode("latin-1"), expected):
            return JSONResponse({"error": "invalid signature"}, 401)
        event = request.headers.get("x-github-event", "")
        unsupported = event not in EVENTS and event != "ping"
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            if not unsupported:
                return JSONResponse({"error": "invalid JSON"}, 400)
            data = {}
        if unsupported:
            self.drop(event, _object(data), "unhandled event")
            return JSONResponse({"ok": True})
        if not isinstance(data, dict):
            return JSONResponse({"error": "expected a JSON object"}, 400)
        if event == "ping":
            zen = data.get("zen")
            return Response(_json({"ok": True, "zen": zen[:500] if isinstance(zen, str) else None}), media_type="application/json")
        delivery = request.headers.get("x-github-delivery", "")
        if not re.fullmatch(r"[!-~]{1,128}", delivery):
            return JSONResponse({"error": "invalid delivery id"}, 400)
        return JSONResponse({"ok": True}, background=BackgroundTask(self.dispatch, event, delivery, data, client_ip(request.scope)))

    async def dispatch(self, event: str, delivery: str, data: dict[str, Any], ip: str) -> None:
        from ...core import CoreError

        now = self.core.clock()
        while self.seen and next(iter(self.seen.values())) <= now - DELIVERY_TTL_S:
            self.seen.popitem(last=False)
        if delivery in self.seen:
            return
        if len(self.seen) >= MAX_DELIVERIES:
            self.seen.popitem(last=False)
        self.seen[delivery] = now
        payload = trim(event, delivery, data)
        if payload is None:
            self.drop(event, data, "not completed")
            return
        repo = repo_name(payload.get("repo"))
        location = self.space_for(repo) if repo else None
        if location is None:
            self.drop(event, data, "unrouted repository")
            self.core.audit.record(origin=self.name, source="github", client=ip, destination=self.name, action="webhook",
                                   outcome="dropped", target=repo, ref=delivery, detail="unrouted repository")
            return
        space, project = location
        config_channel = next((key for key in self.space_config.get(space, {}).get("channels", {}) if repo_name(key) == repo), repo)
        mode, _, actions, target, project = self.core.modes.policy(self.name, space, repo, project, parent=config_channel)
        sender = _object(data.get("sender"))
        user_id = provider_id(sender.get("id"))
        author = payload.get("actor")
        link = payload.get("url")
        event_name = "Issue" if event == "issues" else event.replace("_", " ").capitalize()
        summary = " ".join(part for part in (event_name, payload.get("action"), payload.get("conclusion")) if part)
        number = provider_id(payload.get("number"))
        event_title = f"{summary} #{number}" if number else summary
        delivery_note = f"GitHub delivery {delivery}."
        if mode == "watch":
            title = subject(author or "Unknown sender", "GitHub", repo, event_title, verb="")
            details = "\n\n".join(part for part in (summary + ".", payload.get("body")) if part)
            text = wake_text(title, details, link, delivery_note)
            body = _json({"subject": title, "text": text, **payload})
            for sink in self.sink_names(repo):
                self.sinks[sink].put(delivery, body)
            self.core.modes.watch(self.name, space, repo, delivery, user_id or "", author,
                                  payload.get("body") or payload.get("title") or event, link, target, project=project)
            return
        owner = (user_id is not None and user_id in self.space_owners.get(space, self.owner_ids)
                 and not payload.get("is_bot"))
        if mode == "off" or (mode == "work" and not owner):
            self.drop(event, data, "channel mode ignored event")
            return
        if not user_id or payload.get("is_bot") or (mode != "work" and user_id == self.api.pat_actor):
            return
        principal = Principal(self.name, ip, "github", origin=self.name, trust="owner" if owner else "external")
        who = Who(owner=owner, id=f"github:{user_id}", workspace=space)
        if not self.core.visibility.allows(who, f"channel:{space}/{repo}", f"channel:{self.name}/{repo}"):
            return
        if not target:
            return
        try:
            addr = await self.core.resolve_to(target)
            if not self.core.visibility.allows(who, f"target:{slugify(target)}", f"target:{slugify(addr.id)}"):
                return
            if not owner:
                recent = [seen for seen in self._senders.pop(user_id, []) if now - seen < 600]
                self._senders[user_id] = recent
                if len(recent) >= 5:
                    return
                recent.append(now)
                if len(self._senders) > MAX_DELIVERIES:
                    self._senders.popitem(last=False)
            self.core.turn(f"{self.name}:{repo}:{number or delivery}", principal)
            meta = self.core.modes.meta(self.name, space, repo, mode, actions, project, link, target)
            meta.update(repo=repo, number=int(number) if number else None, event=event,
                        pull_request=event.startswith("pull_request") or bool(_object(data.get("issue")).get("pull_request")))
            if event in {"pull_request_review_comment", "pull_request_review_thread"}:
                comment = _object(data.get("comment"))
                if event == "pull_request_review_thread":
                    comments = _object(data.get("thread")).get("comments")
                    comment = _object(comments[0]) if isinstance(comments, list) and comments else {}
                reply_to = provider_id(comment.get("in_reply_to_id") or comment.get("id"))
                if reply_to:
                    meta["review_comment"] = int(reply_to)
            text = wrap(json.dumps(payload, ensure_ascii=False), author, f"github:{user_id}", "external", f"in GitHub {repo}")
            title = subject(author or user_id, "GitHub", repo, event_title, verb="", trust=principal.trust)
            tail = delivery_note
            if mode in {"assist", "triage"}:
                tail += f"\n\n{self.core.modes.wake_note(meta)} Use github_reply for the originating GitHub thread."
            await self.core.send(principal, to=target, task=wake_text(title, text, link, tail), project=project, title=title,
                                 handle_meta={**({"channel_mode": meta} if mode in {"assist", "triage"} else {}),
                                              **({"boundary": project} if project and mode != "work" else {})})
        except (BlockError, CoreError, ValueError) as exc:
            log.warning("GitHub wake for %s failed: %s", repo, type(exc).__name__)
            self.core.audit.record(origin=self.name, source="github", client=ip, destination=self.name, action="webhook",
                                   outcome="error", target=repo, ref=delivery, detail=type(exc).__name__)

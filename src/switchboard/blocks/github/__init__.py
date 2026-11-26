from __future__ import annotations

import asyncio
import hmac
import json
import re
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import aiohttp
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...config import ConfigError
from ...guard import client_ip
from .. import Block, Manifest

if TYPE_CHECKING:
    from ...auth import Principal
    from ...core import Switchboard

SECRET_ENV = "GITHUB_WEBHOOK_SECRET"
CHECKS = {"check_run", "check_suite", "workflow_run"}
EVENTS = CHECKS | {"issues", "issue_comment", "pull_request", "pull_request_review",
                   "pull_request_review_comment", "pull_request_review_thread", "push"}
DELIVERY_TTL_S = 86400
MAX_DELIVERIES = 100_000
RETRY_DELAYS = (0.0, 0.5, 1.0, 2.0)


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
    def __init__(self, url: str | None):
        self.url = url
        self.queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue(maxsize=1000)
        self.delivered = self.failed = 0

    def put(self, delivery: str, body: bytes) -> None:
        if not self.url:
            self.failed += 1
            return
        try:
            self.queue.put_nowait((delivery, body))
        except asyncio.QueueFull:
            self.failed += 1

    async def run(self, http: aiohttp.ClientSession) -> None:
        while True:
            delivery, body = await self.queue.get()
            try:
                headers = {"Idempotency-Key": delivery, "User-Agent": "Switchboard-GitHub", "Content-Type": "application/json"}
                delivered = False
                for delay in RETRY_DELAYS:
                    if delay:
                        await asyncio.sleep(delay)
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
                        speaks=("event",), secrets=(SECRET_ENV,))

    def __init__(self, core: Switchboard, config: dict[str, Any]):
        super().__init__(core, config)
        env = core.cfg.env
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
            url = env.get(url_env) if url_env else sink.get("url")
            if url is not None and not isinstance(url, str):
                raise ConfigError(f"{self.name}.sinks needs string names and URL settings")
            self.sinks[name] = _Sink(url)
        self.repo_routes: dict[str, set[str]] = {}
        for repo, names in routes.items():
            if not isinstance(repo, str) or not isinstance(names, list) or any(not isinstance(n, str) or n not in self.sinks for n in names):
                raise ConfigError(f"{self.name}.routes must map repository names to lists of configured sinks")
            self.repo_routes.setdefault(repo.casefold(), set()).update(names)
        self.seen: OrderedDict[str, float] = OrderedDict()
        self.dropped = 0
        self.http: aiohttp.ClientSession | None = None

    def bind(self) -> None:
        if sum(block.manifest.id == self.manifest.id for block in self.core.blocks.values()) > 1:
            raise ConfigError("only one github block can serve /github/webhook")

    def routes(self) -> list[Route]:
        return [Route("/github/webhook", self.webhook, methods=["POST"])]

    async def start(self) -> None:
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        for sink in self.sinks.values():
            if sink.url:
                self.core.spawn(sink.run(self.http))

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.close()

    @property
    def online(self) -> bool:
        return bool(self.secret)

    def problems(self) -> list[str]:
        return [] if self.online else [f"{self.name}: webhook secret is missing"]

    def status(self) -> dict[str, Any]:
        return {"configured": True, "secret_present": self.online, "routes": len(self.repo_routes), "dropped": self.dropped,
                "sinks": {name: {"delivered": sink.delivered, "failed": sink.failed} for name, sink in self.sinks.items()}}

    def next_step(self) -> str | None:
        if not self.online:
            if self.core.secret(self.secret_env):
                return "Restart Switchboard to apply the saved GitHub webhook secret."
            return "Save the GitHub webhook secret."
        if not any(self.repo_routes.values()):
            return "Add a GitHub repository route in config/switchboard.yaml and restart Switchboard."
        return None

    async def connect_steps(self, kind: str, principal: "Principal", project: str | None, choose: dict[str, Any] | None) -> dict[str, Any]:
        present = bool(self.core.secret(self.secret_env))
        done = [f"GitHub webhook secret: {'present' if present else 'missing'}.",
                f"GitHub repository routes: {sum(bool(sinks) for sinks in self.repo_routes.values())}."]
        step = self.next_step()
        if not present:
            return {"done": done, "next": [self.token_step(principal, self.secret_env, step)]}
        return {"done": done, "next": [{"by": "person", "do": step}] if step else []}

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
        if event not in EVENTS and event != "ping":
            self.dropped += 1
            return JSONResponse({"ok": True})
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            return JSONResponse({"error": "invalid JSON"}, 400)
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
            self.dropped += 1
            return
        repo = _object(data.get("repository")).get("full_name")
        sinks = set()
        if isinstance(repo, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            key = repo.casefold()
            owner = key.split("/", 1)[0]
            sinks = self.repo_routes.get(key, set()) | self.repo_routes.get(f"{owner}/*", set())
        else:
            repo = None
        if not sinks:
            self.dropped += 1
            self.core.audit.record(origin=self.name, source="github", client=ip, destination=self.name, action="webhook",
                                   outcome="dropped", target=repo[:240] if repo else None, ref=delivery, detail="unrouted repository")
            return
        body = _json(payload)
        for sink in sinks:
            self.sinks[sink].put(delivery, body)

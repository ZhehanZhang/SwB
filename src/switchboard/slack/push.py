from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextvars import ContextVar
from typing import Any

import httpx

from ..signing import STRIPE_SIGNATURE_HEADER, stripe_signature
from .config import SinkConfig
from .events import render_payload

log = logging.getLogger("switchboard.slack.push")
_private_webhook_request = ContextVar("slack_private_webhook_request", default=False)


def _public_http_log(record: logging.LogRecord) -> bool:
    return not _private_webhook_request.get()


for _logger in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy", "httpcore.socks", "hpack.hpack"):
    logging.getLogger(_logger).addFilter(_public_http_log)


def sign(secret: str, stamp: str, body: bytes) -> str:
    """Hex HMAC-SHA256 of "<timestamp>.<body>" with the sink's secret: what X-Switchboard-Signature carries after "v1="."""
    return hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class WebhookSink:
    def __init__(self, workspace: str, cfg: SinkConfig, http: httpx.AsyncClient, retries: int = 4):
        self.workspace = workspace
        self.cfg = cfg
        self.http = http
        self.retries = retries
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10000)
        self.delivered = 0
        self.failed = 0
        self._task: asyncio.Task | None = None

    def accepts(self, event: dict[str, Any]) -> bool:
        if event["kind"] not in self.cfg.kinds:
            return False
        if self.cfg.targets and event.get("target") not in self.cfg.targets:
            return False
        if self.cfg.channels:
            names = {event["channel"], event.get("channel_name") or "", "#" + (event.get("channel_name") or "")}
            return bool(names & set(self.cfg.channels))
        return True

    def put(self, event: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.failed += 1
            log.error("sink %s queue full, dropping %s", self.cfg.name, event["id"])

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name=f"sink:{self.cfg.name}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def next_batch(self) -> list[dict[str, Any]]:
        first = await self.queue.get()
        batch = [first]
        if self.cfg.coalesce_seconds <= 0:
            return batch
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.cfg.coalesce_seconds
        while len(batch) < self.cfg.max_batch:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                batch.append(await asyncio.wait_for(self.queue.get(), remaining))
            except asyncio.TimeoutError:
                break
        return batch

    async def run(self) -> None:
        while True:
            batch = await self.next_batch()
            batched = self.cfg.coalesce_seconds > 0
            key = batch[0]["id"] if not batched else f"batch:{batch[0]['id']}:{batch[-1]['id']}:{len(batch)}"
            await self.deliver(render_payload(batch, batched=batched), len(batch), idempotency_key=key)

    async def deliver(self, payload: dict[str, Any], count: int = 1, idempotency_key: str | None = None) -> bool:
        if not self.cfg.url:
            self.failed += count
            log.warning("sink %s has no URL; dropped %d event(s)", self.cfg.name, count)
            return False
        secret = self.cfg.secret
        stripe_header = None
        if self.cfg.signature == "stripe":
            if not secret:
                self.failed += count
                log.warning("sink %s has no signing secret; dropped %d event(s)", self.cfg.name, count)
                return False
            stripe_header = self.cfg.header or STRIPE_SIGNATURE_HEADER
        headers = dict(self.cfg.headers)
        if idempotency_key:
            headers["Idempotency-Key"] = f"slackgate:{self.workspace}:{idempotency_key}"
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
        if stripe_header:
            headers = {name: value for name, value in headers.items() if name.lower() != stripe_header.lower()}
        elif secret:
            stamp = str(int(time.time()))
            headers["X-Switchboard-Timestamp"] = stamp
            headers["X-Switchboard-Signature"] = "v1=" + sign(secret, stamp, body)
        delay = 1.0
        request_options = {"follow_redirects": False} if stripe_header else {}
        for attempt in range(1, self.retries + 1):
            if stripe_header and secret:
                headers[stripe_header] = stripe_signature(secret, body, int(time.time()))
            private = _private_webhook_request.set(True)
            try:
                resp = await self.http.post(self.cfg.url, content=body, headers=headers, timeout=15, **request_options)
                if resp.status_code < 300:
                    self.delivered += count
                    return True
                if resp.status_code == 429:
                    delay = float(resp.headers.get("retry-after", delay) or delay)
                elif resp.status_code < 500:
                    log.error("sink %s rejected payload: HTTP %d", self.cfg.name, resp.status_code)
                    break
                log.warning("sink %s HTTP %d (attempt %d)", self.cfg.name, resp.status_code, attempt)
            except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
                log.warning("sink %s error %s (attempt %d)", self.cfg.name, type(exc).__name__, attempt)
            finally:
                _private_webhook_request.reset(private)
            if attempt < self.retries:
                await asyncio.sleep(min(delay, 60))
                delay *= 2
        self.failed += count
        return False

    def status(self) -> dict[str, Any]:
        return {
            "name": self.cfg.name,
            "configured": bool(self.cfg.url),
            "queued": self.queue.qsize(),
            "delivered": self.delivered,
            "failed": self.failed,
            "coalesce_seconds": self.cfg.coalesce_seconds,
        }

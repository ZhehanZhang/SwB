from __future__ import annotations

import asyncio
import json
import platform
import random
import re
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from .. import __version__
from .caps.base import Capability, CapabilityError, CompanionContext, JobContext
from .state import CompanionError, CompanionState, valid_pair

DISCONNECTED = "This device was disconnected."


class Disconnected(Exception):
    pass


def _client(url: str, key: str | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=url + "/companion/",
        headers={"Authorization": f"Bearer {key}"} if key else {},
        timeout=15,
        trust_env=False,
        follow_redirects=False,
    )


def _hello(caps: dict[str, Capability]) -> dict[str, Any]:
    return {
        "name": socket.gethostname()[:120] or "Companion",
        "platform": platform.system().lower()[:64] or "unknown",
        "capabilities": [cap.manifest() for cap in caps.values()],
    }


async def pair(url: str, code: str, caps: dict[str, Capability], enabled: tuple[str, ...], drop_dir: Path) -> CompanionState:
    try:
        async with _client(url) as client:
            response = await client.post("pair", json={"code": code, **_hello(caps)})
        if response.status_code in (400, 401, 403, 404, 410):
            raise CompanionError("Check the SwB URL and pairing code.")
        response.raise_for_status()
        data = response.json()
        companion_id, key = data["companion_id"], data["device_key"]
        if not valid_pair(companion_id, key):
            raise ValueError
    except CompanionError:
        raise
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError, KeyError, RecursionError):
        raise CompanionError("Check the connection to your SwB server.") from None
    return CompanionState(url, companion_id, key, enabled, str(drop_dir))


async def _post(client: httpx.AsyncClient, endpoint: str, data: dict[str, Any]) -> httpx.Response:
    response = await client.post(endpoint, json=data)
    if response.status_code == 401:
        raise Disconnected
    response.raise_for_status()
    return response


async def _post_json(
    client: httpx.AsyncClient, endpoint: str, body: dict[str, Any], error: str,
) -> dict[str, Any]:
    try:
        response = await _post(client, endpoint, body)
        data = response.json()
        if isinstance(data, dict):
            return data
    except (httpx.HTTPError, ValueError, TypeError, RecursionError):
        pass
    raise CapabilityError(error)


async def disconnect(state: CompanionState, path: Path) -> None:
    try:
        async with _client(state.url, state.device_key) as client:
            await _post(client, "revoke", {})
    except Disconnected:
        pass
    except httpx.HTTPError:
        raise CompanionError("Check the connection to SwB and try disconnecting again.") from None
    state.clear(path)
    print(DISCONNECTED, flush=True)


async def _events(response: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    event = ""
    data = []
    async for line in response.aiter_lines():
        if not line:
            if event:
                yield event, "\n".join(data)
            event = ""
            data.clear()
            continue
        field, _, value = line.partition(":")
        if field == "event":
            event = value.removeprefix(" ")
        elif field == "data":
            data.append(value.removeprefix(" "))


def _job(data: str) -> tuple[str, str, str, dict[str, Any]] | None:
    try:
        value = json.loads(data)
        job_id, capability, action, args = (value["job_id"], value["capability"], value["action"], value["args"])
        if (isinstance(job_id, str) and re.fullmatch(r"cj_[0-9a-f]{32}", job_id)
                and isinstance(capability, str) and isinstance(action, str) and isinstance(args, dict)):
            return job_id, capability, action, args
    except (TypeError, ValueError, KeyError, RecursionError):
        pass
    return None


async def _execute(
    client: httpx.AsyncClient, caps: dict[str, Capability], job_id: str, capability: str, action: str, args: dict[str, Any],
) -> None:
    async def progress(data: dict[str, Any]) -> None:
        try:
            await _post(client, "results", {"job_id": job_id, "progress": data})
        except httpx.HTTPError:
            pass

    async def claim(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await _post_json(client, "claims", {"job_id": job_id, "kind": kind, "payload": payload},
                                "SwB could not accept the device claim.")

    cap = caps.get(capability)
    if cap is None or action not in cap.actions:
        result = {"ok": False, "error": "This capability or action is unavailable."}
    else:
        try:
            context = JobContext(job_id, progress, claim)
            result = {"ok": True, "result": await cap.invoke(action, args, context)}
            json.dumps(result, allow_nan=False)
        except Disconnected:
            raise
        except CapabilityError as error:
            result = {"ok": False, "error": str(error)}
        except Exception:
            result = {"ok": False, "error": "The device action failed."}
    try:
        await _post(client, "results", {"job_id": job_id, **result})
    except httpx.HTTPError:
        pass


async def _run_capability(client: httpx.AsyncClient, companion_id: str, cap: Capability) -> None:
    async def emit(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await _post_json(client, "emit", {"capability": cap.id, "kind": kind, "payload": payload},
                                "SwB could not accept the device event.")

    try:
        await cap.run(CompanionContext(companion_id, emit))
    except Disconnected:
        raise
    except Exception:
        pass


async def run(state: CompanionState, path: Path, caps: dict[str, Capability]) -> None:
    delay = 1.0
    slots = asyncio.Semaphore(4)
    disconnected = False
    async with _client(state.url, state.device_key) as client:
        try:
            while True:
                active: dict[str, asyncio.Task[None]] = {}
                hooks = []

                async def execute(job_id: str, capability: str, action: str, args: dict[str, Any]) -> None:
                    try:
                        async with slots:
                            await _execute(client, caps, job_id, capability, action, args)
                    finally:
                        active.pop(job_id, None)

                async with asyncio.TaskGroup() as tasks:
                    try:
                        await _post(client, "hello", {"version": __version__, **_hello(caps)})
                        async with client.stream("GET", "events", headers={"Accept": "text/event-stream"},
                                                 timeout=httpx.Timeout(15, read=75)) as response:
                            if response.status_code == 401:
                                raise Disconnected
                            response.raise_for_status()
                            if response.headers.get("content-type", "").partition(";")[0] != "text/event-stream":
                                raise httpx.StreamError("Unexpected event stream.")
                            hooks = [tasks.create_task(_run_capability(client, state.companion_id, cap))
                                     for cap in caps.values()]
                            async for event, data in _events(response):
                                if event == "revoked":
                                    raise Disconnected
                                if event in ("job", "ping"):
                                    delay = 1.0
                                if event == "job" and (job := _job(data)) and job[0] not in active:
                                    active[job[0]] = tasks.create_task(execute(*job))
                    except (httpx.HTTPError, httpx.StreamError):
                        pass
                    finally:
                        for task in (*hooks, *active.values()):
                            task.cancel()
                await asyncio.sleep(max(1, min(60, random.uniform(0.8, 1.2) * delay)))
                delay = min(delay * 2, 60.0)
        except* Disconnected:
            disconnected = True
    if disconnected:
        state.clear(path)
        print(DISCONNECTED, flush=True)

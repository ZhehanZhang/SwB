from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

MAX_ULID = "7ZZZZZZZZZZZZZZZZZZZZZZZZZ"
PR_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")


class CapyError(Exception):
    def __init__(self, status: int, tag: str, message: str):
        super().__init__(f"{tag} ({status}): {message}" if message else f"{tag} ({status})")
        self.status = status
        self.tag = tag


class CapyAPI:
    """Thin async client for the Capy public API. The key is sent only as a header and never logged."""

    def __init__(self, base: str, key: str, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30.0):
        self.http = self._client = httpx.AsyncClient(
            base_url=base.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {key}", "User-Agent": "switchboard/0.2"},
            timeout=timeout,
            transport=transport,
        )
        self._upload = httpx.AsyncClient(timeout=120.0, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._upload.aclose()

    async def request(self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(3):
            r = await self._client.request(method, path.lstrip("/"), json=json, params=params)
            if r.status_code == 429 and attempt < 2:
                wait = min(float(r.headers.get("retry-after", "2") or 2), 10.0)
                await asyncio.sleep(wait)
                continue
            break
        if r.status_code >= 400:
            tag, msg = "capy/Error", ""
            try:
                body = r.json()
                tag = body.get("_tag", tag)
                msg = body.get("message", "") or ""
            except ValueError:
                msg = r.text[:200]
            raise CapyError(r.status_code, tag, msg)
        return r.json() if r.content else None

    # Threads and messages

    async def create_thread(self, *, request_id: str, project_id: str, message: str, title: str | None = None, captain_id: str | None = None,
                            model: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"requestId": request_id, "projectId": project_id, "message": message}
        if title:
            body["title"] = title
        if captain_id:
            body["captainId"] = captain_id
        if model:
            body["model"] = {"modelId": model}
        return await self.request("POST", "threads", json=body)

    async def send_message(self, thread_id: str, text: str, *, files: list[dict[str, str]] | None = None, delivery: str | None = None,
                           client_key: str | None = None, model: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text}
        if model:
            body["model"] = {"modelId": model}
        if files:
            body["files"] = files
        if delivery:
            body["delivery"] = delivery
        if client_key:
            body["clientKey"] = client_key
        return await self.request("POST", f"threads/{thread_id}/message", json=body)

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        return await self.request("GET", f"threads/{thread_id}")

    async def list_threads(self, project_id: str, limit: int = 20) -> dict[str, Any]:
        return await self.request("GET", "threads", params={"projectId": project_id, "limit": limit})

    async def tail_messages(self, thread_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Newest messages, oldest first. Tries a backward read from the newest end; falls back to paging forward."""
        try:
            page = await self.request("GET", f"threads/{thread_id}/messages", params={"before": MAX_ULID, "limit": min(limit, 100)})
            return page.get("items", [])[-limit:]
        except CapyError as e:
            if e.status != 400:
                raise
        items: list[dict[str, Any]] = []
        after = None
        for _ in range(50):
            page = await self.request("GET", f"threads/{thread_id}/messages", params={"after": after, "limit": 100})
            items = (items + page.get("items", []))[-limit:]
            nxt = page.get("cursor")
            if not nxt or nxt == after:
                break
            after = nxt
        return items

    async def latest_cursor(self, thread_id: str) -> str | None:
        """The event id of the newest transcript entry, so a stream opened after it carries only what comes next."""
        page = await self.request("GET", f"threads/{thread_id}/messages", params={"before": MAX_ULID, "limit": 1})
        return page.get("beforeCursor") if page.get("items") else None

    async def download_url(self, thread_id: str, file_id: str) -> dict[str, Any]:
        return await self.request("GET", f"threads/{thread_id}/attachments/{file_id}/download")

    async def list_attachments(self, thread_id: str) -> list[dict[str, Any]]:
        try:
            page = await self.request("GET", f"threads/{thread_id}/attachments", params={"before": MAX_ULID, "limit": 50})
        except CapyError as e:
            if e.status != 400:
                raise
            page = await self.request("GET", f"threads/{thread_id}/attachments", params={"limit": 50})
        return page.get("items", [])

    async def upload(self, name: str, media_type: str, data: bytes) -> dict[str, str]:
        targets = await self.request("POST", "files", json={"files": [{"name": name, "mediaType": media_type, "sizeBytes": len(data)}]})
        t = targets["targets"][0]
        r = await self._upload.put(t["url"], content=data, headers={"Content-Type": media_type, "Content-Length": str(len(data))})
        if r.status_code >= 300:
            raise CapyError(r.status_code, "upload/Failed", f"file upload for {name} failed")
        return {"fileId": t["fileId"], "name": name}

    # Captains and projects

    async def list_captains(self) -> list[dict[str, Any]]:
        return (await self.request("GET", "captains", params={"limit": 50})).get("items", [])

    async def get_captain(self, captain_id: str) -> dict[str, Any]:
        return await self.request("GET", f"captains/{captain_id}")

    async def captain_digest(self, captain_id: str) -> dict[str, Any]:
        return await self.request("GET", f"captains/{captain_id}/digest")

    async def captain_crew(self, captain_id: str) -> list[dict[str, Any]]:
        return (await self.request("GET", f"captains/{captain_id}/crew")).get("items", [])

    async def get_project(self, project_id: str) -> dict[str, Any]:
        return await self.request("GET", f"projects/{project_id}")

    async def list_projects(self) -> list[dict[str, Any]]:
        return (await self.request("GET", "projects", params={"limit": 100})).get("items", [])


def pr_links(messages: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for m in messages:
        for url in PR_RE.findall(m.get("text") or ""):
            seen[url] = None
    return list(seen)


def thread_summary(t: dict[str, Any]) -> dict[str, Any]:
    ask = t.get("ask") or None
    return {
        "id": t.get("id"),
        "title": t.get("title"),
        "status": t.get("status"),
        "needs_you": bool(t.get("needsYou")),
        "ask": ask.get("text") if isinstance(ask, dict) else None,
        "project_id": t.get("projectId"),
        "captain": (t.get("captain") or {}).get("name"),
        "last_activity_at": t.get("lastActivityAt") or t.get("updatedAt"),
        "credits": (t.get("usage") or {}).get("totalCredits"),
    }

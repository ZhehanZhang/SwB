"""Owner-console routes for portable memory archives and Git backup."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .api_write import _Body, _body, _error, _owner
from .memory import MemoryError_
from .memory_archive import MAX_ARCHIVE_BYTES, MIME, export_zip, import_zip


class _BackupBody(_Body):
    remote: str | None = None
    branch: str | None = None
    author_name: str | None = None
    author_email: str | None = None
    enabled: bool | None = None
    credential: str | None = None


class _ImportOptions(_Body):
    trust_owner: bool = False
    overwrite: bool = False


class _RestoreBody(_ImportOptions):
    ref: str | None = None


async def download(request: Request) -> Response:
    data, _, date = await run_in_threadpool(export_zip, request.app.state.core, _owner(request))
    return Response(data, media_type=MIME, headers={"Content-Disposition": f'attachment; filename="swb-memory-{date}.zip"'})


async def upload(request: Request) -> Response:
    options = {}
    for name in _ImportOptions.model_fields:
        values = request.query_params.getlist(name)
        if values:
            if len(values) != 1 or values[0] not in ("true", "false"):
                return _error("invalid_request")
            options[name] = values[0] == "true"
    media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if media_type == "multipart/form-data":
        async with request.form(max_files=1, max_fields=0) as form:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                return _error("invalid_request")
            data = await file.read(MAX_ARCHIVE_BYTES + 1)
    elif media_type in ("", MIME, "application/octet-stream"):
        data = await request.body()
    else:
        return _error("invalid_request")
    if len(data) > MAX_ARCHIVE_BYTES:
        return _error("request_too_large", 413)
    try:
        return JSONResponse(await run_in_threadpool(import_zip, request.app.state.core, _owner(request), data, **options))
    except MemoryError_ as e:
        return _error(str(e))


async def backup(request: Request) -> Response:
    worker = request.app.state.core.memory_git
    if request.method in ("GET", "HEAD"):
        return JSONResponse(worker.status())
    body = await _body(request, _BackupBody)
    if body is None:
        return _error("invalid_request")
    try:
        return JSONResponse(await run_in_threadpool(worker.configure, _owner(request), **body.model_dump(exclude_none=True)))
    except MemoryError_ as e:
        return _error(str(e))


async def backup_now(request: Request) -> Response:
    try:
        return JSONResponse(request.app.state.core.memory_git.now(_owner(request)))
    except MemoryError_ as e:
        return _error(str(e))


async def backup_restore(request: Request) -> Response:
    body = await _body(request, _RestoreBody) if await request.body() else _RestoreBody()
    if body is None:
        return _error("invalid_request")
    try:
        return JSONResponse(await run_in_threadpool(request.app.state.core.memory_git.restore, _owner(request), **body.model_dump()))
    except MemoryError_ as e:
        return _error(str(e))


MEMORY_BACKUP_ROUTES = [
    Route("/memory/export", download),
    Route("/memory/import", upload, methods=["POST"]),
    Route("/memory/backup", backup, methods=["GET", "POST"]),
    Route("/memory/backup/now", backup_now, methods=["POST"]),
    Route("/memory/backup/restore", backup_restore, methods=["POST"]),
]

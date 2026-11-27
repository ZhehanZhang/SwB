import asyncio
import base64
import json
import os
from unittest.mock import AsyncMock, Mock

import pytest

from switchboard.companion.caps import registry
from switchboard.companion.caps.base import CapabilityError, JobContext
from switchboard.companion.caps.drop import MAX_FILE_SIZE, DropCapability
from switchboard.companion.caps.notify import NotifyCapability


@pytest.fixture
def context():
    return JobContext("test-job", AsyncMock(), AsyncMock())


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
async def test_notifications_keep_text_separate_from_commands(context, monkeypatch, platform):
    monkeypatch.setattr("switchboard.companion.caps.notify.sys.platform", platform)
    monkeypatch.setattr(NotifyCapability, "_executable", lambda self: "native-notifier")
    process = Mock(returncode=0, communicate=AsyncMock())
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    title = "-e ; $(a) `a`"
    body = "quote ' and \" café\nsecond line"

    assert await NotifyCapability().invoke("show", {"title": title, "body": body}, context) == {"shown": True}

    command = spawn.call_args.args
    assert command[0] == "native-notifier"
    if platform == "win32":
        assert command[1:4] == ("-NoProfile", "-NonInteractive", "-Command")
        assert title not in command[-1] and body not in command[-1]
        assert json.loads(process.communicate.call_args.args[0]) == {"title": title, "body": body}
    else:
        assert command[-3:] == ("--", title, body)
        process.communicate.assert_awaited_once_with(None)
        if platform == "darwin":
            assert "display notification (item 2 of argv) with title (item 1 of argv)" in command
    assert spawn.call_args.kwargs["stderr"] == asyncio.subprocess.DEVNULL


@pytest.mark.parametrize("args", [
    {"title": "a" * 81, "body": "text"},
    {"title": "title", "body": "a" * 501},
    {"title": "title", "body": "text\0"},
    {"title": 123, "body": "text"},
    {"title": "title"},
])
async def test_notifications_validate_before_spawning(context, monkeypatch, args):
    spawn = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(CapabilityError, match="80 characters"):
        await NotifyCapability().invoke("show", args, context)
    spawn.assert_not_called()


async def test_drop_put_get_list_and_duplicate_names(context, tmp_path):
    capability = DropCapability(tmp_path / "drop")
    put = {"name": "photo.png", "content_b64": "YWJj"}
    assert await capability.invoke("put", put, context) == {"name": "photo.png"}
    put["content_b64"] = "ZGVmZ2g="
    assert await capability.invoke("put", put, context) == {"name": "photo (2).png"}
    assert await capability.invoke("get", {"name": "photo.png"}, context) == {
        "name": "photo.png", "content_b64": "YWJj",
    }
    assert await capability.invoke("list", {}, context) == {
        "files": [{"name": "photo (2).png", "size": 5}, {"name": "photo.png", "size": 3}],
    }
    assert sorted(p.name for p in capability.path.iterdir()) == ["photo (2).png", "photo.png"]


async def test_drop_concurrent_put_allocates_distinct_names(context, tmp_path):
    capability = DropCapability(tmp_path)
    results = await asyncio.gather(*(
        capability.invoke("put", {"name": "note.txt", "content_b64": "YQ=="}, context) for _ in range(4)
    ))
    assert {result["name"] for result in results} == {"note.txt", *(f"note ({n}).txt" for n in range(2, 5))}


async def test_drop_list_names_can_be_passed_to_get_verbatim(context, tmp_path):
    capability = DropCapability(tmp_path)
    (tmp_path / "Résumé draft.txt").write_bytes(b"draft")
    (tmp_path / "swb-untrusted.txt").write_bytes(b"reserved")
    [file] = (await capability.invoke("list", {}, context))["files"]
    assert file == {"name": "Résumé draft.txt", "size": 5}
    assert await capability.invoke("get", {"name": file["name"]}, context) == {
        "name": "Résumé draft.txt", "content_b64": "ZHJhZnQ=",
    }


@pytest.mark.parametrize("name", ["", ".", "..", "a..b", "../outside", "nested/a", "nested\\a",
                                  "/absolute", "C:\\file", "note:stream", "NUL", "nul.txt", "a\0b"])
async def test_drop_rejects_non_basenames(context, tmp_path, name):
    capability = DropCapability(tmp_path / "drop")
    with pytest.raises(CapabilityError, match="file name"):
        await capability.invoke("put", {"name": name, "content_b64": ""}, context)
    with pytest.raises(CapabilityError, match="file name"):
        await capability.invoke("get", {"name": name}, context)
    assert not capability.path.exists()


async def test_drop_enforces_size_and_base64(context, tmp_path):
    capability = DropCapability(tmp_path)
    (tmp_path / "large").write_bytes(b"x" * (MAX_FILE_SIZE + 1))
    with pytest.raises(CapabilityError, match="10 MiB"):
        await capability.invoke("get", {"name": "large"}, context)
    with pytest.raises(CapabilityError, match="10 MiB"):
        await capability.invoke("put", {
            "name": "new", "content_b64": base64.b64encode(b"x" * (MAX_FILE_SIZE + 1)).decode("ascii"),
        }, context)
    with pytest.raises(CapabilityError, match="base64"):
        await capability.invoke("put", {"name": "new", "content_b64": "a==="}, context)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["large"]
    data = b"x" * MAX_FILE_SIZE
    encoded = base64.b64encode(data).decode("ascii")
    assert await capability.invoke("put", {"name": "max", "content_b64": encoded}, context) == {"name": "max"}
    assert (await capability.invoke("get", {"name": "max"}, context))["content_b64"] == encoded


@pytest.mark.skipif(os.name == "nt", reason="symlink creation can require privileges on Windows")
async def test_drop_blocks_symlinks_and_hardlinks(context, tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    capability = DropCapability(tmp_path / "drop")
    capability.path.mkdir()
    (capability.path / "link").symlink_to(outside)
    (capability.path / "hardlink").hardlink_to(outside)
    (capability.path / "folder").mkdir()
    assert await capability.invoke("list", {}, context) == {"files": []}
    for name in ("link", "hardlink", "folder"):
        with pytest.raises(CapabilityError, match="regular file"):
            await capability.invoke("get", {"name": name}, context)
    assert await capability.invoke("put", {"name": "link", "content_b64": "YQ=="}, context) == {
        "name": "link (2)",
    }
    assert outside.read_bytes() == b"private"
    symlink = tmp_path / "symlink"
    symlink.symlink_to(capability.path, target_is_directory=True)
    assert DropCapability(symlink).available()
    with pytest.raises(CapabilityError, match="regular Drop folder"):
        await DropCapability(symlink).invoke("list", {}, context)


def test_registry_advertises_enabled_available_capabilities(monkeypatch, tmp_path):
    monkeypatch.setattr(NotifyCapability, "_executable", lambda self: None)
    available, unavailable = registry(("drop", "notify", "drop"), tmp_path / "drop")
    assert list(available) == ["drop"]
    assert list(unavailable) == ["notify"]
    assert [action["name"] for action in available["drop"].manifest()["actions"]] == ["list", "get", "put"]
    assert not (tmp_path / "drop").exists()

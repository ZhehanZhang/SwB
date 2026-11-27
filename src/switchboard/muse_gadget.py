"""Run the SwB device on a Linux host with Meta's optional Gadget SDK."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .cli import CliError, _hidden_input, _write_file
from .muse_protocol import BRIDGE_PATH, MESSAGE_LIMIT, NO_REPLY, POLL_SECONDS, TOKEN, UNKNOWN, WAIT_SECONDS


class BridgeOffline(Exception):
    pass


async def exchange(client: httpx.AsyncClient, url: str, payload: dict[str, str]) -> dict[str, Any]:
    for attempt in range(4):
        try:
            response = await client.post(url, json=payload)
            if response.status_code in (401, 403):
                raise CliError("SwB rejected this bridge token. Check the claim and rerun with --setup.")
            if response.status_code in (404, 409) and "handle" in payload:
                return {"ok": False}
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data
        except (httpx.HTTPError, ValueError):
            pass
        if attempt < 3:
            await asyncio.sleep(POLL_SECONDS)
    raise BridgeOffline


async def relay(session: Any, client: httpx.AsyncClient, url: str, send_and_wait: Any, pending: Path) -> None:
    await session.registered.wait()
    print("SwB is ready for Muse messages.", flush=True)
    while True:
        if pending.exists():
            saved = json.loads(pending.read_text(encoding="utf-8"))
            if saved["url"] != url:
                raise CliError("Use a separate Muse state directory for this SwB server.")
            try:
                outcome = await exchange(client, url, saved["result"])
            except BridgeOffline:
                await asyncio.sleep(POLL_SECONDS)
                continue
            if not outcome.get("ok"):
                print("SwB refused a saved result. Check the conversation in Muse.", file=sys.stderr, flush=True)
            pending.unlink()
        claim = uuid4().hex
        try:
            work = (await exchange(client, url, {"claim": claim})).get("message")
        except BridgeOffline:
            await asyncio.sleep(POLL_SECONDS)
            continue
        if work is None:
            await asyncio.sleep(POLL_SECONDS)
            continue
        text = str(work["text"])
        session_id = str(work["session_id"])
        result = {"claim": claim, "handle": str(work["handle"]), "state": UNKNOWN}
        try:
            outcome = await send_and_wait(session, lambda: session.send_chat(text, session_id), session_id, WAIT_SECONDS)
            if outcome.get("ok"):
                reply = outcome.get("reply")
                if isinstance(reply, str) and reply.strip() and not outcome.get("partial"):
                    result.update(state="answered", text=reply[:MESSAGE_LIMIT])
                else:
                    result["state"] = NO_REPLY
        except (OSError, TimeoutError, ValueError):
            pass
        _write_file(pending, json.dumps({"url": url, "result": result}), force=True, mode=0o600)


def run_gadget(url: str, state_dir: Path, *, setup: bool, pair_again: bool) -> None:
    if sys.platform != "linux":
        raise CliError("The Muse gadget requires Linux and Bluetooth LE for pairing.")
    if pair_again and not setup:
        raise CliError("--pair-again requires --setup.")
    import fcntl

    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    if state_dir.is_symlink() or state_dir.stat().st_uid != os.getuid() or state_dir.stat().st_mode & 0o077:
        raise CliError("Use a private Muse state directory owned by your account (mode 0700).")
    with (state_dir / "bridge.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CliError("Stop the other SwB Muse bridge for this directory.") from None
        _run_gadget(url, state_dir.resolve(), setup=setup, pair_again=pair_again)


def _run_gadget(url: str, state_dir: Path, *, setup: bool, pair_again: bool) -> None:
    try:
        from musegadget import config, identity, pair
        from musegadget.service import Service, send_and_wait
    except ImportError:
        raise CliError("Install Meta's Linux Device SDK in the same Python environment as SwB. See the SwB Muse setup instructions.") from None

    os.environ[config.STATE_DIR_ENV] = str(state_dir)
    os.environ.pop(config.SDK_TOKEN_ENV, None)
    os.environ.pop(config.CUSTOM_SERVER_HOSTS_ENV, None)
    logging.getLogger("musegadget").setLevel(logging.CRITICAL + 1)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    token_path = state_dir / "swb_bridge_token"
    if setup:
        token = _hidden_input("SwB bridge token from the claim ").strip()
        if not TOKEN.fullmatch(token):
            raise CliError("Enter a random URL-safe SwB bridge token of 43 to 128 characters.")
    else:
        token = token_path.read_text(encoding="utf-8").strip() if token_path.is_file() else ""
        if not TOKEN.fullmatch(token):
            raise CliError("Run swb muse-bridge with --setup first.")
    if pair_again or not config.load_json(config.PAIRING_FILE):
        if not setup:
            raise CliError("Pair this SwB gadget with --setup.")
        sdk_token = _hidden_input("Personal Meta Gadget SDK token ").strip()
        sdk_path = state_dir / config.SDK_TOKEN_FILE
        os.environ[config.SDK_TOKEN_ENV] = sdk_token
        try:
            try:
                if not config.sdk_token():
                    raise ValueError
            except ValueError:
                raise CliError("Enter the SDK token from gadgets.muse.ai/settings/sdk-tokens.") from None
            try:
                pairing = pair.pair(
                    force=pair_again,
                    on_open=lambda window: print(
                        f"In the Muse app, turn on Settings > Devices > Developer mode. Choose Add Device > {window.ble_name}.",
                        flush=True,
                    ),
                )
            except Exception:
                raise CliError("Check Bluetooth LE, BlueZ, Python D-Bus bindings, and this account's access to BlueZ.") from None
        finally:
            os.environ.pop(config.SDK_TOKEN_ENV, None)
        if pairing.outcome not in (pair.PairOutcome.PAIRED, pair.PairOutcome.ALREADY_PAIRED):
            raise CliError("Check the SDK token and the Muse app, then run --setup again.")
        _write_file(sdk_path, sdk_token + "\n", force=True, mode=0o600)
    if setup:
        _write_file(token_path, token + "\n", force=True, mode=0o600)

    class NoCommands:
        def specs(self, **_: Any) -> dict:
            return {}

        def run(self, *_: Any) -> dict:
            return {"ok": False, "error": "The SwB gadget accepts messages only."}

    class Gadget(Service):
        async def serve(self, session: Any, vm: dict, stop: asyncio.Event) -> Any:
            async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=10,
                                         trust_env=False, follow_redirects=False) as client:
                link = asyncio.create_task(session.run(stop))
                bridge = asyncio.create_task(relay(session, client, url + BRIDGE_PATH, send_and_wait, state_dir / "pending.json"))
                try:
                    done, _ = await asyncio.wait((link, bridge), return_when=asyncio.FIRST_COMPLETED)
                    if bridge in done:
                        await bridge
                    return await link
                except CliError as error:
                    self.problem = error
                    self.stop()
                    return await link
                finally:
                    bridge.cancel()
                    link.cancel()
                    await asyncio.gather(link, bridge, return_exceptions=True)

    async def main() -> None:
        gadget = Gadget(identity.load_or_create(), NoCommands(), sdk_token=config.sdk_token(),
                        display_name="SwB", model_id="swb-muse-bridge", model_name="SwB", guide=None)
        gadget.problem = None
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, gadget.stop)
        try:
            await gadget.run()
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
        if gadget.problem:
            raise gadget.problem

    asyncio.run(main())

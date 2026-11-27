from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from typing import Any

from .base import ActionSpec, Capability, CapabilityError, JobContext


_APPLE_SCRIPT = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "end run",
)
_POWERSHELL_SCRIPT = """
$ErrorActionPreference = 'Stop'
$data = [Console]::In.ReadToEnd() | ConvertFrom-Json
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text/><text/></binding></visual></toast>')
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).InnerText = $data.title
$texts.Item(1).InnerText = $data.body
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(
    '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'
).Show($toast)
"""


class NotifyCapability(Capability):
    id = "notify"
    version = "1"
    title = "Notifications"
    actions = {
        "show": ActionSpec("Show a desktop notification.", {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 80},
                "body": {"type": "string", "maxLength": 500},
            },
            "required": ["title", "body"],
            "additionalProperties": False,
        }),
    }

    def _executable(self) -> str | None:
        if sys.platform == "darwin":
            return shutil.which("/usr/bin/osascript")
        if sys.platform.startswith("linux"):
            return shutil.which("notify-send")
        if sys.platform == "win32":
            root = os.environ.get("SystemRoot", r"C:\Windows")
            return shutil.which(os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"))
        return None

    def available(self) -> str | None:
        if self._executable() is None:
            return "Desktop notifications are unavailable on this device."
        return None

    async def invoke(self, action: str, args: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        if action != "show":
            raise CapabilityError("Choose a notification action.")
        title, body = args.get("title"), args.get("body")
        if (set(args) != {"title", "body"} or not isinstance(title, str) or not isinstance(body, str)
                or len(title) > 80 or len(body) > 500 or "\0" in title or "\0" in body):
            raise CapabilityError("Use a title of up to 80 characters and a body of up to 500 characters.")
        executable = self._executable()
        if executable is None:
            raise CapabilityError("Desktop notifications are unavailable on this device.")
        data = None
        if sys.platform == "darwin":
            command = [executable, *(arg for line in _APPLE_SCRIPT for arg in ("-e", line)), "--", title, body]
        elif sys.platform == "win32":
            command = [executable, "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_SCRIPT]
            data = json.dumps({"title": title, "body": body}).encode("ascii")
        else:
            command = [executable, "--", title, body]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.communicate(data), timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                await process.wait()
                raise
        except (OSError, TimeoutError, UnicodeError):
            raise CapabilityError("The desktop notification could not be shown.") from None
        if process.returncode:
            raise CapabilityError("The desktop notification could not be shown.")
        return {"shown": True}

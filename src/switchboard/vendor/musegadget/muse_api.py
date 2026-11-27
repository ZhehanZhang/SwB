# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Modified for SwB.

"""Client for Muse device credential rotation and leased VM lookup."""

from __future__ import annotations

import http.client
import json
import logging
import platform
import urllib.error
import urllib.request
from . import __version__, server_hosts, tls

log = logging.getLogger(__name__)

API_BASE = "https://api.muse.ai"
FETCH_PATH = "/fetch_vms"
REFRESH_PATH = "/device_token/refresh"
FETCH_TIMEOUT_S = 15   # the VM lookup's socket timeout (callers may bound the whole lookup by it)
MAX_RESPONSE_BYTES = 1024 * 1024


def api_root(api_url_v2: str = "") -> str:
    """The URL that API paths are appended to: ``api_url_v2``, or the Muse API.

    ``api_url`` is ignored. Only older firmware reads it, adding ``/hatch``.
    A base this client doesn't allow (``server_hosts``) gives the Muse API.
    """
    if not server_hosts.api_allowed(api_url_v2):
        log.warning("saved Muse API server not allowed; using the default")
        api_url_v2 = ""
    return api_url_v2 if isinstance(api_url_v2, str) and api_url_v2 else API_BASE


def user_agent() -> str:
    return f"musegadget/{__version__} Python/{platform.python_version()}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(req: urllib.request.Request) -> tuple[int, object]:
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=tls.client_context()),
    )
    with opener.open(req, timeout=FETCH_TIMEOUT_S) as resp:
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Muse API response too large")
        return resp.getcode(), json.loads(raw)


def fetch_vms_with_status(
    access_token: str, root: str = api_root()
) -> tuple[list[dict], int | None]:
    """Leased VMs for the device token, plus the HTTP status if one arrived.

    Returns ``(vms, status)``; ``status`` is ``None`` when no HTTP response
    was obtained. A 401 means the device token was rejected, which a retry
    won't fix; a ``None`` status is a transport failure worth retrying.
    """
    req = urllib.request.Request(api_root(root) + FETCH_PATH, method="GET")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("X-API-Version", "1.0.0")
    req.add_header("User-Agent", user_agent())
    try:
        status, data = _request(req)
    except urllib.error.HTTPError as exc:
        log.error("VM fetch failed: HTTP %d", exc.code)
        return [], exc.code
    except (http.client.HTTPException, OSError, ValueError, RecursionError, tls.TrustStoreError):
        log.warning("Muse VM lookup failed")
        return [], None

    if not isinstance(data, dict):
        log.warning("Muse VM lookup returned an invalid response")
        return [], status
    if data.get("error_title") or data.get("backend_error_code"):
        log.warning("Muse VM lookup was refused")
        return [], status
    vm_list = data.get("vm_list")
    if not isinstance(vm_list, list):
        log.error("VM fetch: missing vm_list")
        return [], status

    vms = []
    for entry in vm_list:
        if not isinstance(entry, dict):
            continue
        vm_id = entry.get("vm_id") or entry.get("vm_name")
        vm_token = entry.get("vm_auth_token")
        if isinstance(vm_id, str) and vm_id and isinstance(vm_token, str) and vm_token:
            vms.append({
                "vm_auth_token": vm_token,
                "vm_id": vm_id,
                "is_default": bool(entry.get("default", False)),
            })
    log.info("VM fetch: %d VMs", len(vms))
    return vms, status


def _post_refresh(
    auth_header: str, device_id: str, root: str, sdk_token: str | None,
) -> dict | None:
    body = {"device_id": device_id}
    if sdk_token:
        body["sdk_token"] = sdk_token
    req = urllib.request.Request(
        api_root(root) + REFRESH_PATH,
        data=json.dumps(body).encode(),
        method="POST",
    )
    req.add_header("Authorization", auth_header)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", user_agent())
    _, data = _request(req)
    if isinstance(data, dict) and isinstance(data.get("payload"), dict):
        data = data["payload"]
    if isinstance(data, dict) and all(isinstance(data.get(k), str) and data[k]
                                      for k in ("access_token", "refresh_token")):
        return {k: data[k] for k in ("access_token", "refresh_token")}
    log.warning("token refresh response missing tokens")
    return None


def refresh_device_token(
    refresh_token: str, device_id: str, root: str = api_root(),
    sdk_token: str | None = None,
) -> tuple[dict | None, int | None]:
    """Rotate the device token pair with the refresh token.

    Returns ``(tokens, status)``: ``tokens`` is None on failure, and
    ``status`` is the HTTP status that ended the attempt, or None for a
    transport failure. A 401 means the pairing is gone and the device must be
    paired again.

    The access token is never presented, as on the ESP32: the server can
    accept it and answer 200 with replacement tokens that every endpoint then
    rejects, which would overwrite working credentials.
    """
    # Apps now hand over refresh tokens that already carry the hatch_refresh:
    # prefix; doubling it makes the server reject it. Same rule as the ESP32.
    raw_refresh = refresh_token.rsplit(":", 1)[-1]
    try:
        return _post_refresh(f"Bearer hatch_refresh:{raw_refresh}", device_id, root, sdk_token), 200
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            log.warning("token refresh rejected: device must be paired again")
        else:
            log.warning("token refresh failed: HTTP %d", exc.code)
        return None, exc.code
    except (http.client.HTTPException, OSError, ValueError, RecursionError, tls.TrustStoreError):
        log.warning("Muse device token refresh failed")
        return None, None

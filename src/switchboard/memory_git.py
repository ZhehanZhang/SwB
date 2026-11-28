"""Back up memory snapshots to an owner-configured Git repository."""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, quote_plus, urlsplit

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_ssh_private_key

from .audit import iso
from .claims import MEMORY_BACKUP_CREDENTIAL, ClaimInputError, normalize
from .connect import AGENT_RE
from .memory import Memory, MemoryError_
from .memory_archive import MAX_ARCHIVE_BYTES, import_manifest, require_owner, snapshot

if TYPE_CHECKING:
    from .auth import Principal
    from .core import Switchboard

PREFIX = "memory.backup."
RETRY_S = (60, 300, 900, 3600)
TIMEOUT_S = 30
SSH_REMOTE = re.compile(r"git@[A-Za-z0-9][A-Za-z0-9.-]*:[A-Za-z0-9_./~+][A-Za-z0-9_./~+-]*")
URL_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s/]*@", re.I)
SCP_USERINFO = re.compile(r"(?<![\w@.])[^\s/@:]+@(?=[A-Za-z0-9.-]+:)")
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,199}")
MANAGED = ("manifest.json", "INDEX.md", "general", "projects", "agents")


class BackupError(MemoryError_):
    pass


def _public_remote(remote: str) -> str:
    return SCP_USERINFO.sub("", URL_USERINFO.sub(r"\1", remote))


def _remote_kind(remote: str) -> str:
    if SSH_REMOTE.fullmatch(remote):
        return "ssh"
    if not (any(c.isspace() or ord(c) < 32 for c in remote) or "\\" in remote):
        try:
            url = urlsplit(remote)
            if (url.scheme == "https" and url.hostname and url.path and url.port != 0
                    and not (url.query or url.fragment or url.username is not None or url.password is not None)):
                return "https"
        except ValueError:
            pass
    raise BackupError("Use an HTTPS or SSH Git remote without credentials.")


def _check_ssh_key(value: str) -> None:
    load = load_ssh_private_key if value.startswith("-----BEGIN OPENSSH PRIVATE KEY-----") else load_pem_private_key
    try:
        load(value.encode(), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ClaimInputError("Use an unencrypted SSH private key.") from None


def _redact(message: str, credential: str | None) -> str:
    if credential:
        forms = {credential, quote(credential, safe=""), quote_plus(credential),
                 base64.b64encode(credential.encode()).decode(),
                 base64.b64encode(f"x-access-token:{credential}".encode()).decode()}
        forms.update(line for line in credential.splitlines() if len(line) > 16)
        for form in sorted(forms, key=len, reverse=True):
            message = re.sub(re.escape(form), "[redacted]", message, flags=re.I)
    return _public_remote(message)


class _Git:
    def __init__(self, env: dict[str, str], credential: str | None):
        self.env = env
        self.credential = credential

    def run(self, cwd: Path, *args: str, allow: tuple[int, ...] = (), read: bool = False) -> subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run(
                ["git", "-c", "credential.helper=", "-c", "core.hooksPath=" + os.devnull,
                 "-c", "http.followRedirects=false", *args], cwd=cwd, env=self.env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if read else subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise BackupError("Git timed out.") from None
        except FileNotFoundError:
            raise BackupError("Install git to use backup.") from None
        except (OSError, ValueError):
            raise BackupError("Could not run git.") from None
        if result.returncode and result.returncode not in allow:
            detail = _redact(result.stderr.decode(errors="replace"), self.credential).strip()
            raise BackupError(detail[-1200:] or "Git failed.")
        return result


def _backup_actor(source: str) -> str:
    return source if AGENT_RE.fullmatch(source) else "SwB"


def _change_line(row: Any) -> str:
    mid, action = row["memory_id"], row["action"]
    scope = row["scope"] or row["current_scope"]
    namespace = row["namespace"] or row["current_namespace"]
    key = row["key"] if row["scope"] else row["current_key"]
    path = "/memories/" + Memory.rel_path(scope, namespace, key, mid).as_posix()
    actor = _backup_actor(row["source"])
    if action.startswith(("move ", "rename")):
        legacy_from, separator, legacy_to = action.removeprefix("move ").partition(" -> ")
        if action.startswith("move ") and separator and not row["scope"]:
            path = "/memories/" + Memory.rel_path(legacy_to, namespace, key, mid).as_posix()
        previous = row["previous_scope"] or (legacy_from if action.startswith("move ") and separator else None)
        if previous:
            old = Memory.rel_path(previous, row["previous_namespace"] or namespace,
                                  row["previous_key"] if row["previous_scope"] else key, mid).as_posix()
            return f"{actor} renamed /memories/{old} to {path}"
    verb = {"create": "created", "delete": "deleted"}.get(action, "updated")
    if action == "sync" and row["previous_rev"] is None:
        verb = "created"
    return f"{actor} {verb} {path}"


def _commit_message(changes: list[Any]) -> str:
    lines = [_change_line(row) for row in changes]
    if not lines:
        return "Back up memory"
    if len(lines) == 1:
        return lines[0]
    names = ", ".join(dict.fromkeys(_backup_actor(row["source"]) for row in changes))
    return f"{len(lines)} memory changes by {names}\n\n" + "\n".join(lines)


class MemoryGit:
    def __init__(self, core: Switchboard):
        self.core = core
        self.root = core.data_dir.resolve() / "memory-git"
        self.defaults = {"remote": "", "branch": "main", "author_name": "SwB", "author_email": "swb@localhost", "enabled": False,
                         "last_attempt_at": None, "last_success_at": None, "last_commit": None, "last_error": None,
                         "credential_claimed_at": None, "next_retry_at": None,
                         "failures": 0, "last_rev": 0, "local_rev": 0}
        self._wake = threading.Condition()
        self._run_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._first = self._last = None
        self._immediate = False
        core.claims.register_name(MEMORY_BACKUP_CREDENTIAL, self._check_claim)
        if (listeners := getattr(core.memory, "listeners", None)) is not None:
            listeners.append(self.changed)

    def _settings(self) -> dict[str, Any]:
        rows = self.core.store.q("SELECT key, value FROM settings WHERE key GLOB ?", (PREFIX + "*",))
        opts = {**self.defaults, **{row["key"].removeprefix(PREFIX): json.loads(row["value"]) for row in rows}}
        opts["branch"] = opts["branch"].removeprefix("refs/heads/")
        return opts

    def _save(self, actor: str, **values: Any) -> None:
        now = self.core.clock()

        def write(db):
            db.executemany(
                "INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                [(PREFIX + key, json.dumps(value), now, actor) for key, value in values.items()]
            )

        self.core.store.tx(write)

    def _check_claim(self, value: str) -> None:
        remote = self._settings()["remote"]
        kind = _remote_kind(remote) if remote else None
        if kind == "ssh":
            _check_ssh_key(value)
        elif kind != "https" or value.startswith("-----BEGIN "):
            raise ClaimInputError("Use an HTTPS access token.")

    def configure(self, principal: Principal, *, remote: str | None = None, branch: str | None = None,
                  author_name: str | None = None, author_email: str | None = None, enabled: bool | None = None,
                  credential: str | None = None, claim: bool = False) -> dict[str, Any]:
        require_owner(principal)
        with self._run_lock:
            before = self._settings()
            remote = before["remote"] if remote is None or remote == _public_remote(before["remote"]) else remote.strip()
            branch = before["branch"] if branch is None else (branch.strip() or "main").removeprefix("refs/heads/")
            author_name = before["author_name"] if author_name is None else author_name.strip() or "SwB"
            author_email = before["author_email"] if author_email is None else author_email.strip() or "swb@localhost"
            enabled = before["enabled"] if enabled is None else enabled
            kind = _remote_kind(remote) if remote else None
            if not kind and enabled:
                raise BackupError("Set a Git remote first.")
            if (branch == "HEAD" or not REF.fullmatch(branch) or ".." in branch or "//" in branch or branch.endswith(("/", ".")) or any(
                p.startswith(".") or p.endswith(".lock") for p in branch.split("/")
            )):
                raise BackupError("Use a valid Git branch name.")
            if any(len(v) > 200 or any(ord(c) < 32 or c in "<>" for c in v) for v in (author_name, author_email)):
                raise BackupError("Use a valid Git author name and email.")
            if credential is not None:
                try:
                    if kind == "ssh":
                        credential = normalize("pem", credential, check=_check_ssh_key)
                    elif kind == "https" and not credential.lstrip().startswith("-----BEGIN "):
                        credential = normalize("token", credential)
                    else:
                        raise ClaimInputError("Set an HTTPS or SSH remote for the credential.")
                except ClaimInputError as e:
                    raise BackupError(str(e)) from None

            state = {"remote": remote, "branch": branch, "author_name": author_name, "author_email": author_email, "enabled": enabled}
            if remote != before["remote"] or credential is not None:
                self.core.store.x("DELETE FROM claim_links WHERE name = ?", (MEMORY_BACKUP_CREDENTIAL,))
                self.core.claims.save_secret(MEMORY_BACKUP_CREDENTIAL, credential)
            if remote != before["remote"] or branch != before["branch"]:
                state.update(last_rev=0, local_rev=0, last_attempt_at=None, last_success_at=None, last_commit=None, last_error=None)
            state.update(next_retry_at=None, failures=0)
            self._save(principal.source, **state)
            out = self.status()
            if claim and kind in ("https", "ssh"):
                out["link"] = self.core.claims.mint(MEMORY_BACKUP_CREDENTIAL, self.core.base_url,
                                                    kind="pem" if kind == "ssh" else "token",
                                                    label="Git SSH private key" if kind == "ssh" else "Git access token")
                out["link_expires_at"] = iso(self.core.claims.inspect_details(out["link"].rsplit("/", 1)[-1])["expires_at"])
        self.changed()
        self.core.audit_memory(principal, "memory_backup_configure", "git")
        return out

    def status(self) -> dict[str, Any]:
        opts = self._settings()
        pending = self.core.store.one("SELECT COUNT(*) AS n FROM memory_revisions WHERE rowid > ?", (opts["last_rev"],))["n"]
        available = shutil.which("git") is not None
        credential_saved = self.core.store.one("SELECT 1 FROM secrets WHERE name = ? AND sealed IS NOT NULL",
                                               (MEMORY_BACKUP_CREDENTIAL,)) is not None
        return {"enabled": opts["enabled"], "remote": _public_remote(opts["remote"]), "branch": opts["branch"],
                "author_name": opts["author_name"], "author_email": opts["author_email"], "last_success_at": iso(opts["last_success_at"]),
                "last_attempt_at": iso(opts["last_attempt_at"]), "last_commit": opts["last_commit"],
                "last_error": (opts["last_error"] if available else "Install git to use backup."),
                "pending_changes": pending, "next_retry_at": iso(opts["next_retry_at"]), "git_available": available,
                "credential_saved": credential_saved, "credential_claimed_at": opts["credential_claimed_at"]}

    def changed(self) -> None:
        with self._wake:
            self._last = time.monotonic()
            if self._first is None:
                self._first = self._last
            self._wake.notify()

    def now(self, principal: Principal) -> dict[str, Any]:
        require_owner(principal)
        if not self._settings()["enabled"]:
            raise BackupError("Enable Git backup first.")
        self._schedule_now(principal.source)
        self.core.audit_memory(principal, "memory_backup_now", "git")
        return self.status()

    def credential_saved(self) -> None:
        self._schedule_now("memory-git", credential_claimed_at=self.core.clock())

    def _schedule_now(self, actor: str, **fields: Any) -> None:
        with self._wake:
            self._immediate = True
            self._save(actor, next_retry_at=None, failures=0, **fields)
            self._wake.notify()

    def start(self) -> None:
        if self._thread is None:
            self.changed()
            self._thread = threading.Thread(target=self._loop, name="memory-git", daemon=True)
            self._thread.start()

    def close(self) -> None:
        if self._thread is not None:
            with self._wake:
                self._stopping = True
                self._wake.notify()
            self._thread.join()

    def _loop(self) -> None:
        while True:
            with self._wake:
                if self._stopping:
                    return
                opts = self._settings()
                delay = None
                if opts["enabled"]:
                    if self._immediate:
                        delay = 0
                    elif opts["next_retry_at"] is not None:
                        delay = opts["next_retry_at"] - self.core.clock()
                    elif self._first is not None:
                        delay = min(self._first + self.core.cfg.backup_max_delay_s,
                                    self._last + self.core.cfg.backup_debounce_s) - time.monotonic()
                if delay is None or delay > 0:
                    self._wake.wait(delay)
                    continue
                self._immediate = False
                self._first = self._last = None
            self.run_once()

    @contextmanager
    def _git(self, opts: dict[str, Any], credential: str | None):
        kind = _remote_kind(opts["remote"])
        with TemporaryDirectory(prefix="memory-git-auth-", dir=self.root.parent) as temp:
            env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")
                   and key not in ("SSH_ASKPASS", "SWB_MEMORY_GIT_TOKEN")}
            env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
                       GIT_ALLOW_PROTOCOL="https:ssh", LC_ALL="C", GIT_AUTHOR_NAME=opts["author_name"],
                       GIT_AUTHOR_EMAIL=opts["author_email"], GIT_COMMITTER_NAME=opts["author_name"],
                       GIT_COMMITTER_EMAIL=opts["author_email"])
            if kind == "https" and credential:
                helper = Path(temp) / "askpass"
                helper.write_text('#!/bin/sh\ncase "$1" in\nUsername*) printf "%s\\n" x-access-token ;;\n'
                                  'Password*) printf "%s\\n" "$SWB_MEMORY_GIT_TOKEN" ;;\n*) exit 1 ;;\nesac\n')
                helper.chmod(0o700)
                env.update(GIT_ASKPASS=str(helper), SWB_MEMORY_GIT_TOKEN=credential)
            if kind == "ssh":
                args = ["ssh", "-F", os.devnull, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
                        "-o", f"UserKnownHostsFile={self.root.parent / 'memory-git-known_hosts'}", "-o", "StrictHostKeyChecking=accept-new"]
                if credential:
                    key = Path(temp) / "identity"
                    fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w") as stream:
                        stream.write(credential)
                    args += ["-i", str(key)]
                else:
                    args += ["-o", "IdentityFile=none"]
                env["GIT_SSH_COMMAND"] = shlex.join(args)
            yield _Git(env, credential)

    def _prepare(self, git: _Git, remote: str, branch_ref: str) -> bool:
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink():
            raise BackupError("Use a local directory for the Git worktree.")
        git_dir = self.root / ".git"
        if git_dir.is_symlink() or (git_dir.exists() and not git_dir.is_dir()):
            raise BackupError("Use a local directory for the Git worktree.")
        if not git_dir.is_dir():
            git.run(self.root, "init", "-q", "--template=")
        marker = git_dir / "swb-target"
        target = json.dumps([remote, branch_ref])
        if marker.is_file() and marker.read_text() == target:
            return False
        found = git.run(self.root, "ls-remote", "--exit-code", "--heads", "--", remote, branch_ref, allow=(2,))
        git.run(self.root, "update-ref", "-d", "HEAD")
        git.run(self.root, "read-tree", "--empty")
        if found.returncode != 2:
            git.run(self.root, "fetch", "--no-tags", "--", remote, branch_ref)
            git.run(self.root, "update-ref", "HEAD", "FETCH_HEAD")
            git.run(self.root, "read-tree", "HEAD")
        marker.write_text(target)
        return True

    def _tree(self, git: _Git, files: dict[str, str]) -> None:
        index = git.run(self.root, "ls-files", "-z", "--", *MANAGED, read=True).stdout.split(b"\x00")
        roots = {os.fsdecode(path.split(b"/", 1)[0]) for path in index if path}
        for name in MANAGED:
            path = self.root / name
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        for rel, text in files.items():
            path = self.root / rel
            if rel.partition("/")[0] not in MANAGED or any(part in ("", ".", "..") for part in rel.split("/")):
                raise BackupError("Use a safe memory backup path.")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            roots.add(rel.partition("/")[0])
        git.run(self.root, "add", "-A", "--", *sorted(roots))

    def _backup(self, git: _Git, opts: dict[str, Any]) -> None:
        branch = "refs/heads/" + opts["branch"]
        fresh = self._prepare(git, opts["remote"], branch)
        snap = snapshot(self.core, 0 if fresh else opts["local_rev"])
        existing = self.root / "manifest.json"
        if existing.is_file() and not existing.is_symlink():
            try:
                previous = json.loads(existing.read_bytes())
            except (ValueError, RecursionError):
                previous = None
            if isinstance(previous, dict) and previous.get("entries") == snap.manifest["entries"]:
                snap.manifest["exported_at"] = previous.get("exported_at")
        self._tree(git, snap.files())
        if git.run(self.root, "diff", "--cached", "--quiet", allow=(1,)).returncode:
            git.run(self.root, "commit", "-q", "-m", _commit_message(snap.changes))
        head = git.run(self.root, "rev-parse", "HEAD", read=True).stdout.decode().strip()
        self._save("memory-git", local_rev=snap.last_rev)
        git.run(self.root, "push", "--", opts["remote"], f"HEAD:{branch}")
        completed_at = self.core.clock()
        self._save("memory-git", last_rev=snap.last_rev, last_commit=head, last_attempt_at=completed_at, last_success_at=completed_at,
                   last_error=None, failures=0, next_retry_at=None)

    def run_once(self) -> dict[str, Any]:
        with self._run_lock:
            opts = self._settings()
            if not opts["enabled"]:
                return self.status()
            credential = None
            try:
                credential = self.core.claims.secret(MEMORY_BACKUP_CREDENTIAL)
                with self._git(opts, credential) as git:
                    self._backup(git, opts)
            except (BackupError, InvalidToken, OSError, UnicodeError) as e:
                error = _redact(str(e), credential) if isinstance(e, BackupError) else "Could not write the Git backup."
                if isinstance(e, InvalidToken):
                    error = "Save the Git backup credential again."
                failures = opts["failures"] + 1
                failed_at = self.core.clock()
                self._save("memory-git", last_attempt_at=failed_at, last_error=error, failures=failures,
                           next_retry_at=failed_at + RETRY_S[min(failures - 1, len(RETRY_S) - 1)])
                self.core.audit.record(origin="system", source="memory-git", client="-", destination="memory", target="git",
                                       action="memory_backup_failed", outcome="error", detail=error)
            return self.status()

    def restore(self, principal: Principal, ref: str | None = None, *, trust_owner: bool = False,
                overwrite: bool = False) -> dict[str, Any]:
        require_owner(principal)
        if ref is not None and not REF.fullmatch(ref):
            raise BackupError("Use a Git commit or branch name.")
        with self._run_lock:
            opts = self._settings()
            if not opts["remote"]:
                raise BackupError("Set a Git remote first.")
            try:
                credential = self.core.claims.secret(MEMORY_BACKUP_CREDENTIAL)
                with self._git(opts, credential) as git, TemporaryDirectory(prefix="memory-git-restore-", dir=self.root.parent) as temp:
                    root = Path(temp)
                    git.run(root, "init", "-q", "--template=")
                    git.run(root, "fetch", "--no-tags", "--", opts["remote"], "refs/heads/" + opts["branch"])
                    branch_ref = "refs/heads/" + opts["branch"]
                    commit = "FETCH_HEAD" if ref in (None, "HEAD", opts["branch"], branch_ref) else ref
                    commit = git.run(root, "rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}", read=True).stdout.decode().strip()
                    author = git.run(root, "show", "-s", "--format=%an%x00%ae", commit, read=True).stdout.rstrip(b"\n")
                    expected_author = f"{opts['author_name']}\0{opts['author_email']}".encode()
                    obj = f"{commit}:manifest.json"
                    size = int(git.run(root, "cat-file", "-s", obj, read=True).stdout)
                    if size > MAX_ARCHIVE_BYTES:
                        raise BackupError("Use a memory archive up to 50 MB.")
                    data = git.run(root, "cat-file", "blob", obj, read=True).stdout
            except OSError:
                raise BackupError("Could not read the Git backup.") from None
            except InvalidToken:
                raise BackupError("Save the Git backup credential again.") from None
        out = import_manifest(self.core, principal, data, trust_owner=trust_owner, overwrite=overwrite,
                              origin=f"git {_public_remote(opts['remote'])}@{commit}", external=author != expected_author)
        self.core.audit_memory(principal, "memory_backup_restore", "git")
        return out

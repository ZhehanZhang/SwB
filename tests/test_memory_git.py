import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from conftest import OWNER, PUBLIC, STATIC_KEY, base_config, call, eventually, new_core, payload, principal
from switchboard.claims import MEMORY_BACKUP_CREDENTIAL
from switchboard.memory_git import BackupError

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required")


def git(repo, *args):
    return subprocess.check_output([GIT, "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "remote repo.git"
    repo.mkdir()
    git(repo, "init", "--bare")
    return repo


def write(core, actor="claude", key="one", body="One"):
    return core.memory.write(source=actor, scope="agent:" + actor, namespace="notes", title=key, key=key, body=body)["id"]


async def test_git_backup_restore_and_no_empty_commit(make, repo, tmp_path, capy):
    core, _ = make()
    owner = principal("owner")
    worker = core.memory_git
    worker.configure(owner, remote=repo.as_uri(), branch="backup/memory", author_name="Backup Owner",
                     author_email="backup@example.test", enabled=True)
    first = write(core)
    write(core, actor="chatgpt", key="two", body="Two")
    assert worker.status()["pending_changes"] == 2
    status = worker.run_once()
    branch = "refs/heads/backup/memory"
    assert status["last_error"] is None and status["last_success_at"]
    commit = git(repo, "rev-parse", branch)
    assert status["last_commit"] == commit and status["pending_changes"] == 0
    assert git(repo, "show", "-s", "--format=%an <%ae> %cn <%ce>", branch) == (
        "Backup Owner <backup@example.test> Backup Owner <backup@example.test>"
    )
    assert git(repo, "show", "-s", "--format=%B", branch).splitlines() == [
        "2 memory changes by claude, chatgpt", "",
        "claude created /memories/agents/claude/notes/one.md", "chatgpt created /memories/agents/chatgpt/notes/two.md",
    ]
    assert git(repo, "ls-tree", "-r", "--name-only", branch).splitlines() == [
        "INDEX.md", "agents/chatgpt/notes/two.md", "agents/claude/notes/one.md", "manifest.json",
    ]
    manifest = json.loads(git(repo, "show", f"{branch}:manifest.json"))
    assert {e["body"] for e in manifest["entries"]} == {"One", "Two"}
    assert worker.run_once()["last_commit"] == commit
    assert git(repo, "rev-list", "--count", branch) == "1"

    write(core, body="Changed")
    updated = worker.run_once()
    assert updated["last_commit"] != commit
    assert git(repo, "show", "-s", "--format=%s", branch) == "claude updated /memories/agents/claude/notes/one.md"
    core.memory.delete(source="claude", id=first)
    assert worker.run_once()["last_error"] is None
    assert git(repo, "show", "-s", "--format=%s", branch) == "claude deleted /memories/agents/claude/notes/one.md"
    assert "agents/claude/notes/one.md" not in git(repo, "ls-tree", "-r", "--name-only", branch)

    dest = new_core(tmp_path, capy, data=tmp_path / "restored")
    try:
        dest.memory_git.configure(owner, remote=repo.as_uri(), branch="backup/memory")
        assert dest.memory_git.restore(owner, commit)["created"] == 2
        assert dest.memory_git.restore(owner)["unchanged"] == 1
        assert dest.memory.read(id=first)["body"] == "One"
    finally:
        await dest.aclose()


async def test_push_failure_retries_and_now_resets_backoff(make, repo):
    core, _ = make()
    worker = core.memory_git
    owner = principal("owner")
    worker.configure(owner, remote=repo.as_uri(), enabled=True)
    write(core)
    assert worker.run_once()["last_error"] is None
    missing = repo.with_name("offline.git")
    repo.rename(missing)
    write(core, body="Next")
    failed = worker.run_once()
    assert failed["last_error"] and failed["next_retry_at"] and failed["pending_changes"] == 1
    assert worker._settings()["next_retry_at"] == core.clock() + 60
    assert worker.run_once()["last_error"]
    assert worker._settings()["next_retry_at"] == core.clock() + 300
    assert core.store.one("SELECT 1 FROM audit WHERE action = 'memory_backup_failed'")

    missing.rename(repo)
    worker.now(owner)
    await eventually(lambda: worker.status()["pending_changes"] == 0)
    status = worker.status()
    assert status["next_retry_at"] is None and status["last_error"] is None
    assert git(repo, "rev-list", "--count", "main") == "2"


async def test_background_worker_starts_from_app_and_debounces(make, repo):
    core, _ = make(config=base_config(memory={"backup": {"debounce_s": 0.05, "max_delay_s": 0.25}}))
    worker = core.memory_git
    worker.configure(principal(), remote=repo.as_uri(), enabled=True)
    write(core)
    write(core, actor="chatgpt", key="two", body="Two")
    await eventually(lambda: worker.status()["last_commit"] is not None)
    assert git(repo, "rev-list", "--count", "main") == "1"


def test_https_askpass_keeps_token_from_argv_status_audit_and_logs(make, tmp_path, monkeypatch, caplog):
    core, client = make()
    worker = core.memory_git
    token = "fixture-git-token-with/a+delimiter"
    fake = tmp_path / "git"
    fake.write_text(
        '#!/bin/sh\n'
        'for arg in "$@"; do\n'
        '    case "$arg" in *"$SWB_MEMORY_GIT_TOKEN"*) exit 91 ;; esac\n'
        '    if [ "$arg" = ls-remote ]; then\n'
        '        test "$GIT_TERMINAL_PROMPT" = 0 && test "$("$GIT_ASKPASS" "Password for host:")" = "$SWB_MEMORY_GIT_TOKEN" || exit 92\n'
        '        printf "fixture error %s https://fixture:private-userinfo@example.test/repo\\n" "$SWB_MEMORY_GIT_TOKEN" >&2\n'
        '        exit 128\n'
        '    fi\n'
        'done\n'
        f'exec "{GIT}" "$@"\n'
    )
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    claim = payload(call(client, STATIC_KEY, "memory_backup_configure", {"remote": "https://example.test/repo", "enabled": True}))["link"]
    code = claim.rsplit("/", 1)[-1]
    assert core.claims.inspect_details(code)["kind"] == "token"
    core.claims.consume(code, token)
    assert core.claims.secret(MEMORY_BACKUP_CREDENTIAL) == token
    assert worker._settings()["remote"] == "https://example.test/repo"
    status = worker.run_once()
    assert status["last_error"] == "fixture error [redacted] https://example.test/repo"
    report = json.dumps(status) + caplog.text + str([dict(r) for r in core.store.q("SELECT * FROM audit")])
    assert token not in report and "private-userinfo" not in report
    assert not list(core.data_dir.glob("memory-git-auth-*"))
    with pytest.raises(BackupError, match="without credentials"):
        worker.configure(principal(), remote="https://user:private-userinfo@example.test/repo")
    worker.configure(principal(), remote="https://another.example.test/repo")
    assert core.claims.secret(MEMORY_BACKUP_CREDENTIAL) is None


def test_ssh_credential_is_sealed_and_used_from_a_private_file(make):
    core, _ = make()
    worker = core.memory_git
    owner = principal()
    secret = Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()).decode()
    configured = worker.configure(owner, remote="git@example.test:archive.git", claim=True)
    assert configured["remote"] == "example.test:archive.git"
    code = configured["link"].rsplit("/", 1)[-1]
    assert core.claims.inspect_details(code)["kind"] == "pem"
    core.claims.consume(code, secret)
    credential = core.claims.secret(MEMORY_BACKUP_CREDENTIAL)
    worker.configure(owner, remote=configured["remote"], enabled=False)
    assert core.claims.secret(MEMORY_BACKUP_CREDENTIAL) == credential
    with worker._git(worker._settings(), credential) as git_client:
        argv = shlex.split(git_client.env["GIT_SSH_COMMAND"])
        key = Path(argv[argv.index("-i") + 1])
        assert key.stat().st_mode & 0o777 == 0o600
        assert key.read_text() == credential
        assert credential not in git_client.env.values()
        assert "IdentitiesOnly=yes" in argv and "StrictHostKeyChecking=accept-new" in argv
    assert not key.exists()


def test_backup_console_routes_use_owner_csrf_and_never_return_creds(make):
    core, client = make()
    csrf = client.get("/auth/session", headers=OWNER).json()["csrf"]
    headers = {**OWNER, "Origin": PUBLIC, "X-CSRF-Token": csrf}
    assert client.get("/api/memory/backup").status_code == 401
    assert client.get("/api/memory/backup", headers=OWNER).json()["credential_saved"] is False
    for path in ("/api/memory/backup", "/api/memory/backup/now", "/api/memory/backup/restore"):
        assert client.post(path, headers={**OWNER, "Origin": PUBLIC}, json={}).status_code == 403
    token = "fixture-only-token"
    configured = client.post("/api/memory/backup", headers=headers, json={"remote": "https://example.test/repo", "credential": token})
    assert configured.status_code == 200 and token not in configured.text
    assert configured.json()["credential_saved"] is True
    assert core.claims.secret(MEMORY_BACKUP_CREDENTIAL) == token
    assert client.get("/api/memory/backup", headers=OWNER).json() == configured.json()
    assert client.post("/api/memory/backup/now", headers=headers).json() == {"error": "Enable Git backup first."}

import os
import stat

import pytest

from switchboard.core import Switchboard


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
@pytest.mark.parametrize("existing", [False, True])
async def test_data_and_sqlite_files_are_private(tmp_path, existing):
    config = tmp_path / "swb.yaml"
    config.write_text("version: 2\nblocks:\n  slack: {use: slack}\n")
    data = tmp_path / "data"
    paths = [data / f"{db}{suffix}" for db in ("switchboard.db", "slack.db") for suffix in ("", "-wal", "-shm")]
    original = None
    if existing:
        original = Switchboard(config, data_dir=data, env={})
        original.store.x("INSERT INTO projects (slug, name, created_at, updated_at) VALUES ('saved', 'Saved project', 1, 1)")
        data.chmod(0o755)
        for path in paths:
            path.chmod(0o644)
    try:
        core = Switchboard(config, data_dir=data, env={})
        try:
            assert stat.S_IMODE(data.stat().st_mode) == 0o700
            for path in paths:
                assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
            if existing:
                assert core.store.one("SELECT name FROM projects WHERE slug = 'saved'")["name"] == "Saved project"
        finally:
            await core.aclose()
    finally:
        if original is not None:
            await original.aclose()

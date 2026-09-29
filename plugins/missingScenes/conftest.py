"""pytest setup for missingScenes."""

import time

import pytest

import missing_scenes


@pytest.fixture(autouse=True)
def isolated_disk_cache(tmp_path, monkeypatch):
    """Give each test its own stash_id disk cache, outside the plugin folder."""
    monkeypatch.setattr(missing_scenes, "CACHE_DIR", str(tmp_path))
    missing_scenes._local_stash_id_cache.clear()
    missing_scenes._cache_metadata.clear()


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Offline tests never wait: the page delay and retry pauses become no-ops.
    Tests that check the waits patch time.sleep again with a recorder."""
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

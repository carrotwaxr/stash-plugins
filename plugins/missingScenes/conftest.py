"""pytest setup for missingScenes."""

import pytest

import missing_scenes


@pytest.fixture(autouse=True)
def isolated_disk_cache(tmp_path, monkeypatch):
    """Give each test its own stash_id disk cache, outside the plugin folder."""
    monkeypatch.setattr(missing_scenes, "CACHE_DIR", str(tmp_path))
    missing_scenes._local_stash_id_cache.clear()
    missing_scenes._cache_metadata.clear()

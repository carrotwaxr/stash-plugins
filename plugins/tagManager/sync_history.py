"""
Scene Tag Sync history.

Scene sync only adds tags, and a stash-box keeps returning the tags it has, so a
sync with no memory adds back every tag the user removed from a scene. This
history remembers, per stash-box endpoint and local scene, which stash-box tags
matched a local tag at the scene's last live sync. The next sync doesn't add
those again, so a removed tag stays removed. Tags the stash-box gains later, and
tags that only now match a local tag (or are no longer blacklisted), are still
added.

A scene re-linked to a different stash-box scene starts over. The history is a
SQLite file (tagManager keeps it in Stash's config dir, see plugin_data), sized
for tens of thousands of scenes. Every record is committed as it is made, because
tags are written to Stash immediately: a sync that is cancelled or killed must not
lose history for scenes it already changed. The "Reset Scene Tag Sync History" task
empties it.
"""

import sqlite3
import time

SCHEMA_VERSION = 1  # PRAGMA user_version, so a later schema change can tell


class SyncHistory:
    """The stash-box tag ids each local scene was last synced with, per endpoint."""

    def __init__(self, path):
        self._db = sqlite3.connect(path, timeout=30)
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")  # safe with WAL; a commit per record stays cheap
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS scene_tags ("
                "endpoint TEXT, scene_id TEXT, remote_id TEXT, tag_ids TEXT, synced_at INTEGER, "
                "PRIMARY KEY(endpoint, scene_id))"
            )
            if self._db.execute("PRAGMA user_version").fetchone()[0] == 0:
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._db.commit()
        except sqlite3.Error:
            self._db.close()
            raise

    def get(self, endpoint, scene_id, remote_id):
        """
        The stash-box tag ids recorded for a scene, as a set.

        None if the scene has no record for this endpoint, or its record is for a
        different stash-box scene (it has been re-linked since).
        """
        row = self._db.execute(
            "SELECT remote_id, tag_ids FROM scene_tags WHERE endpoint = ? AND scene_id = ?",
            (endpoint, str(scene_id)),
        ).fetchone()
        if row is None or row[0] != str(remote_id):
            return None
        return set((row[1] or "").split())

    def record(self, endpoint, scene_id, remote_id, stashdb_tag_ids):
        """Replace a scene's record and commit it, so a killed sync keeps its history."""
        tag_ids = " ".join(sorted({str(t) for t in stashdb_tag_ids}))
        self._db.execute(
            "INSERT OR REPLACE INTO scene_tags (endpoint, scene_id, remote_id, tag_ids, synced_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (endpoint, str(scene_id), str(remote_id), tag_ids, int(time.time())),
        )
        self._db.commit()

    def reset(self):
        """Forget every scene. Returns how many records were removed."""
        cleared = self._db.execute("DELETE FROM scene_tags").rowcount
        self._db.commit()
        return cleared

    def close(self):
        """Commit and close. Calling it again does nothing."""
        if self._db is None:
            return
        try:
            self._db.commit()
        finally:
            self._db.close()
            self._db = None

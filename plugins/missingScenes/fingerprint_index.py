"""Fingerprint index: the stash-box scenes your scenes match by file fingerprint.

For one stash-box endpoint, the index holds every local scene that has no stash_id for
that endpoint (untagged, or tagged only on another box): the scene's updated_at and
fingerprints when it was last looked up, and the endpoint's scene ids its files match
(findScenesBySceneFingerprints with phash, oshash and md5). The missing and browse
views count those stash-box scenes as owned.

One SQLite file per endpoint in the plugin data dir. Every call opens its own
connection and closes it; each batch is written in one transaction, so a failed
lookup keeps what was stored. A file that isn't a usable index (corrupt, or another
schema version) is moved aside to <file>.corrupt and a new one is built.

Standard library only.
"""

import contextlib
import hashlib
import os
import sqlite3
import time

import log
import stashbox_api

SCHEMA_VERSION = "1"
BATCH_SIZE = stashbox_api.FINGERPRINT_BATCH_SIZE

# Stash's fingerprint types and the stash-box FingerprintAlgorithm for each
ALGORITHMS = {"md5": "MD5", "oshash": "OSHASH", "phash": "PHASH"}

# A scene whose last lookup found nothing is looked up again after this long even if
# unchanged, so matches the stash-box gained since are picked up
RECHECK_UNMATCHED_DAYS = 30

# A match whose stash-box duration is further than this from every local file's duration
# is another scene (a trailer, a compilation), so it isn't counted
DURATION_TOLERANCE_SECONDS = 60
DURATION_TOLERANCE_RATIO = 0.1

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    # One row per local scene looked up. fingerprints is a digest of what was looked up,
    # because Stash's Generate task adds a phash without touching the scene's updated_at.
    """CREATE TABLE IF NOT EXISTS scenes (
        scene_id     TEXT PRIMARY KEY,
        updated_at   TEXT NOT NULL,
        fingerprints TEXT NOT NULL,
        checked_at   REAL NOT NULL
    )""",
    # The endpoint's scene ids each local scene matches
    """CREATE TABLE IF NOT EXISTS matches (
        scene_id TEXT NOT NULL,
        stash_id TEXT NOT NULL,
        PRIMARY KEY (scene_id, stash_id)
    )""",
)


def index_path(data_dir, endpoint):
    """The index file for an endpoint."""
    key = hashlib.sha256(str(endpoint).encode("utf-8")).hexdigest()[:12]
    return os.path.join(data_dir, f"fingerprints_{key}.sqlite3")


# ---- the file ------------------------------------------------------------------

class NotAnIndex(sqlite3.DatabaseError):
    """The file is a database, but not an index this version can use."""


def _is_unusable(error):
    """True when the file itself is bad. OperationalError (locked, can't open) leaves it alone."""
    return isinstance(error, sqlite3.DatabaseError) and not isinstance(error, sqlite3.OperationalError)


def _move_aside(path, error):
    """Move an unusable index (and any journal) to <path>.corrupt so a new one can start."""
    log.LogWarning(f"The fingerprint index {os.path.basename(path)} is unusable ({error}); "
                   f"moved it to {os.path.basename(path)}.corrupt and starting a new one")
    for suffix in ("", "-journal", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.replace(path + suffix, path + ".corrupt" + suffix)


@contextlib.contextmanager
def _transaction(conn):
    """BEGIN IMMEDIATE ... COMMIT, rolled back if the block raises."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _prepare(conn):
    """Create the tables, and check the file's schema version."""
    with _transaction(conn):
        for statement in _SCHEMA:
            conn.execute(statement)
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        elif row[0] != SCHEMA_VERSION:
            raise NotAnIndex(f"schema version {row[0]}, expected {SCHEMA_VERSION}")


def _connect(path, create):
    """A connection to a usable index at path (the caller closes it).

    Returns None when there is no file and create is False. An unusable file is moved
    aside: with create a new index replaces it, without it the result is None.
    """
    for attempt in range(2):
        if not create and not os.path.exists(path):
            return None
        # Autocommit mode: _transaction manages every transaction explicitly
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        try:
            _prepare(conn)
            return conn
        except sqlite3.Error as e:
            conn.close()
            if not _is_unusable(e) or attempt:
                raise
            _move_aside(path, e)
    return None


@contextlib.contextmanager
def _open(path, create):
    """_connect as a context manager that always closes the connection."""
    conn = _connect(path, create)
    try:
        yield conn
    finally:
        if conn is not None:
            conn.close()


def _set_meta(conn, **values):
    conn.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     [(k, str(v)) for k, v in values.items()])


# ---- reading ------------------------------------------------------------------

def read_index(data_dir, endpoint):
    """The index for an endpoint, or None when it has never been built or is unusable.

    Never raises: the views work without it. An unusable file is moved aside.

    Returns:
        {"stash_ids": set of matched stash-box scene ids, "complete": whether the last
        build finished, "last_run_at": unix time, "scenes": local scenes indexed,
        "matched": local scenes with a match}
    """
    path = index_path(data_dir, endpoint)
    try:
        with _open(path, create=False) as conn:
            if conn is None:
                return None
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            if "last_run_at" not in meta:
                return None
            index = {
                "stash_ids": {row[0] for row in conn.execute("SELECT DISTINCT stash_id FROM matches")},
                "complete": meta.get("complete") == "1",
                "last_run_at": float(meta["last_run_at"]),
                "scenes": conn.execute("SELECT COUNT(*) FROM scenes").fetchone()[0],
                "matched": conn.execute("SELECT COUNT(DISTINCT scene_id) FROM matches").fetchone()[0],
            }
    except (sqlite3.Error, OSError, ValueError) as e:
        if isinstance(e, sqlite3.Error) and _is_unusable(e):
            try:
                _move_aside(path, e)
            except OSError as move_error:
                log.LogWarning(f"Could not move the unusable fingerprint index aside: {move_error}")
        else:
            log.LogWarning(f"Could not read the fingerprint index ({e}); not using it")
        return None
    return index


# ---- building -----------------------------------------------------------------

def scene_fingerprints(scene):
    """The stash-box fingerprint inputs for a local scene's files, deduplicated and sorted."""
    found = set()
    for file in scene.get("files") or []:
        if not isinstance(file, dict):
            continue
        for fp in file.get("fingerprints") or []:
            if not isinstance(fp, dict):
                continue
            algorithm = ALGORITHMS.get(str(fp.get("type") or "").lower())
            value = str(fp.get("value") or "").strip()
            if algorithm and value:
                found.add((algorithm, value))
    return [{"hash": value, "algorithm": algorithm} for algorithm, value in sorted(found)]


def _durations(scene):
    out = []
    for file in scene.get("files") or []:
        try:
            duration = float((file or {}).get("duration") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if duration > 0:
            out.append(duration)
    return out


def _digest(fingerprints):
    text = "\n".join(f"{fp['algorithm']}:{fp['hash']}" for fp in fingerprints)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def duration_matches(local_durations, remote_duration):
    """False only when both durations are known and the stash-box's is far from every file's."""
    try:
        remote = float(remote_duration)
    except (TypeError, ValueError):
        return True
    if remote <= 0 or not local_durations:
        return True
    tolerance = max(DURATION_TOLERANCE_SECONDS, DURATION_TOLERANCE_RATIO * remote)
    return any(abs(local - remote) <= tolerance for local in local_durations)


def _failure_message(error, box_name, queried, total):
    message = f"{box_name} fingerprint lookup failed after {queried} of {total} scenes: {error}."
    if error.is_auth_error:
        message += (f" Check the {box_name} API key in Settings > Metadata Providers > "
                    f"Stash-box Endpoints.")
    elif error.is_rate_limited:
        message += " Try again later, or raise the Request Delay plugin setting."
    return message + " The scenes looked up so far are kept; run the build again to continue."


def build(data_dir, endpoint, scenes, lookup, request_delay=0.0, box_name="The stash-box",
          progress=None):
    """Bring an endpoint's index up to date with the local scenes that have no stash_id for it.

    Args:
        data_dir: the plugin data dir
        endpoint: the stash-box GraphQL URL
        scenes: every local scene with no stash_id for endpoint, as Stash's findScenes
            returns it: {"id", "updated_at", "files": [{"duration", "fingerprints": [{"type", "value"}]}]}
        lookup: lookup(fingerprint_batches) -> one list of {"id", "duration"} per batch, in
            order (stashbox_api.find_scenes_by_fingerprints); raises StashBoxAPIError
        request_delay: seconds slept between lookups
        box_name: the stash-box's name, for messages
        progress: optional progress(fraction of the lookups done)

    A scene looked up before with the same updated_at and fingerprints is skipped. Scenes
    no longer listed (tagged since, or deleted) leave the index. Lookups go 40 scenes at a
    time and each batch is stored in its own transaction. A failed lookup stops the build
    and keeps what was stored.

    Returns:
        {scanned, skipped, no_fingerprints, queried, remaining, matched, owned, partial,
        complete}; after a failed lookup also error, auth_error, rate_limited and, when
        known, retry_after. partial is True when this run stored lookups before failing.
        matched counts local scenes that match a stash-box scene, owned the stash-box
        scenes they match (both across the whole index).

    Raises:
        sqlite3.Error or OSError when the index can't be written.
    """
    path = index_path(data_dir, endpoint)
    try:
        return _build(path, endpoint, scenes, lookup, request_delay, box_name, progress)
    except sqlite3.Error as e:
        # Found corrupt part-way through: start again with a new file
        if not _is_unusable(e):
            raise
        _move_aside(path, e)
        return _build(path, endpoint, scenes, lookup, request_delay, box_name, progress)


def _build(path, endpoint, scenes, lookup, request_delay, box_name, progress):
    listed = {}
    for scene in scenes:
        scene_id = str(scene.get("id") or "") if isinstance(scene, dict) else ""
        if scene_id:
            fingerprints = scene_fingerprints(scene)
            listed[scene_id] = (str(scene.get("updated_at") or ""), fingerprints,
                                _digest(fingerprints), _durations(scene))

    with _open(path, create=True) as conn:
        stored_rows = {row[0]: row[1:] for row in
                       conn.execute("SELECT scene_id, updated_at, fingerprints, checked_at FROM scenes")}
        stored = {scene_id: row[:2] for scene_id, row in stored_rows.items()}
        matched_ids = {row[0] for row in conn.execute("SELECT DISTINCT scene_id FROM matches")}
        recheck_before = time.time() - RECHECK_UNMATCHED_DAYS * 86400

        with _transaction(conn):
            # Tagged for this endpoint since, or deleted: their stash_ids speak for them now
            gone = [(scene_id,) for scene_id in stored if scene_id not in listed]
            conn.executemany("DELETE FROM matches WHERE scene_id = ?", gone)
            conn.executemany("DELETE FROM scenes WHERE scene_id = ?", gone)
            # Not complete until this run finishes
            _set_meta(conn, endpoint=endpoint, complete=0)

        skipped = 0
        no_fingerprints = []
        to_query = []
        for scene_id, (updated_at, fingerprints, digest, durations) in listed.items():
            if not fingerprints:
                no_fingerprints.append((scene_id, updated_at, digest))
            elif stored.get(scene_id) == (updated_at, digest) and (
                    scene_id in matched_ids or stored_rows[scene_id][2] >= recheck_before):
                skipped += 1
            else:
                to_query.append((scene_id, updated_at, digest, fingerprints, durations))

        # Nothing to look up for these, but a row keeps an old match from outliving its fingerprints
        now = time.time()
        with _transaction(conn):
            for scene_id, updated_at, digest in no_fingerprints:
                conn.execute("DELETE FROM matches WHERE scene_id = ?", (scene_id,))
                conn.execute("INSERT OR REPLACE INTO scenes VALUES (?, ?, ?, ?)",
                             (scene_id, updated_at, digest, now))

        total = len(to_query)
        queried = 0
        failure = None
        for start in range(0, total, BATCH_SIZE):
            batch = to_query[start:start + BATCH_SIZE]
            if start and request_delay > 0:
                time.sleep(request_delay)
            try:
                results = lookup([item[3] for item in batch])
                if len(results) != len(batch):
                    raise stashbox_api.StashBoxAPIError(
                        f"the lookup returned {len(results)} results for {len(batch)} scenes")
            except stashbox_api.StashBoxAPIError as e:
                log.LogWarning(f"{box_name} fingerprint lookup failed at scene {start + 1} of {total}: {e}")
                failure = e
                break
            now = time.time()
            with _transaction(conn):
                for (scene_id, updated_at, digest, _fps, durations), found in zip(batch, results):
                    conn.execute("DELETE FROM matches WHERE scene_id = ?", (scene_id,))
                    for match in found:
                        if not duration_matches(durations, match.get("duration")):
                            log.LogDebug(f"Scene {scene_id}: ignored {box_name} match {match['id']} "
                                         f"(duration {match.get('duration')}s vs {durations})")
                            continue
                        conn.execute("INSERT OR IGNORE INTO matches (scene_id, stash_id) VALUES (?, ?)",
                                     (scene_id, str(match["id"])))
                    conn.execute("INSERT OR REPLACE INTO scenes VALUES (?, ?, ?, ?)",
                                 (scene_id, updated_at, digest, now))
            queried += len(batch)
            if progress:
                progress(queried / total)

        with _transaction(conn):
            _set_meta(conn, last_run_at=time.time(), complete=0 if failure else 1)
        matched = conn.execute("SELECT COUNT(DISTINCT scene_id) FROM matches").fetchone()[0]
        owned = conn.execute("SELECT COUNT(DISTINCT stash_id) FROM matches").fetchone()[0]

    out = {
        "scanned": len(listed),
        "skipped": skipped,
        "no_fingerprints": len(no_fingerprints),
        "queried": queried,
        "remaining": total - queried,
        "matched": matched,
        "owned": owned,
        "partial": failure is not None and queried > 0,
        "complete": failure is None,
    }
    if failure is not None:
        out["error"] = _failure_message(failure, box_name, queried, total)
        out["auth_error"] = failure.is_auth_error
        out["rate_limited"] = failure.is_rate_limited
        if failure.retry_after is not None:
            out["retry_after"] = failure.retry_after
    return out

"""Location of sceneMatcher's runtime state.

State lives in <Stash config dir>/plugin_data/sceneMatcher/ so that plugin
updates (which replace the plugin dir) don't wipe it.
"""

import os
import tempfile

import log

PLUGIN_ID = "sceneMatcher"

_current_dir = None


def data_dir(server_connection=None):
    """Return (and create) the data dir for the given Stash server_connection."""
    base = (server_connection or {}).get("Dir")
    if base:
        path = os.path.join(base, "plugin_data", PLUGIN_ID)
    else:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(path, exist_ok=True)
    return path


def configure(server_connection):
    """Remember the data dir for this process. Never raises.

    If <Stash config dir>/plugin_data can't be created (read-only mount,
    permissions), falls back to the plugin dir's data folder, then to a temp dir.
    """
    global _current_dir
    try:
        _current_dir = data_dir(server_connection)
        return
    except OSError as e:
        log.LogWarning(f"Could not create the plugin data folder ({e}); using the plugin folder instead")
    try:
        _current_dir = data_dir(None)
        return
    except OSError as e:
        log.LogWarning(f"Could not create the plugin folder's data folder ({e}); using a temp folder")
    path = os.path.join(tempfile.gettempdir(), PLUGIN_ID)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = tempfile.gettempdir()
    _current_dir = path


def current_dir():
    """The configured data dir, or the plugin-dir fallback if unconfigured."""
    if _current_dir is None:
        return data_dir(None)
    return _current_dir

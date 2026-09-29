"""Location of missingScenes's runtime state.

State lives in <Stash config dir>/plugin_data/missingScenes/ so that plugin
updates (which replace the plugin dir) don't wipe it. Nothing is created until
configure() or current_dir() is called, so importing works on a read-only plugin dir.
"""

import os
import tempfile

import log

PLUGIN_ID = "missingScenes"

_current_dir = None


def default_dir():
    """The plugin dir's data folder, the fallback when Stash's config dir is unknown.
    Only a path: nothing is created."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def data_dir(server_connection=None):
    """Return (and create) the data dir for the given Stash server_connection.

    Raises:
        OSError: the folder can't be created.
    """
    base = (server_connection or {}).get("Dir")
    path = os.path.join(base, "plugin_data", PLUGIN_ID) if base else default_dir()
    os.makedirs(path, exist_ok=True)
    return path


def configure(server_connection):
    """Remember the data dir for this process. Never raises.

    If <Stash config dir>/plugin_data can't be created (read-only mount,
    permissions), falls back to the plugin dir's data folder, then to a temp dir.
    """
    global _current_dir
    if (server_connection or {}).get("Dir"):
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
    """The configured data dir. Unconfigured, the plugin-dir fallback (else a temp dir),
    created on first use. Never raises."""
    if _current_dir is None:
        configure(None)
    return _current_dir

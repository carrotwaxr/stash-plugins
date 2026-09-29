"""Location of tagManager's runtime state.

State lives in <Stash config dir>/plugin_data/tagManager/ so that plugin
updates (which replace the plugin dir) don't wipe it.
"""

import os

PLUGIN_ID = "tagManager"

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
    """Remember the data dir for this process."""
    global _current_dir
    _current_dir = data_dir(server_connection)


def current_dir():
    """The configured data dir, or the plugin-dir fallback if unconfigured."""
    if _current_dir is None:
        return data_dir(None)
    return _current_dir

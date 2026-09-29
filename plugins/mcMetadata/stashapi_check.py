"""Pre-flight check for the stashapp-tools dependency.

Deliberately imports nothing from stashapi, so it can run (and report a clear
error) when stashapp-tools is missing or too old.
"""

import importlib.metadata
import importlib.util

MIN_STASHAPP_TOOLS_VERSION = "0.2.59"

INSTALL_HELP = (
    "Install it with: pip install stashapp-tools "
    "(see the Python prerequisites section of the repository README)."
)


def parse_version(version_str):
    """Parse a version string into a tuple of integers for comparison."""
    try:
        return tuple(int(x) for x in version_str.split("."))
    except (ValueError, AttributeError):
        return (0,)


def stashapi_problem():
    """Return an error message if stashapp-tools is missing/too old, else None."""
    try:
        found = importlib.util.find_spec("stashapi") is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        return f"mcMetadata requires the Python package stashapp-tools, which is not installed. {INSTALL_HELP}"
    try:
        version = importlib.metadata.version("stashapp-tools")
    except importlib.metadata.PackageNotFoundError:
        return None  # stashapi importable but not via pip metadata; don't block
    except Exception:
        return None
    if parse_version(version) < parse_version(MIN_STASHAPP_TOOLS_VERSION):
        return (
            f"stashapp-tools {version} is too old; mcMetadata needs "
            f"{MIN_STASHAPP_TOOLS_VERSION}+ for Stash schema 72+. "
            "Upgrade with: pip install --upgrade stashapp-tools "
            "(see the Python prerequisites section of the repository README)."
        )
    return None

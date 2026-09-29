"""Path helpers: sanitize name components, join safely under a base, fit byte limits."""
import os
import re

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)


class PathEscapeError(ValueError):
    """Raised when a joined path would land outside its base directory."""


def sanitize_component(value):
    """Make one file or folder name safe on all platforms. Keeps '&'.

    ':' becomes '-', other invalid characters and controls become a space.
    Reserved Windows names get '_' appended to the stem ('nul.txt' -> 'nul_.txt').
    """
    text = str(value if value is not None else "")
    text = text.replace(":", "-")
    text = _INVALID_CHARS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if text in ("", ".", ".."):
        return "_"
    stem, dot, rest = text.partition(".")
    if _RESERVED.match(stem):
        text = stem + "_" + dot + rest
    return text


def fit_bytes(name, max_bytes=255, keep_suffix=""):
    """Trim whole characters from the end of the stem so name fits max_bytes in UTF-8.

    keep_suffix (e.g. an extension) is preserved intact.
    """
    if len(name.encode("utf-8")) <= max_bytes:
        return name
    suffix_len = len(keep_suffix.encode("utf-8"))
    if suffix_len > max_bytes:
        raise ValueError("keep_suffix alone exceeds max_bytes")
    stem = name[: -len(keep_suffix)] if keep_suffix and name.endswith(keep_suffix) else name
    budget = max_bytes - suffix_len
    stem = stem.encode("utf-8")[:budget].decode("utf-8", "ignore")
    return stem + keep_suffix


def is_inside(path, base):
    """True if path equals or is under base (boundary-aware, case preserved, no symlink resolution)."""
    if not path or not base:
        return False
    path = os.path.normpath(path)
    base = os.path.normpath(base)
    try:
        return os.path.commonpath([path, base]) == base
    except ValueError:
        return False


def join_under(base, *parts):
    """Join parts under base, raising PathEscapeError if the result leaves base."""
    if not base:
        raise ValueError("base must not be empty")
    clean = [os.path.normpath(p).lstrip("/\\" if os.sep == "\\" else "/") for p in parts if p]
    result = os.path.normpath(os.path.join(os.path.normpath(base), *clean))
    if not is_inside(result, base):
        raise PathEscapeError(f"{result!r} escapes {base!r}")
    return result

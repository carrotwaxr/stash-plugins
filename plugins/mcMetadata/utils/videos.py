"""What counts as a video file: one list for the renamer, the sidecar finder and the NFO writer.

A video missing here would be moved by the plugin as another video's sidecar, outside
Stash, so the list is broad.
"""

# Lower case, with the dot, for str.endswith
VIDEO_EXTENSIONS = (
    ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".qt", ".wmv", ".asf", ".webm", ".flv", ".f4v",
    ".ts", ".m2ts", ".mts", ".m2v", ".mpg", ".mpeg", ".mpe", ".vob", ".iso",
    ".3gp", ".3g2", ".rm", ".rmvb", ".ogv", ".ogm", ".divx", ".xvid", ".mxf",
)


def is_video(name):
    """True if the file name has a video extension (any case)."""
    return name.lower().endswith(VIDEO_EXTENSIONS)

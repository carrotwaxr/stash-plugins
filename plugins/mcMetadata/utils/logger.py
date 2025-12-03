"""
Custom logger that wraps stashapi.log and adds file logging support.

When a log file path is configured, all log messages are written to both
Stash's log system and to the specified file. This is especially useful
for dry run mode where users want to review the full output before
committing changes.
"""

import os
from datetime import datetime
import stashapi.log as stash_log

# Global file handle for log file
_log_file = None
_log_file_path = None


def init_file_logger(filepath):
    """Initialize file logging if a path is provided.

    Args:
        filepath: Path to the log file. If empty/None, file logging is disabled.
    """
    global _log_file, _log_file_path

    if not filepath:
        return

    try:
        # Create directory if it doesn't exist
        log_dir = os.path.dirname(filepath)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # Open file in write mode (overwrites previous log)
        _log_file = open(filepath, 'w', encoding='utf-8')
        _log_file_path = filepath

        # Write header
        _log_file.write(f"mcMetadata Log - {datetime.now().isoformat()}\n")
        _log_file.write("=" * 80 + "\n\n")
        _log_file.flush()

        stash_log.info(f"File logging enabled: {filepath}")
    except Exception as err:
        stash_log.error(f"Failed to initialize file logging: {err}")


def close_file_logger():
    """Close the log file if open."""
    global _log_file, _log_file_path

    if _log_file:
        try:
            _log_file.write("\n" + "=" * 80 + "\n")
            _log_file.write(f"Log completed at {datetime.now().isoformat()}\n")
            _log_file.close()
            stash_log.info(f"Log file written to: {_log_file_path}")
        except Exception:
            pass
        finally:
            _log_file = None
            _log_file_path = None


def _write_to_file(level, message):
    """Write a message to the log file if open."""
    if _log_file:
        try:
            timestamp = datetime.now().strftime("%H:%M:%S")
            _log_file.write(f"[{timestamp}] [{level}] {message}\n")
            _log_file.flush()
        except Exception:
            pass


def debug(message):
    """Log a debug message."""
    stash_log.debug(message)
    _write_to_file("DEBUG", message)


def info(message):
    """Log an info message."""
    stash_log.info(message)
    _write_to_file("INFO", message)


def warning(message):
    """Log a warning message."""
    stash_log.warning(message)
    _write_to_file("WARN", message)


def error(message):
    """Log an error message."""
    stash_log.error(message)
    _write_to_file("ERROR", message)


def progress(value):
    """Update progress bar."""
    stash_log.progress(value)

r"""
Blacklist utility for filtering unwanted StashDB tags.

Blacklist format (stored in plugin settings):
- One pattern per line
- Literal strings for exact match (case-insensitive)
- Prefix with / for regex: /^\d+p$/, /Available$/
"""

import re
from typing import List, Optional


class Blacklist:
    """Parsed blacklist with literal and regex patterns."""

    def __init__(self, blacklist_str: Optional[str] = None):
        self.literals: set[str] = set()  # Lowercase literal patterns
        self.regexes: list[re.Pattern] = []

        if blacklist_str:
            self._parse(blacklist_str)

    def _parse(self, blacklist_str: str) -> None:
        """Parse blacklist string into patterns."""
        for line in blacklist_str.split('\n'):
            pattern = line.strip()
            if not pattern:
                continue

            if pattern.startswith('/'):
                # Regex pattern
                regex_str = pattern[1:]  # Remove leading /
                try:
                    self.regexes.append(re.compile(regex_str, re.IGNORECASE))
                except re.error as e:
                    print(f"[tagManager] Invalid regex in blacklist: {pattern} - {e}")
            else:
                # Literal pattern (case-insensitive)
                self.literals.add(pattern.lower())

    def is_blacklisted(self, tag_name: str) -> bool:
        """Check if a tag name matches any blacklist pattern."""
        if not tag_name:
            return False

        lower_name = tag_name.lower()

        # Check literal matches first (faster)
        if lower_name in self.literals:
            return True

        # Check regex patterns
        for regex in self.regexes:
            if regex.search(tag_name):
                return True

        return False

    def filter_tags(self, tags: list, name_key: str = 'name') -> tuple[list, int]:
        """
        Filter a list of tag objects, removing blacklisted ones.

        Args:
            tags: List of tag objects/dicts
            name_key: Key to access tag name (default 'name')

        Returns:
            Tuple of (filtered_tags, hidden_count)
        """
        if not self.literals and not self.regexes:
            return tags, 0

        filtered = []
        hidden = 0

        for tag in tags:
            name = tag.get(name_key) if isinstance(tag, dict) else getattr(tag, name_key, None)
            if name and self.is_blacklisted(name):
                hidden += 1
            else:
                filtered.append(tag)

        return filtered, hidden

    @property
    def count(self) -> int:
        """Total number of patterns."""
        return len(self.literals) + len(self.regexes)

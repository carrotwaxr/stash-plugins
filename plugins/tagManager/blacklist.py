r"""
Blacklist utility for filtering unwanted StashDB tags.

Blacklist format (stored in plugin settings):
- Patterns are separated by newlines, commas or semicolons; blank entries
  and surrounding whitespace are ignored.
- A plain entry is a literal, matched against the whole tag name
  (case-insensitive): 4K Available, Full HD
- An entry starting with / is a regex literal: /body/flags, e.g.
  /^\d+p$/, /Available$/, /test/i. A comma or semicolon inside the
  body does not split it (/a{1,3}b/). Regexes always match
  case-insensitively and search anywhere in the name. Flags: i (accepted,
  always on), m (MULTILINE), s (DOTALL); other flags log a warning.
- Legacy form: a / entry with no valid closing / (e.g. /^Avail) uses the
  rest of the line as the body. There commas and semicolons do not split.
- An invalid regex logs a warning and is skipped.

The same rules are implemented in JavaScript and both run
tests/blacklist_cases.json.
"""

import re
from typing import List, Optional

import log

_SEPARATORS = '\n,;'
_FLAG_CHARS = 'abcdefghijklmnopqrstuvwxyz'


def _tokenize(text: str) -> list:
    """Return (kind, body, flags, raw) tuples; kind is 'regex' or 'literal'."""
    tokens = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch.isspace() or ch in _SEPARATORS:
            i += 1
            continue

        eol = text.find('\n', i)
        if eol == -1:
            eol = n

        if ch == '/':
            # Look for the next unescaped / on this line.
            j = i + 1
            close = -1
            while j < eol:
                if text[j] == '\\' and j + 1 < eol:
                    j += 2
                    continue
                if text[j] == '/':
                    close = j
                    break
                j += 1
            if close != -1:
                k = close + 1
                while k < n and text[k] in _FLAG_CHARS:
                    k += 1
                if k == n or text[k].isspace() or text[k] in _SEPARATORS:
                    tokens.append(('regex', text[i + 1:close], text[close + 1:k], text[i:k]))
                    i = k
                    continue
            # Legacy form: rest of the line is the body.
            raw = text[i:eol].rstrip()
            tokens.append(('regex', raw[1:], '', raw))
            i = eol
            continue

        end = i
        while end < n and text[end] not in _SEPARATORS:
            end += 1
        raw = text[i:end].strip()
        tokens.append(('literal', raw, '', raw))
        i = end
    return tokens


def split_patterns(text: str) -> List[str]:
    """
    Split blacklist text into raw pattern tokens.

    Skips whitespace and the separators newline, comma and semicolon.
    A token starting with / is a regex literal up to the next unescaped /
    (a / preceded by a backslash does not close it), followed by [a-z]* flags.
    The closing / and flags must be followed by whitespace, a separator or the
    end of text; otherwise (or with no closing /) it is the legacy form and the
    token is the rest of the line (commas and semicolons do not split it).
    Regex tokens keep their slashes and flags; literal tokens run to the next
    separator and are trimmed.
    """
    return [t[3] for t in _tokenize(text or '')]


class Blacklist:
    """Parsed blacklist with literal and regex patterns."""

    def __init__(self, blacklist_str: Optional[str] = None):
        self.literals: set[str] = set()  # Lowercase literal patterns
        self.regexes: list[re.Pattern] = []

        if blacklist_str:
            self._parse(blacklist_str)

    def _parse(self, blacklist_str: str) -> None:
        """Parse blacklist string into patterns."""
        for kind, body, flags, raw in _tokenize(blacklist_str):
            if kind == 'literal':
                self.literals.add(body.lower())
                continue
            if not body:
                continue

            re_flags = re.IGNORECASE
            for flag in flags:
                if flag == 'm':
                    re_flags |= re.MULTILINE
                elif flag == 's':
                    re_flags |= re.DOTALL
                elif flag != 'i':
                    log.LogWarning(f"[tagManager] Unknown regex flag '{flag}' in blacklist: {raw}")
            try:
                self.regexes.append(re.compile(body, re_flags))
            except re.error as e:
                log.LogWarning(f"[tagManager] Invalid regex in blacklist: {raw} - {e}")

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

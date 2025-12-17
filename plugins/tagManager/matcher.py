"""
Tag matching logic with layered search strategy.

Search order:
1. Exact name match (case-insensitive)
2. Alias match (case-insensitive)
3. Synonym match (from custom mapping)
4. Fuzzy match (using thefuzz library)

Each match includes:
- tag: The matched StashDB tag
- match_type: "exact", "alias", "synonym", or "fuzzy"
- score: Confidence score (0-100)
- matched_on: What string was matched (for display)
"""

import log

# Try to import thefuzz, fall back to basic matching if not available
try:
    from thefuzz import fuzz
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False
    log.LogWarning("thefuzz not installed - fuzzy matching disabled. Install with: pip install thefuzz")


class TagMatcher:
    """
    Matches local tag names to StashDB tags using layered search.
    """

    def __init__(self, stashdb_tags, synonyms=None, fuzzy_threshold=80):
        """
        Initialize matcher with StashDB tags.

        Args:
            stashdb_tags: List of StashDB tag dicts
            synonyms: Dict mapping local names to StashDB tag names
            fuzzy_threshold: Minimum score (0-100) for fuzzy matches
        """
        self.stashdb_tags = stashdb_tags
        self.synonyms = synonyms or {}
        self.fuzzy_threshold = fuzzy_threshold

        # Build lookup indexes for fast matching
        self._build_indexes()

    def _build_indexes(self):
        """Build indexes for fast exact and alias matching."""
        # Index by lowercase name
        self.name_index = {}
        # Index by lowercase alias
        self.alias_index = {}

        for tag in self.stashdb_tags:
            name_lower = tag["name"].lower()
            self.name_index[name_lower] = tag

            for alias in tag.get("aliases", []):
                alias_lower = alias.lower()
                # Don't overwrite if already exists (first tag wins)
                if alias_lower not in self.alias_index:
                    self.alias_index[alias_lower] = tag

    def find_matches(self, local_tag_name, enable_fuzzy=True, enable_synonyms=True, limit=10):
        """
        Find matching StashDB tags for a local tag name.

        Args:
            local_tag_name: The local tag name to match
            enable_fuzzy: Whether to use fuzzy matching
            enable_synonyms: Whether to use synonym mapping
            limit: Maximum matches to return

        Returns:
            List of match dicts sorted by score (highest first):
            [
                {
                    "tag": {...},  # StashDB tag
                    "match_type": "exact|alias|synonym|fuzzy",
                    "score": 0-100,
                    "matched_on": "string that matched"
                },
                ...
            ]
        """
        matches = []
        search_term = local_tag_name.strip()
        search_lower = search_term.lower()

        # 1. Exact name match
        if search_lower in self.name_index:
            tag = self.name_index[search_lower]
            matches.append({
                "tag": tag,
                "match_type": "exact",
                "score": 100,
                "matched_on": tag["name"]
            })
            # For exact match, we could return early but let's still check
            # for other high-quality matches in case user wants alternatives

        # 2. Alias match
        if search_lower in self.alias_index:
            tag = self.alias_index[search_lower]
            # Don't add if already matched by exact name
            if not any(m["tag"]["id"] == tag["id"] for m in matches):
                matches.append({
                    "tag": tag,
                    "match_type": "alias",
                    "score": 100,
                    "matched_on": search_term
                })

        # 3. Synonym match
        if enable_synonyms and search_term in self.synonyms:
            synonym_targets = self.synonyms[search_term]
            if isinstance(synonym_targets, str):
                synonym_targets = [synonym_targets]

            for target in synonym_targets:
                target_lower = target.lower()
                if target_lower in self.name_index:
                    tag = self.name_index[target_lower]
                    if not any(m["tag"]["id"] == tag["id"] for m in matches):
                        matches.append({
                            "tag": tag,
                            "match_type": "synonym",
                            "score": 95,  # Slightly lower than exact/alias
                            "matched_on": target
                        })

        # 4. Fuzzy match (only if no exact/alias matches found)
        if enable_fuzzy and FUZZY_AVAILABLE and len(matches) == 0:
            fuzzy_matches = self._fuzzy_search(search_term, limit=limit)
            matches.extend(fuzzy_matches)

        # Sort by score descending
        matches.sort(key=lambda m: m["score"], reverse=True)

        return matches[:limit]

    def _fuzzy_search(self, search_term, limit=10):
        """
        Perform fuzzy matching against all tag names and aliases.

        Args:
            search_term: Term to search for
            limit: Maximum matches to return

        Returns:
            List of fuzzy match dicts
        """
        if not FUZZY_AVAILABLE:
            return []

        candidates = []

        for tag in self.stashdb_tags:
            # Check name
            name_score = fuzz.ratio(search_term.lower(), tag["name"].lower())
            if name_score >= self.fuzzy_threshold:
                candidates.append({
                    "tag": tag,
                    "match_type": "fuzzy",
                    "score": name_score,
                    "matched_on": tag["name"]
                })
                continue  # Don't also check aliases for same tag

            # Check aliases
            best_alias_score = 0
            best_alias = None
            for alias in tag.get("aliases", []):
                alias_score = fuzz.ratio(search_term.lower(), alias.lower())
                if alias_score > best_alias_score:
                    best_alias_score = alias_score
                    best_alias = alias

            if best_alias_score >= self.fuzzy_threshold:
                candidates.append({
                    "tag": tag,
                    "match_type": "fuzzy",
                    "score": best_alias_score,
                    "matched_on": best_alias
                })

        # Sort by score descending and limit
        candidates.sort(key=lambda m: m["score"], reverse=True)
        return candidates[:limit]


def load_synonyms(filepath):
    """
    Load synonym mappings from JSON file.

    Args:
        filepath: Path to synonyms.json

    Returns:
        Dict of synonym mappings
    """
    import json
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("synonyms", {})
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        log.LogWarning(f"Error parsing synonyms.json: {e}")
        return {}

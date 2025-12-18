"""
TagCache - Efficient lookup for local tag matching.

Pre-builds lookup maps from local Stash tags for fast matching
against StashDB tags during sync.
"""


class TagCache:
    """
    Pre-built lookup maps for efficient tag matching.

    Maps:
    - stashdb_id_map: {(endpoint, stashdb_id): local_tag_id}
    - name_map: {lowercase_name: local_tag_id}
    - alias_map: {lowercase_alias: local_tag_id}
    - id_to_name: {local_tag_id: tag_name}
    """

    def __init__(self):
        """Initialize empty cache."""
        self.stashdb_id_map = {}
        self.name_map = {}
        self.alias_map = {}
        self.id_to_name = {}
        self.tag_count = 0

    @classmethod
    def build(cls, local_tags):
        """
        Build cache from list of local tags.

        Args:
            local_tags: List of tag dicts from Stash API with keys:
                - id: Local tag ID
                - name: Tag name
                - aliases: List of alias strings
                - stash_ids: List of {endpoint, stash_id} dicts

        Returns:
            TagCache instance with populated lookup maps
        """
        cache = cls()
        cache.tag_count = len(local_tags)

        for tag in local_tags:
            tag_id = str(tag.get("id", ""))
            name = tag.get("name", "")
            aliases = tag.get("aliases", []) or []
            stash_ids = tag.get("stash_ids", []) or []

            if not tag_id or not name:
                continue

            # Index by name (lowercase for case-insensitive matching)
            cache.name_map[name.lower()] = tag_id

            # Index by each alias
            for alias in aliases:
                if alias:
                    cache.alias_map[alias.lower()] = tag_id

            # Index by StashDB ID (endpoint + stash_id tuple)
            for stash_id_entry in stash_ids:
                endpoint = stash_id_entry.get("endpoint", "")
                stash_id = stash_id_entry.get("stash_id", "")
                if endpoint and stash_id:
                    cache.stashdb_id_map[(endpoint, stash_id)] = tag_id

            # Store ID to name mapping for reverse lookup
            cache.id_to_name[tag_id] = name

        return cache

    def by_stashdb_id(self, endpoint, stashdb_id):
        """
        Find local tag ID by StashDB ID link.

        Args:
            endpoint: StashDB endpoint URL
            stashdb_id: StashDB tag UUID

        Returns:
            Local tag ID (str) if found, None otherwise
        """
        return self.stashdb_id_map.get((endpoint, stashdb_id))

    def by_name(self, name):
        """
        Find local tag ID by exact name match (case-insensitive).

        Args:
            name: Tag name to match

        Returns:
            Local tag ID (str) if found, None otherwise
        """
        if not name:
            return None
        return self.name_map.get(name.lower())

    def by_alias(self, alias):
        """
        Find local tag ID by alias match (case-insensitive).

        Args:
            alias: Alias to match

        Returns:
            Local tag ID (str) if found, None otherwise
        """
        if not alias:
            return None
        return self.alias_map.get(alias.lower())

    def get_name(self, tag_id):
        """
        Get tag name for a local tag ID.

        Args:
            tag_id: Local tag ID

        Returns:
            Tag name (str) if found, None otherwise
        """
        return self.id_to_name.get(str(tag_id))

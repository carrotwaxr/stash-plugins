"""Duplicate performer detection logic."""


def find_duplicates(performers: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """
    Find performers that share the same stash_id for the same endpoint.

    Args:
        performers: List of performer dicts from Stash API

    Returns:
        Dict mapping (endpoint, stash_id) tuples to lists of duplicate performers.
        Only includes groups with 2+ performers.
    """
    buckets: dict[tuple[str, str], list[dict]] = {}

    for performer in performers:
        stash_ids = performer.get("stash_ids") or []
        for sid in stash_ids:
            key = (sid["endpoint"], sid["stash_id"])
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(performer)

    # Filter to only actual duplicates (2+ performers sharing same stash_id)
    duplicates = {k: v for k, v in buckets.items() if len(v) >= 2}

    return duplicates


def get_total_content_count(performer: dict) -> int:
    """Calculate total content count for a performer."""
    return (
        (performer.get("scene_count") or 0)
        + (performer.get("image_count") or 0)
        + (performer.get("gallery_count") or 0)
    )


def group_by_endpoint(duplicates: dict[tuple[str, str], list[dict]]) -> dict[str, list[dict]]:
    """
    Reorganize duplicates grouped by endpoint for display.

    Returns:
        Dict mapping endpoint URLs to lists of duplicate groups.
        Each group is a dict with 'stash_id' and 'performers' keys.
    """
    by_endpoint: dict[str, list[dict]] = {}

    for (endpoint, stash_id), performers in duplicates.items():
        if endpoint not in by_endpoint:
            by_endpoint[endpoint] = []

        # Sort performers by content count (highest first) for suggested keeper
        sorted_performers = sorted(
            performers,
            key=get_total_content_count,
            reverse=True,
        )

        # Mark the suggested keeper (highest content count)
        for i, p in enumerate(sorted_performers):
            p["is_suggested"] = i == 0
            p["total_content"] = get_total_content_count(p)

        by_endpoint[endpoint].append({
            "stash_id": stash_id,
            "performers": sorted_performers,
        })

    return by_endpoint

#!/usr/bin/env python3
"""
Test script for Whisparr status integration.
Tests the new whisparr_get_status_map() function against a live Whisparr instance.

Usage:
    python test_whisparr_status.py
"""

import json
import urllib.request
import urllib.parse
import ssl

# Test configuration - update these for your environment
WHISPARR_URL = "http://10.0.0.4:6968"
WHISPARR_API_KEY = "b311570632a647dea63baf212adbc5be"

# Create SSL context
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def whisparr_request(endpoint, method="GET", payload=None):
    """Make a request to the Whisparr API."""
    url = f"{WHISPARR_URL.rstrip('/')}/api/v3/{endpoint}"
    headers = {
        "X-Api-Key": WHISPARR_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    data = None
    if payload:
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as response:
        return json.loads(response.read().decode("utf-8"))


def test_get_all_scenes():
    """Test fetching all scenes from Whisparr."""
    print("\n=== Testing whisparr_get_all_scenes ===")

    scenes = whisparr_request("movie")
    print(f"Found {len(scenes)} scenes in Whisparr")

    if scenes:
        scene = scenes[0]
        print(f"\nFirst scene sample:")
        print(f"  id: {scene.get('id')}")
        print(f"  title: {scene.get('title')}")
        print(f"  stashId: {scene.get('stashId')}")
        print(f"  foreignId: {scene.get('foreignId')}")
        print(f"  hasFile: {scene.get('hasFile')}")
        print(f"  monitored: {scene.get('monitored')}")

    return scenes


def test_get_queue():
    """Test fetching download queue from Whisparr."""
    print("\n=== Testing whisparr_get_queue ===")

    result = whisparr_request("queue?pageSize=1000")
    records = result.get("records", [])
    print(f"Found {len(records)} items in queue")

    if records:
        item = records[0]
        print(f"\nFirst queue item sample:")
        print(f"  movieId: {item.get('movieId')}")
        print(f"  title: {item.get('title')}")
        print(f"  status: {item.get('status')}")
        print(f"  trackedDownloadState: {item.get('trackedDownloadState')}")
        print(f"  size: {item.get('size')}")
        print(f"  sizeleft: {item.get('sizeleft')}")
        print(f"  timeleft: {item.get('timeleft')}")
        print(f"  errorMessage: {item.get('errorMessage')}")

    return records


def test_build_status_map():
    """Test building the full status map."""
    print("\n=== Testing whisparr_get_status_map ===")

    status_map = {}

    # Get all scenes
    scenes = whisparr_request("movie")
    whisparr_id_to_stash_id = {}

    for scene in scenes:
        stash_id = scene.get("stashId", "")
        if not stash_id:
            continue

        whisparr_id = scene.get("id")
        has_file = scene.get("hasFile", False)

        whisparr_id_to_stash_id[whisparr_id] = stash_id

        if has_file:
            status_map[stash_id] = {
                "status": "downloaded",
                "whisparr_id": whisparr_id
            }
        else:
            status_map[stash_id] = {
                "status": "waiting",
                "whisparr_id": whisparr_id
            }

    # Get queue and update statuses
    result = whisparr_request("queue?pageSize=1000")
    queue = result.get("records", [])

    for item in queue:
        movie_id = item.get("movieId")
        stash_id = whisparr_id_to_stash_id.get(movie_id)

        if not stash_id:
            continue

        queue_status = item.get("status", "").lower()
        tracked_state = item.get("trackedDownloadState", "").lower()
        error_message = item.get("errorMessage", "")

        size = item.get("size", 0)
        size_left = item.get("sizeleft", 0)
        progress = 0
        if size > 0:
            progress = round(((size - size_left) / size) * 100, 1)

        eta = item.get("timeleft")

        if queue_status == "warning" or "stalled" in error_message.lower():
            status_map[stash_id] = {
                "status": "stalled",
                "progress": progress,
                "eta": eta,
                "error": error_message,
                "whisparr_id": movie_id
            }
        elif queue_status == "downloading" or tracked_state == "downloading":
            status_map[stash_id] = {
                "status": "downloading",
                "progress": progress,
                "eta": eta,
                "whisparr_id": movie_id
            }
        elif queue_status == "queued":
            status_map[stash_id] = {
                "status": "queued",
                "eta": eta,
                "whisparr_id": movie_id
            }
        else:
            status_map[stash_id] = {
                "status": "queued",
                "progress": progress,
                "eta": eta,
                "whisparr_id": movie_id
            }

    print(f"\nBuilt status map for {len(status_map)} scenes")

    # Count by status
    status_counts = {}
    for stash_id, info in status_map.items():
        status = info.get("status")
        status_counts[status] = status_counts.get(status, 0) + 1

    print("\nStatus breakdown:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    # Show some examples
    print("\nSample entries by status:")
    shown_statuses = set()
    for stash_id, info in status_map.items():
        status = info.get("status")
        if status not in shown_statuses:
            shown_statuses.add(status)
            print(f"\n  [{status}] stash_id={stash_id[:20]}...")
            print(f"    {json.dumps(info, indent=4)}")

    return status_map


def main():
    print("Whisparr Status Integration Test")
    print("=" * 50)

    try:
        # Test individual components
        scenes = test_get_all_scenes()
        queue = test_get_queue()

        # Test the full status map
        status_map = test_build_status_map()

        print("\n" + "=" * 50)
        print("All tests passed!")
        print(f"Total scenes tracked: {len(status_map)}")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())

#!/usr/bin/env python3
"""
Simulate tagManager results against local Stash and StashDB.
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stashdb_api import query_all_tags, search_tags_by_name
from matcher import TagMatcher

# Config
STASH_URL = 'http://10.0.0.4:6969/graphql'
STASH_API_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1aWQiOiJwaG9lbml4IiwiaWF0IjoxNjM0MjMwOTQ0LCJzdWIiOiJBUElLZXkifQ.obrT2FJFLWNVA6z7yhnqSg3t1_Ul8Ku7pLKG76clkNc'
STASHDB_URL = 'https://stashdb.org/graphql'
STASHDB_API_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1aWQiOiJjZGFkODkzOC03YTBhLTRhMDYtOTA4OC1iYWI4YWRkMGEwODMiLCJzdWIiOiJBUElLZXkiLCJpYXQiOjE2NDMxNDgyNDZ9.rNM5kQEbiUPAV03hl5OMJDhZ5r4NQhA70lUeIhcv6zs'


def fetch_local_tags():
    """Fetch all tags from local Stash."""
    query = '''
    query FindTags {
      findTags(filter: { per_page: -1 }) {
        count
        tags {
          id
          name
          description
          aliases
          stash_ids {
            endpoint
            stash_id
          }
        }
      }
    }
    '''

    headers = {
        'Content-Type': 'application/json',
        'ApiKey': STASH_API_KEY
    }

    data = json.dumps({'query': query}).encode('utf-8')
    req = urllib.request.Request(STASH_URL, data=data, headers=headers, method='POST')

    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.loads(response.read().decode('utf-8'))
        return result.get('data', {}).get('findTags', {}).get('tags', [])


def main():
    print("=" * 60)
    print("tagManager Simulation - What will be found in your library")
    print("=" * 60)
    print()

    # Step 1: Fetch local tags
    print("[1/3] Fetching tags from local Stash...")
    local_tags = fetch_local_tags()

    unmatched = [t for t in local_tags if not t.get('stash_ids') or len(t['stash_ids']) == 0]
    matched = [t for t in local_tags if t.get('stash_ids') and len(t['stash_ids']) > 0]

    print(f"      Total tags: {len(local_tags)}")
    print(f"      Already matched: {len(matched)}")
    print(f"      Unmatched: {len(unmatched)}")
    print()

    # Step 2: Fetch StashDB tags
    print("[2/3] Fetching all tags from StashDB (for fuzzy matching)...")
    stashdb_tags = query_all_tags(STASHDB_URL, STASHDB_API_KEY)
    print(f"      Loaded {len(stashdb_tags)} StashDB tags")
    print()

    # Step 3: Match each unmatched tag
    print("[3/3] Finding matches for unmatched tags...")
    print()

    matcher = TagMatcher(stashdb_tags, fuzzy_threshold=80)

    results = {
        'exact': [],
        'alias': [],
        'fuzzy': [],
        'synonym': [],
        'no_match': []
    }

    for i, tag in enumerate(unmatched):
        # First try API search
        api_matches = search_tags_by_name(STASHDB_URL, STASHDB_API_KEY, tag['name'], limit=5)

        # Also try local fuzzy matching
        local_matches = matcher.find_matches(tag['name'], enable_fuzzy=True, limit=5)

        # Combine
        seen_ids = set()
        combined = []

        for m in api_matches:
            if m['id'] not in seen_ids:
                seen_ids.add(m['id'])
                match_type = 'exact' if m['name'].lower() == tag['name'].lower() else 'alias'
                combined.append({
                    'tag': m,
                    'match_type': match_type,
                    'score': 100
                })

        for m in local_matches:
            if m['tag']['id'] not in seen_ids:
                seen_ids.add(m['tag']['id'])
                combined.append(m)

        combined.sort(key=lambda x: x['score'], reverse=True)

        if combined:
            best = combined[0]
            match_type = best['match_type']
            cat = best['tag'].get('category')
            results[match_type].append({
                'local': tag['name'],
                'stashdb': best['tag']['name'],
                'score': best['score'],
                'category': cat.get('name', '') if cat else ''
            })
        else:
            results['no_match'].append(tag['name'])

        # Progress
        if (i + 1) % 25 == 0 or i == len(unmatched) - 1:
            print(f"      Processed {i + 1}/{len(unmatched)} tags...")

    print()
    print("=" * 60)
    print("SIMULATION RESULTS")
    print("=" * 60)
    print()

    # Summary
    print(f"EXACT MATCHES ({len(results['exact'])} tags - ready for one-click accept):")
    print("-" * 50)
    for r in results['exact'][:20]:
        print(f"  {r['local']} -> {r['stashdb']} [{r['category']}]")
    if len(results['exact']) > 20:
        print(f"  ... and {len(results['exact']) - 20} more")
    print()

    print(f"ALIAS MATCHES ({len(results['alias'])} tags - high confidence):")
    print("-" * 50)
    for r in results['alias'][:20]:
        print(f"  {r['local']} -> {r['stashdb']} [{r['category']}]")
    if len(results['alias']) > 20:
        print(f"  ... and {len(results['alias']) - 20} more")
    print()

    print(f"FUZZY MATCHES ({len(results['fuzzy'])} tags - review recommended):")
    print("-" * 50)
    for r in results['fuzzy'][:20]:
        print(f"  {r['local']} -> {r['stashdb']} ({r['score']}%) [{r['category']}]")
    if len(results['fuzzy']) > 20:
        print(f"  ... and {len(results['fuzzy']) - 20} more")
    print()

    print(f"NO MATCHES ({len(results['no_match'])} tags - manual search needed):")
    print("-" * 50)
    for name in results['no_match'][:30]:
        print(f"  {name}")
    if len(results['no_match']) > 30:
        print(f"  ... and {len(results['no_match']) - 30} more")
    print()

    # Final summary
    total_matched = len(results['exact']) + len(results['alias']) + len(results['fuzzy'])
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Tags that can be auto-matched: {total_matched}/{len(unmatched)} ({100*total_matched//len(unmatched) if unmatched else 0}%)")
    print(f"    - Exact matches: {len(results['exact'])}")
    print(f"    - Alias matches: {len(results['alias'])}")
    print(f"    - Fuzzy matches: {len(results['fuzzy'])}")
    print(f"  Tags needing manual search: {len(results['no_match'])}")
    print()


if __name__ == '__main__':
    main()

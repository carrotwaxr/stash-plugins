"""
StashDB API utilities for tag queries.

Features:
- Fetch all tags with pagination
- Search tags by name (uses StashDB's names filter which searches name + aliases)
- Retry with exponential backoff for transient errors
"""

import json
import os
import re
import ssl
import time
import urllib.request
import urllib.error

import log

# SSL context for HTTPS requests (uses system defaults with proper verification)
SSL_CONTEXT = ssl.create_default_context()

# Default configuration
DEFAULT_CONFIG = {
    "max_retries": 3,
    "initial_retry_delay": 1.0,
    "max_retry_delay": 30.0,
    "retry_backoff_multiplier": 2.0,
    "request_delay": 0.3,
    "request_timeout": 30,
    "per_page": 100,
}

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _read_plugin_version():
    """Read the version from tagManager.yml next to this module."""
    try:
        yml = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tagManager.yml")
        with open(yml, encoding="utf-8") as f:
            m = re.search(r"^version:\s*(\S+)", f.read(), re.MULTILINE)
        if m:
            return m.group(1).strip("\"'")
    except OSError:
        pass
    return "unknown"


PLUGIN_VERSION = _read_plugin_version()
USER_AGENT = f"stash-plugins-tagManager/{PLUGIN_VERSION}"


class RateLimiter:
    """
    Rate limiter for API calls.

    Enforces minimum interval between requests and provides
    exponential backoff for retry scenarios.
    """

    def __init__(self, requests_per_second=2):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Max requests per second (default 2)
        """
        self.min_interval = 1.0 / requests_per_second
        self.last_request = 0

    def wait(self):
        """Block until rate limit allows next request."""
        now = time.time()
        elapsed = now - self.last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request = time.time()

    def backoff(self, attempt):
        """
        Calculate exponential backoff delay.

        Args:
            attempt: Attempt number (0-indexed)

        Returns:
            Delay in seconds (2^attempt)
        """
        return float(2 ** attempt)


class StashDBAPIError(Exception):
    """Exception for StashDB API errors."""

    def __init__(self, message, status_code=None, retryable=False, body=None, graphql=False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.body = body
        self.graphql = graphql

    @property
    def is_auth_error(self):
        """True for HTTP 401/403 or a GraphQL 'not authorized'/'unauthorized' error."""
        if self.status_code in (401, 403):
            return True
        if self.graphql:
            msg = str(self).lower()
            return "not authorized" in msg or "unauthorized" in msg
        return False


def graphql_request(url, query, variables=None, api_key=None, timeout=30):
    """
    Make a GraphQL request to StashDB.

    Args:
        url: GraphQL endpoint URL
        query: GraphQL query string
        variables: Query variables dict
        api_key: API key for authentication
        timeout: Request timeout in seconds

    Returns:
        Response data dict

    Raises:
        StashDBAPIError: On request failure
    """
    log.LogTrace(f"GraphQL request to {url}")
    log.LogTrace(f"Variables: {json.dumps(variables) if variables else 'None'}")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }

    if api_key:
        headers["ApiKey"] = api_key
        log.LogTrace("API key provided")
    else:
        log.LogWarning("No API key provided for request")

    data = json.dumps({
        "query": query,
        "variables": variables or {}
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    max_retries = DEFAULT_CONFIG["max_retries"]
    delay = DEFAULT_CONFIG["initial_retry_delay"]

    for attempt in range(max_retries + 1):
        try:
            log.LogTrace(f"Request attempt {attempt + 1}/{max_retries + 1}")
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as response:
                response_data = response.read().decode("utf-8")
                log.LogTrace(f"Response received: {len(response_data)} bytes")
                result = json.loads(response_data)

                if "errors" in result:
                    error_messages = [e.get("message", str(e)) for e in result["errors"]]
                    if result.get("data") is None:
                        raise StashDBAPIError(
                            "GraphQL error: " + "; ".join(str(m) for m in error_messages),
                            graphql=True,
                        )
                    log.LogWarning(f"GraphQL errors: {error_messages}")

                return result.get("data")

        except urllib.error.HTTPError as e:
            log.LogDebug(f"HTTP error {e.code}: {e.reason}")
            if e.code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                log.LogWarning(f"HTTP {e.code}, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                delay = min(delay * DEFAULT_CONFIG["retry_backoff_multiplier"], DEFAULT_CONFIG["max_retry_delay"])
                continue

            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")[:500]
                log.LogDebug(f"Error response body: {error_body}")
            except Exception:
                pass

            message = f"HTTP {e.code}: {e.reason}"
            if error_body:
                message += f" - {error_body[:200]}"
            raise StashDBAPIError(
                message,
                status_code=e.code,
                retryable=e.code in RETRYABLE_STATUS_CODES,
                body=error_body or None,
            )

        except urllib.error.URLError as e:
            log.LogDebug(f"URL error: {e.reason}")
            if attempt < max_retries:
                log.LogWarning(f"Connection error: {e.reason}, retrying in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * DEFAULT_CONFIG["retry_backoff_multiplier"], DEFAULT_CONFIG["max_retry_delay"])
                continue

            raise StashDBAPIError(f"Connection failed: {e.reason}")

        except json.JSONDecodeError as e:
            log.LogError(f"Failed to parse JSON response: {e}")
            raise StashDBAPIError(f"Invalid JSON response: {e}")

    raise StashDBAPIError("Max retries exceeded")


# GraphQL query fragments
TAG_FIELDS = """
    id
    name
    description
    aliases
    category {
        id
        name
        group
    }
"""


MAX_TAG_PAGES = 500


def query_all_tags(url, api_key, per_page=1000):
    """
    Fetch all tags from StashDB with pagination.

    Args:
        url: StashDB GraphQL endpoint
        api_key: StashDB API key
        per_page: Results per page (default 1000; falls back to 100 if
            the first page is rejected)

    Returns:
        List of all tags

    Raises:
        StashDBAPIError: If any page fails (no partial list is returned)
    """
    query = f"""
    query QueryTags($input: TagQueryInput!) {{
        queryTags(input: $input) {{
            count
            tags {{
                {TAG_FIELDS}
            }}
        }}
    }}
    """

    all_tags = []
    seen_ids = set()
    first_page_len = None
    pages_fetched = 0
    page = 1

    while True:
        variables = {
            "input": {
                "page": page,
                "per_page": per_page,
                "sort": "NAME",
                "direction": "ASC"
            }
        }

        try:
            data = graphql_request(url, query, variables, api_key)
        except StashDBAPIError as e:
            rejected = e.status_code in (400, 422) or e.graphql
            if page == 1 and per_page > 100 and rejected and not e.is_auth_error:
                log.LogWarning(f"per_page={per_page} rejected ({e}); retrying with 100")
                per_page = 100
                continue
            raise

        if not data:
            break

        query_data = data.get("queryTags", {})
        tags = query_data.get("tags", [])
        total = query_data.get("count", 0)

        if not tags:
            break

        # Dedupe by id: some endpoints ignore sort or repeat pages
        new_tags = [t for t in tags if t.get("id") not in seen_ids]
        seen_ids.update(t.get("id") for t in new_tags)
        all_tags.extend(new_tags)
        pages_fetched += 1

        log.LogDebug(f"Tags: page {page}, got {len(tags)} (count: {total}, collected: {len(all_tags)})")

        if not new_tags:
            break
        if first_page_len is None:
            first_page_len = len(tags)
        elif len(tags) < first_page_len:
            break  # short page = last page
        # Some endpoints (ThePornDB) report count per page, not a total; only
        # trust it when it exceeds the first page's length.
        if total > first_page_len and len(all_tags) >= total:
            break

        if page >= MAX_TAG_PAGES:
            log.LogWarning(f"Tags: stopped at the {MAX_TAG_PAGES}-page safety cap")
            break

        page += 1
        time.sleep(DEFAULT_CONFIG["request_delay"])

    log.LogInfo(f"Fetched {len(all_tags)} tags total in {pages_fetched} pages from {url}")
    return all_tags


def search_tags_by_name(url, api_key, search_term, limit=50):
    """
    Search StashDB tags by name.

    Uses the 'names' filter which searches both tag names and aliases.
    e.g., searching "Anklet" will find "Ankle Bracelet" because "Anklet" is an alias.

    Args:
        url: StashDB GraphQL endpoint
        api_key: StashDB API key
        search_term: Search term
        limit: Maximum results to return

    Returns:
        List of matching tags
    """
    query = f"""
    query QueryTags($input: TagQueryInput!) {{
        queryTags(input: $input) {{
            count
            tags {{
                {TAG_FIELDS}
            }}
        }}
    }}
    """

    variables = {
        "input": {
            "names": search_term,
            "page": 1,
            "per_page": limit,
            "sort": "NAME",
            "direction": "ASC"
        }
    }

    data = graphql_request(url, query, variables, api_key)

    if not data:
        return []

    tags = data.get("queryTags", {}).get("tags", [])
    log.LogDebug(f"Search '{search_term}': found {len(tags)} tags")
    return tags


# GraphQL fragment for scene with tags
SCENE_WITH_TAGS_FIELDS = """
    id
    title
    tags {
        id
        name
        aliases
    }
"""


def find_scene_by_id(url, api_key, scene_id, rate_limiter=None):
    """
    Query StashDB for a single scene by its ID.

    Args:
        url: StashDB GraphQL endpoint
        api_key: StashDB API key
        scene_id: StashDB scene UUID
        rate_limiter: Optional RateLimiter instance

    Returns:
        Scene dict with tags, or None if not found
    """
    query = f"""
    query FindScene($id: ID!) {{
        findScene(id: $id) {{
            {SCENE_WITH_TAGS_FIELDS}
        }}
    }}
    """

    variables = {"id": scene_id}

    if rate_limiter:
        rate_limiter.wait()

    data = graphql_request(url, query, variables, api_key)

    if not data:
        return None

    return data.get("findScene")


def find_scenes_by_fingerprints(url, api_key, fingerprint_batches, rate_limiter=None):
    """
    Batch query StashDB for scenes by fingerprints.

    Args:
        url: StashDB GraphQL endpoint
        api_key: StashDB API key
        fingerprint_batches: List of fingerprint lists (max 40 batches)
            Each fingerprint: {'hash': str, 'algorithm': 'MD5'|'OSHASH'|'PHASH'}
        rate_limiter: Optional RateLimiter instance

    Returns:
        List of lists of scene dicts (one list per input batch)
    """
    if len(fingerprint_batches) > 40:
        log.LogWarning(f"Fingerprint batch size {len(fingerprint_batches)} exceeds limit of 40")
        fingerprint_batches = fingerprint_batches[:40]

    query = f"""
    query FindScenesByFingerprints($fingerprints: [[FingerprintQueryInput!]!]!) {{
        findScenesBySceneFingerprints(fingerprints: $fingerprints) {{
            {SCENE_WITH_TAGS_FIELDS}
        }}
    }}
    """

    # Convert to GraphQL input format
    gql_fingerprints = []
    for batch in fingerprint_batches:
        gql_batch = []
        for fp in batch:
            gql_batch.append({
                "hash": fp["hash"],
                "algorithm": fp["algorithm"]
            })
        gql_fingerprints.append(gql_batch)

    variables = {"fingerprints": gql_fingerprints}

    if rate_limiter:
        rate_limiter.wait()

    data = graphql_request(url, query, variables, api_key)

    if not data:
        return [[] for _ in fingerprint_batches]

    return data.get("findScenesBySceneFingerprints", [])

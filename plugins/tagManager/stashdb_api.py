"""
StashDB API utilities for tag queries.

Features:
- Fetch all tags with pagination
- Search tags by name (uses StashDB's names filter which searches name + aliases)
- Retry with exponential backoff for transient errors
"""

import json
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


class StashDBAPIError(Exception):
    """Exception for StashDB API errors."""

    def __init__(self, message, status_code=None, retryable=False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


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
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if api_key:
        headers["ApiKey"] = api_key

    data = json.dumps({
        "query": query,
        "variables": variables or {}
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    max_retries = DEFAULT_CONFIG["max_retries"]
    delay = DEFAULT_CONFIG["initial_retry_delay"]

    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as response:
                result = json.loads(response.read().decode("utf-8"))

                if "errors" in result:
                    error_messages = [e.get("message", str(e)) for e in result["errors"]]
                    log.LogWarning(f"GraphQL errors: {error_messages}")

                return result.get("data")

        except urllib.error.HTTPError as e:
            if e.code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                log.LogWarning(f"HTTP {e.code}, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                delay = min(delay * DEFAULT_CONFIG["retry_backoff_multiplier"], DEFAULT_CONFIG["max_retry_delay"])
                continue

            raise StashDBAPIError(f"HTTP {e.code}: {e.reason}", status_code=e.code)

        except urllib.error.URLError as e:
            if attempt < max_retries:
                log.LogWarning(f"Connection error: {e.reason}, retrying in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * DEFAULT_CONFIG["retry_backoff_multiplier"], DEFAULT_CONFIG["max_retry_delay"])
                continue

            raise StashDBAPIError(f"Connection failed: {e.reason}")

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


def query_all_tags(url, api_key, per_page=100):
    """
    Fetch all tags from StashDB with pagination.

    Args:
        url: StashDB GraphQL endpoint
        api_key: StashDB API key
        per_page: Results per page (default 100)

    Returns:
        List of all tags
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
            log.LogWarning(f"Error fetching tags page {page}: {e}")
            break

        if not data:
            break

        query_data = data.get("queryTags", {})
        tags = query_data.get("tags", [])
        total = query_data.get("count", 0)

        if not tags:
            break

        all_tags.extend(tags)

        log.LogDebug(f"Tags: page {page}, got {len(tags)} (total: {total}, collected: {len(all_tags)})")

        if len(all_tags) >= total:
            break

        page += 1
        time.sleep(DEFAULT_CONFIG["request_delay"])

    log.LogInfo(f"StashDB: Fetched {len(all_tags)} tags total")
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

    try:
        data = graphql_request(url, query, variables, api_key)
    except StashDBAPIError as e:
        log.LogWarning(f"Error searching tags for '{search_term}': {e}")
        return []

    if not data:
        return []

    tags = data.get("queryTags", {}).get("tags", [])
    log.LogDebug(f"Search '{search_term}': found {len(tags)} tags")
    return tags

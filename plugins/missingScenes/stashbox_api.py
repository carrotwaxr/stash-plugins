"""
StashDB/Stash-Box API utilities with resilience patterns.

Features:
- Retry with exponential backoff for transient errors (504, 503, connection errors)
- Rate limit detection and handling (429)
- Configurable delays between paginated requests
- Graceful degradation with partial results

This module is designed to be copied into each plugin that needs StashDB access,
since Stash plugins must be self-contained (no shared imports across plugins).
"""

import json
import ssl
import time
import urllib.request
import urllib.error

import log

# Create SSL context that doesn't verify certificates (for self-signed certs)
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# Default configuration - can be overridden via plugin settings
DEFAULT_CONFIG = {
    # Retry settings
    "max_retries": 3,
    "initial_retry_delay": 1.0,  # seconds
    "max_retry_delay": 30.0,  # seconds
    "retry_backoff_multiplier": 2.0,

    # Rate limiting
    "request_delay": 0.5,  # seconds between requests in pagination
    "rate_limit_pause": 60.0,  # seconds to pause on 429

    # Pagination limits (reduced from original 50 to be more courteous)
    "max_pages_performer": 25,  # Max pages for performer scene queries
    "max_pages_studio": 25,  # Max pages for studio scene queries
    "per_page": 100,  # Results per page

    # Timeouts
    "request_timeout": 30,  # seconds
}

# HTTP status codes that should trigger a retry
RETRYABLE_STATUS_CODES = {
    429,  # Too Many Requests (rate limited)
    500,  # Internal Server Error
    502,  # Bad Gateway
    503,  # Service Unavailable
    504,  # Gateway Timeout
}


class StashBoxAPIError(Exception):
    """Exception for StashDB API errors with context."""

    def __init__(self, message, status_code=None, retryable=False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def get_config(plugin_settings, key):
    """Get a config value, preferring plugin settings over defaults.

    Validates and coerces types to ensure safe values:
    - Integer settings: clamped to minimum of 1
    - Float settings: clamped to minimum of 0.0
    """
    # Check plugin settings first (with stashbox_ prefix)
    setting_key = f"stashbox_{key}"
    if plugin_settings and setting_key in plugin_settings:
        value = plugin_settings[setting_key]
    else:
        value = DEFAULT_CONFIG.get(key)

    # Validate and coerce numeric settings
    integer_keys = {"max_retries", "per_page", "max_pages_performer", "max_pages_studio"}
    float_keys = {"initial_retry_delay", "max_retry_delay", "retry_backoff_multiplier",
                  "request_delay", "rate_limit_pause", "request_timeout"}

    if key in integer_keys:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return DEFAULT_CONFIG.get(key, 1)

    if key in float_keys:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return DEFAULT_CONFIG.get(key, 0.0)

    return value


def graphql_request_with_retry(url, query, variables=None, api_key=None,
                                plugin_settings=None, operation_name=None):
    """
    Make a GraphQL request with retry logic for transient failures.

    Args:
        url: GraphQL endpoint URL
        query: GraphQL query string
        variables: Query variables dict
        api_key: API key for authentication
        plugin_settings: Plugin configuration for retry/timeout settings
        operation_name: Human-readable name for logging

    Returns:
        Response data dict, or None on failure

    Raises:
        StashBoxAPIError: On non-retryable errors or after max retries
    """
    max_retries = get_config(plugin_settings, "max_retries")
    initial_delay = get_config(plugin_settings, "initial_retry_delay")
    max_delay = get_config(plugin_settings, "max_retry_delay")
    backoff_multiplier = get_config(plugin_settings, "retry_backoff_multiplier")
    timeout = get_config(plugin_settings, "request_timeout")
    rate_limit_pause = get_config(plugin_settings, "rate_limit_pause")

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

    last_error = None
    delay = initial_delay

    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as response:
                result = json.loads(response.read().decode("utf-8"))

                if "errors" in result:
                    # GraphQL errors (not HTTP errors) - log but return data if present
                    error_messages = [e.get("message", str(e)) for e in result["errors"]]
                    log.LogWarning(f"GraphQL errors: {error_messages}")

                return result.get("data")

        except urllib.error.HTTPError as e:
            status_code = e.code
            last_error = e

            # Handle rate limiting specially
            if status_code == 429:
                if attempt < max_retries:
                    log.LogWarning(
                        f"Rate limited (429) on {operation_name or 'request'}. "
                        f"Pausing {rate_limit_pause}s before retry {attempt + 1}/{max_retries}"
                    )
                    time.sleep(rate_limit_pause)
                    continue
                else:
                    log.LogError(f"Rate limited (429) - max retries exceeded")
                    raise StashBoxAPIError(
                        f"Rate limited by StashDB after {max_retries} retries",
                        status_code=429,
                        retryable=False
                    )

            # Check if this is a retryable error
            if status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                log.LogWarning(
                    f"HTTP {status_code} on {operation_name or 'request'}. "
                    f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                delay = min(delay * backoff_multiplier, max_delay)
                continue

            # Non-retryable or max retries exceeded
            log.LogError(f"HTTP error {status_code}: {e.reason}")
            raise StashBoxAPIError(
                f"HTTP {status_code}: {e.reason}",
                status_code=status_code,
                retryable=status_code in RETRYABLE_STATUS_CODES
            )

        except urllib.error.URLError as e:
            last_error = e

            # Connection errors are often transient
            if attempt < max_retries:
                log.LogWarning(
                    f"Connection error on {operation_name or 'request'}: {e.reason}. "
                    f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                delay = min(delay * backoff_multiplier, max_delay)
                continue

            log.LogError(f"URL error after {max_retries} retries: {e.reason}")
            raise StashBoxAPIError(
                f"Connection failed: {e.reason}",
                retryable=True
            )

        except Exception as e:
            log.LogError(f"Unexpected error: {e}")
            raise StashBoxAPIError(f"Unexpected error: {e}")

    # Should not reach here, but just in case
    raise StashBoxAPIError(
        f"Failed after {max_retries} retries: {last_error}",
        retryable=True
    )


def paginated_query(url, api_key, query, build_variables_fn, extract_fn,
                    plugin_settings=None, operation_name=None, max_pages=None):
    """
    Execute a paginated GraphQL query with rate limiting between pages.

    Args:
        url: GraphQL endpoint URL
        api_key: API key for authentication
        query: GraphQL query string
        build_variables_fn: Function(page, per_page) -> variables dict
        extract_fn: Function(data) -> (items list, total count)
        plugin_settings: Plugin configuration
        operation_name: Human-readable name for logging
        max_pages: Override default max pages limit

    Returns:
        List of all items collected across pages
    """
    request_delay = get_config(plugin_settings, "request_delay")
    per_page = get_config(plugin_settings, "per_page")

    if max_pages is None:
        max_pages = get_config(plugin_settings, "max_pages_performer")

    all_items = []
    page = 1

    while page <= max_pages:
        variables = build_variables_fn(page, per_page)

        try:
            data = graphql_request_with_retry(
                url, query, variables, api_key,
                plugin_settings=plugin_settings,
                operation_name=f"{operation_name or 'query'} (page {page})"
            )
        except StashBoxAPIError as e:
            # On error, return what we have so far (graceful degradation)
            log.LogWarning(
                f"Stopping pagination on {operation_name} at page {page} due to error: {e}. "
                f"Returning {len(all_items)} items collected so far."
            )
            break

        if not data:
            break

        items, total = extract_fn(data)

        if not items:
            break

        all_items.extend(items)

        log.LogDebug(
            f"{operation_name or 'Query'}: page {page}, got {len(items)} items "
            f"(total: {total}, collected: {len(all_items)})"
        )

        # Check if we've gotten all items
        if page * per_page >= total:
            break

        page += 1

        # Delay between pages to be courteous to the server
        if page <= max_pages:
            time.sleep(request_delay)

    return all_items


# ============================================================================
# Standard StashDB Queries
# ============================================================================

SCENE_FIELDS = """
    id
    title
    details
    release_date
    duration
    code
    director
    urls {
        url
        site {
            name
        }
    }
    studio {
        id
        name
    }
    images {
        id
        url
        width
        height
    }
    performers {
        performer {
            id
            name
            disambiguation
            gender
        }
        as
    }
"""


def query_performer_name(url, api_key, performer_id, plugin_settings=None):
    """Get a performer's name from StashDB."""
    query = """
    query FindPerformer($id: ID!) {
        findPerformer(id: $id) {
            name
        }
    }
    """
    try:
        data = graphql_request_with_retry(
            url, query, {"id": performer_id}, api_key,
            plugin_settings=plugin_settings,
            operation_name="get performer name"
        )
        if data and "findPerformer" in data:
            return data["findPerformer"].get("name", "Unknown")
    except StashBoxAPIError:
        pass
    return "Unknown"


def query_scenes_by_performer(url, api_key, performer_id, plugin_settings=None):
    """Query StashDB for all scenes featuring a performer.

    Performance optimization: Combines performer name lookup with first page query
    to eliminate an extra API round-trip.
    """
    # First query includes performer name lookup to avoid separate API call
    first_query = f"""
    query QueryScenesWithPerformer($input: SceneQueryInput!, $performerId: ID!) {{
        findPerformer(id: $performerId) {{
            name
        }}
        queryScenes(input: $input) {{
            count
            scenes {{
                {SCENE_FIELDS}
            }}
        }}
    }}
    """

    # Subsequent queries don't need performer name
    query = f"""
    query QueryScenes($input: SceneQueryInput!) {{
        queryScenes(input: $input) {{
            count
            scenes {{
                {SCENE_FIELDS}
            }}
        }}
    }}
    """

    request_delay = get_config(plugin_settings, "request_delay")
    per_page = get_config(plugin_settings, "per_page")
    max_pages = get_config(plugin_settings, "max_pages_performer")

    all_scenes = []
    performer_name = "Unknown"
    page = 1

    while page <= max_pages:
        variables = {
            "input": {
                "performers": {
                    "value": [performer_id],
                    "modifier": "INCLUDES"
                },
                "page": page,
                "per_page": per_page,
                "sort": "DATE",
                "direction": "DESC"
            }
        }

        # First page: use combined query with performer lookup
        if page == 1:
            variables["performerId"] = performer_id
            current_query = first_query
        else:
            current_query = query

        try:
            data = graphql_request_with_retry(
                url, current_query, variables, api_key,
                plugin_settings=plugin_settings,
                operation_name=f"scenes for performer (page {page})"
            )
        except StashBoxAPIError as e:
            log.LogWarning(f"Stopping at page {page} due to error: {e}. Returning {len(all_scenes)} scenes.")
            break

        if not data:
            break

        # Extract performer name from first response
        if page == 1 and "findPerformer" in data and data["findPerformer"]:
            performer_name = data["findPerformer"].get("name", "Unknown")

        query_data = data.get("queryScenes", {})
        scenes = query_data.get("scenes", [])
        total = query_data.get("count", 0)

        if not scenes:
            break

        all_scenes.extend(scenes)

        log.LogDebug(
            f"Scenes for '{performer_name}': page {page}, got {len(scenes)} "
            f"(total: {total}, collected: {len(all_scenes)})"
        )

        if page * per_page >= total:
            break

        page += 1

        # Delay between pages
        if page <= max_pages:
            time.sleep(request_delay)

    log.LogInfo(f"StashDB: Found {len(all_scenes)} scenes for {performer_name}")
    return all_scenes


def query_scenes_by_studio(url, api_key, studio_id, plugin_settings=None):
    """Query StashDB for all scenes from a studio."""
    query = f"""
    query QueryScenes($input: SceneQueryInput!) {{
        queryScenes(input: $input) {{
            count
            scenes {{
                {SCENE_FIELDS}
            }}
        }}
    }}
    """

    def build_variables(page, per_page):
        return {
            "input": {
                "studios": {
                    "value": [studio_id],
                    "modifier": "INCLUDES"
                },
                "page": page,
                "per_page": per_page,
                "sort": "DATE",
                "direction": "DESC"
            }
        }

    def extract(data):
        query_data = data.get("queryScenes", {})
        return query_data.get("scenes", []), query_data.get("count", 0)

    max_pages = get_config(plugin_settings, "max_pages_studio")
    scenes = paginated_query(
        url, api_key, query, build_variables, extract,
        plugin_settings=plugin_settings,
        operation_name="scenes for studio",
        max_pages=max_pages
    )

    log.LogInfo(f"StashDB: Found {len(scenes)} scenes for studio")
    return scenes


def search_scenes_by_text(url, api_key, search_term, limit=25, plugin_settings=None):
    """Search StashDB scenes by text query."""
    if not search_term or len(search_term) < 3:
        return []

    query = f"""
    query SearchScene($term: String!, $limit: Int) {{
        searchScene(term: $term, limit: $limit) {{
            {SCENE_FIELDS}
        }}
    }}
    """

    try:
        data = graphql_request_with_retry(
            url, query, {"term": search_term, "limit": limit}, api_key,
            plugin_settings=plugin_settings,
            operation_name=f"text search '{search_term[:30]}...'"
        )
        if data and "searchScene" in data:
            scenes = data["searchScene"] or []
            log.LogInfo(f"StashDB text search '{search_term[:30]}...': found {len(scenes)} scenes")
            return scenes
    except StashBoxAPIError as e:
        log.LogWarning(f"Text search failed: {e}")

    return []


def query_scenes_combined(url, api_key, performer_ids, studio_id, plugin_settings=None, max_pages=10):
    """Query StashDB with combined performer AND studio filter."""
    if not performer_ids or not studio_id:
        return []

    query = f"""
    query QueryScenes($input: SceneQueryInput!) {{
        queryScenes(input: $input) {{
            count
            scenes {{
                {SCENE_FIELDS}
            }}
        }}
    }}
    """

    def build_variables(page, per_page):
        return {
            "input": {
                "performers": {
                    "value": list(performer_ids),
                    "modifier": "INCLUDES"
                },
                "studios": {
                    "value": [studio_id],
                    "modifier": "INCLUDES"
                },
                "page": page,
                "per_page": per_page,
                "sort": "DATE",
                "direction": "DESC"
            }
        }

    def extract(data):
        query_data = data.get("queryScenes", {})
        return query_data.get("scenes", []), query_data.get("count", 0)

    scenes = paginated_query(
        url, api_key, query, build_variables, extract,
        plugin_settings=plugin_settings,
        operation_name="combined performer+studio query",
        max_pages=max_pages
    )

    log.LogInfo(f"StashDB combined (performer+studio): found {len(scenes)} scenes")
    return scenes


def query_scenes_by_performers(url, api_key, performer_ids, plugin_settings=None, max_pages=10):
    """Query StashDB for scenes featuring any of the given performers."""
    if not performer_ids:
        return []

    query = f"""
    query QueryScenes($input: SceneQueryInput!) {{
        queryScenes(input: $input) {{
            count
            scenes {{
                {SCENE_FIELDS}
            }}
        }}
    }}
    """

    def build_variables(page, per_page):
        return {
            "input": {
                "performers": {
                    "value": list(performer_ids),
                    "modifier": "INCLUDES"
                },
                "page": page,
                "per_page": per_page,
                "sort": "DATE",
                "direction": "DESC"
            }
        }

    def extract(data):
        query_data = data.get("queryScenes", {})
        return query_data.get("scenes", []), query_data.get("count", 0)

    scenes = paginated_query(
        url, api_key, query, build_variables, extract,
        plugin_settings=plugin_settings,
        operation_name=f"scenes for {len(performer_ids)} performers",
        max_pages=max_pages
    )

    log.LogInfo(f"StashDB: Found {len(scenes)} scenes for {len(performer_ids)} performers")
    return scenes

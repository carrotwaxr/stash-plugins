"""
StashDB/Stash-Box API utilities with resilience patterns.

Features:
- Retry with exponential backoff for transient errors (504, 503, connection errors)
- Rate limit handling (429) that honours Retry-After within a per-request time budget
- Typed failures (StashBoxAPIError: status_code, is_auth_error, is_rate_limited)
  so callers can report them instead of treating them as "no results"

This module is designed to be copied into each plugin that needs StashDB access,
since Stash plugins must be self-contained (no shared imports across plugins).
"""

import email.utils
import http.client
import json
import math
import os
import re
import ssl
import time
import urllib.request
import urllib.error

import log


def _read_plugin_version():
    """Read the version from missingScenes.yml next to this module."""
    try:
        yml = os.path.join(os.path.dirname(os.path.abspath(__file__)), "missingScenes.yml")
        with open(yml, encoding="utf-8") as f:
            m = re.search(r"^version:\s*(\S+)", f.read(), re.MULTILINE)
        if m:
            return m.group(1).strip("\"'")
    except OSError:
        pass
    return "unknown"


PLUGIN_VERSION = _read_plugin_version()
# ThePornDB's Cloudflare refuses Python's default User-Agent (Error 1010, HTTP 403)
USER_AGENT = f"stash-plugins-missingScenes/{PLUGIN_VERSION}"


def create_ssl_context(verify=True):
    """Build a TLS context for outbound HTTPS requests.

    Verifies certificates by default, using the system store plus certifi's
    bundle when it is installed (python.org builds on macOS ship no system CAs).
    Pass verify=False only for self-hosted services with self-signed certs.
    """
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return ctx


# Stash-box endpoints (StashDB, FansDB, ThePornDB...) are public hosts; always verify.
SSL_CONTEXT = create_ssl_context()

# Default configuration - can be overridden via plugin settings
DEFAULT_CONFIG = {
    # Retry settings
    "max_retries": 3,
    "initial_retry_delay": 1.0,  # seconds
    "max_retry_delay": 30.0,  # seconds
    "retry_backoff_multiplier": 2.0,

    # Rate limiting
    "request_delay": 0.5,  # seconds between requests in pagination
    "rate_limit_pause": 10.0,  # seconds to pause on a 429 that has no Retry-After
    # Most seconds one request may spend waiting on retries (429 pauses, backoff).
    # A wait that would go past it fails the request instead of sleeping.
    "retry_budget": 60.0,

    # Pagination limits (reduced from original 50 to be more courteous)
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

# A rejected or missing API key, or an account without the needed role
AUTH_STATUS_CODES = {401, 403}

# GraphQL error messages that mean the key or account was refused (stash-box: "not authorized")
_AUTH_MESSAGE = re.compile(r"unauthori[sz]ed|not authori[sz]ed|forbidden|unauthenticated|invalid api ?key",
                           re.IGNORECASE)


class StashBoxAPIError(Exception):
    """A stash-box request that failed, with what the caller needs to report it.

    status_code: the HTTP status, or None for connection, GraphQL and response errors.
    retry_after: seconds the server asked us to wait (429), when known.
    is_auth_error: the key or account was refused (401/403, or a GraphQL
        "not authorized" error with no data).
    is_rate_limited: the server answered 429.
    """

    def __init__(self, message, status_code=None, retryable=False, retry_after=None, auth=False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after
        self._auth = auth

    @property
    def is_auth_error(self):
        return self._auth or self.status_code in AUTH_STATUS_CODES

    @property
    def is_rate_limited(self):
        return self.status_code == 429


def get_config(plugin_settings, key):
    """Get a config value, preferring plugin settings over defaults.

    The plugin setting for `key` is `stashbox_<key>` (e.g. `stashbox_request_delay`).
    An unset, empty or unparseable setting falls back to DEFAULT_CONFIG.
    - Integer settings: clamped to minimum of 1
    - Float settings: clamped to minimum of 0.0; inf and NaN use the default
    """
    setting_key = f"stashbox_{key}"
    default = DEFAULT_CONFIG.get(key)
    value = default
    if plugin_settings and plugin_settings.get(setting_key) not in (None, ""):
        value = plugin_settings[setting_key]

    integer_keys = {"max_retries", "per_page"}
    float_keys = {"initial_retry_delay", "max_retry_delay", "retry_backoff_multiplier",
                  "request_delay", "rate_limit_pause", "request_timeout", "retry_budget"}

    if key in integer_keys:
        try:
            return max(1, int(value))
        except (TypeError, ValueError, OverflowError):
            return default if default is not None else 1

    if key in float_keys:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = None
        if number is None or not math.isfinite(number):
            return float(default) if default is not None else 0.0
        return max(0.0, number)

    return value


def parse_retry_after(value, now=None):
    """Seconds to wait from a Retry-After header (delta-seconds or an HTTP date), or None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        seconds = when.timestamp() - (time.time() if now is None else now)
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def graphql_request_with_retry(url, query, variables=None, api_key=None,
                                plugin_settings=None, operation_name=None):
    """
    Make a GraphQL request, retrying transient failures within a time budget.

    Retries 429 (waiting Retry-After, else rate_limit_pause), 5xx and connection
    errors (exponential backoff). The waits for one request never add up to more
    than retry_budget seconds: a wait that would exceed it raises instead.

    Args:
        url: GraphQL endpoint URL
        query: GraphQL query string
        variables: Query variables dict
        api_key: API key for authentication
        plugin_settings: Plugin configuration for retry/timeout settings
        operation_name: Human-readable name for logging

    Returns:
        The response's `data` dict (GraphQL errors alongside data are logged).

    Raises:
        StashBoxAPIError: on HTTP, connection, auth or rate-limit failures, a
            response that isn't JSON, no data, or GraphQL errors with no data.
    """
    max_retries = get_config(plugin_settings, "max_retries")
    initial_delay = get_config(plugin_settings, "initial_retry_delay")
    max_delay = get_config(plugin_settings, "max_retry_delay")
    backoff_multiplier = get_config(plugin_settings, "retry_backoff_multiplier")
    timeout = get_config(plugin_settings, "request_timeout")
    rate_limit_pause = get_config(plugin_settings, "rate_limit_pause")
    budget = get_config(plugin_settings, "retry_budget")
    name = operation_name or "request"

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }

    if api_key:
        headers["ApiKey"] = api_key

    data = json.dumps({
        "query": query,
        "variables": variables or {}
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    delay = initial_delay
    waited = 0.0

    def can_wait(seconds):
        return waited + seconds <= budget

    for attempt in range(max_retries + 1):
        last_attempt = attempt >= max_retries
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as response:
                body = response.read()

        except urllib.error.HTTPError as e:
            status_code = e.code

            if status_code == 429:
                retry_after = parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
                wait = retry_after if retry_after is not None else rate_limit_pause
                if last_attempt or not can_wait(wait):
                    why = (f"asked to wait {wait:.0f}s more, past the {budget:.0f}s retry budget"
                           if not can_wait(wait) else f"still limited after {max_retries} retries")
                    log.LogError(f"Rate limited (429) on {name}: {why}")
                    raise StashBoxAPIError(f"HTTP 429 Too Many Requests: {why}",
                                           status_code=429, retryable=True, retry_after=retry_after)
                log.LogWarning(f"Rate limited (429) on {name}. Waiting {wait:.1f}s "
                               f"before retry {attempt + 1}/{max_retries}")
                time.sleep(wait)
                waited += wait
                continue

            if status_code in AUTH_STATUS_CODES:
                log.LogError(f"HTTP {status_code} on {name}: the API key or account was refused")
                raise StashBoxAPIError(f"HTTP {status_code}: {e.reason}", status_code=status_code)

            retryable = status_code in RETRYABLE_STATUS_CODES
            if retryable and not last_attempt and can_wait(delay):
                log.LogWarning(f"HTTP {status_code} on {name}. "
                               f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                waited += delay
                delay = min(delay * backoff_multiplier, max_delay)
                continue

            log.LogError(f"HTTP error {status_code} on {name}: {e.reason}")
            raise StashBoxAPIError(f"HTTP {status_code}: {e.reason}",
                                   status_code=status_code, retryable=retryable)

        except urllib.error.URLError as e:
            # A bad certificate won't fix itself on retry
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                log.LogError(f"TLS certificate verification failed for {url}: {e.reason}")
                raise StashBoxAPIError(f"TLS certificate verification failed for {url}: {e.reason}")

            if not last_attempt and can_wait(delay):
                log.LogWarning(f"Connection error on {name}: {e.reason}. "
                               f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                waited += delay
                delay = min(delay * backoff_multiplier, max_delay)
                continue

            log.LogError(f"Connection failed on {name}: {e.reason}")
            raise StashBoxAPIError(f"Connection failed: {e.reason}", retryable=True)

        except (OSError, http.client.HTTPException) as e:
            # Timeouts, resets and truncated responses while reading
            if not last_attempt and can_wait(delay):
                log.LogWarning(f"Network error on {name}: {e}. "
                               f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                waited += delay
                delay = min(delay * backoff_multiplier, max_delay)
                continue

            log.LogError(f"Network error on {name}: {e}")
            raise StashBoxAPIError(f"Network error: {e}", retryable=True)

        except Exception as e:
            log.LogError(f"Unexpected error on {name}: {e}")
            raise StashBoxAPIError(f"Unexpected error: {e}")

        return _graphql_data(body, name)

    # The loop always returns or raises; this is a guard
    raise StashBoxAPIError(f"Failed after {max_retries} retries", retryable=True)


def _graphql_data(body, name):
    """The `data` of a GraphQL response body, or StashBoxAPIError."""
    try:
        result = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        log.LogError(f"Response to {name} is not JSON: {body[:200]!r}")
        raise StashBoxAPIError("The server's response is not JSON (a proxy or login page?)")
    if not isinstance(result, dict):
        raise StashBoxAPIError("The server's response is not a GraphQL response")

    data = result.get("data")
    errors = result.get("errors") or []
    if errors:
        messages = [e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errors]
        no_data = not isinstance(data, dict) or all(v is None for v in data.values())
        if no_data:
            text = "; ".join(messages)
            log.LogError(f"GraphQL errors on {name}: {messages}")
            raise StashBoxAPIError(f"GraphQL error: {text}", auth=bool(_AUTH_MESSAGE.search(text)))
        log.LogWarning(f"GraphQL errors on {name}: {messages}")

    if not isinstance(data, dict):
        raise StashBoxAPIError("The response has no data")
    return data


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
    tags {
        id
        name
    }
"""


# ============================================================================
# Paginated Single-Page Query for "Fetch Until Full" Pagination
# ============================================================================

def query_scenes_page(url, api_key, entity_type, entity_stash_id, page=1,
                      per_page=100, sort="DATE", direction="DESC",
                      plugin_settings=None):
    """
    Fetch a single page of scenes from StashDB for pagination.

    Args:
        url: StashDB GraphQL endpoint URL
        api_key: API key for authentication
        entity_type: "performer", "studio", or "tag"
        entity_stash_id: StashDB ID of the entity
        page: Page number (1-indexed)
        per_page: Number of results per page
        sort: Sort field - "DATE", "TITLE", "CREATED_AT", "UPDATED_AT", "TRENDING"
        direction: Sort direction - "ASC" or "DESC"
        plugin_settings: Plugin configuration

    Returns:
        dict with:
            - scenes: list of scene objects
            - count: total scene count on StashDB
            - page: current page number
            - has_more: whether more pages exist

    Raises:
        StashBoxAPIError: the request failed or the response has no queryScenes result.
        ValueError: an unknown entity_type.
    """
    # Validate sort field
    valid_sorts = {"DATE", "TITLE", "CREATED_AT", "UPDATED_AT", "TRENDING"}
    if sort not in valid_sorts:
        log.LogWarning(f"Invalid sort field '{sort}', using DATE")
        sort = "DATE"

    # Validate direction
    if direction not in {"ASC", "DESC"}:
        log.LogWarning(f"Invalid direction '{direction}', using DESC")
        direction = "DESC"

    # Build the input filter based on entity type
    if entity_type == "performer":
        filter_input = {
            "performers": {
                "value": [entity_stash_id],
                "modifier": "INCLUDES"
            }
        }
    elif entity_type == "studio":
        filter_input = {
            "studios": {
                "value": [entity_stash_id],
                "modifier": "INCLUDES"
            }
        }
    elif entity_type == "tag":
        filter_input = {
            "tags": {
                "value": [entity_stash_id],
                "modifier": "INCLUDES"
            }
        }
    else:
        raise ValueError(f"Unknown entity type: {entity_type}")

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

    variables = {
        "input": {
            **filter_input,
            "page": page,
            "per_page": per_page,
            "sort": sort,
            "direction": direction
        }
    }

    data = graphql_request_with_retry(
        url, query, variables, api_key,
        plugin_settings=plugin_settings,
        operation_name=f"scenes page {page} for {entity_type}"
    )
    return _scenes_page(data, page, per_page)


def _scenes_page(data, page, per_page):
    """The page dict for a queryScenes response; StashBoxAPIError when it has no result."""
    query_data = data.get("queryScenes") if isinstance(data, dict) else None
    if not isinstance(query_data, dict):
        raise StashBoxAPIError("The response has no queryScenes result")
    scenes = query_data.get("scenes")
    if not isinstance(scenes, list):
        raise StashBoxAPIError("The response's queryScenes has no scenes list")
    count = query_data.get("count")
    if not isinstance(count, int) or isinstance(count, bool):
        count = 0

    return {
        "scenes": [s for s in scenes if isinstance(s, dict)],
        "count": count,
        "page": page,
        "has_more": page * per_page < count
    }


def query_scenes_browse(url, api_key, page=1, per_page=100, sort="DATE", direction="DESC",
                        performer_ids=None, studio_ids=None, tag_ids=None,
                        excluded_tag_ids=None, plugin_settings=None):
    """
    Browse all scenes on StashDB with optional filters.

    Unlike entity-specific queries, this allows querying without a specific
    performer/studio/tag context.

    Args:
        url: StashDB GraphQL endpoint URL
        api_key: API key for authentication
        page: Page number (1-indexed)
        per_page: Results per page
        sort: Sort field - "DATE", "TITLE", "CREATED_AT", "UPDATED_AT", "TRENDING"
        direction: Sort direction - "ASC" or "DESC"
        performer_ids: List of performer StashDB IDs to filter by (INCLUDES)
        studio_ids: List of studio StashDB IDs to filter by (INCLUDES)
        tag_ids: List of tag StashDB IDs to filter by (INCLUDES)
        excluded_tag_ids: List of tag StashDB IDs to exclude (EXCLUDES)
        plugin_settings: Plugin configuration

    Returns:
        dict with scenes, count, page, has_more

    Raises:
        StashBoxAPIError: the request failed or the response has no queryScenes result.
    """
    valid_sorts = {"DATE", "TITLE", "CREATED_AT", "UPDATED_AT", "TRENDING"}
    if sort not in valid_sorts:
        log.LogWarning(f"Invalid sort field '{sort}', using DATE")
        sort = "DATE"

    if direction not in {"ASC", "DESC"}:
        log.LogWarning(f"Invalid direction '{direction}', using DESC")
        direction = "DESC"

    # Build filter input
    filter_input = {
        "page": page,
        "per_page": per_page,
        "sort": sort,
        "direction": direction
    }

    # Add performer filter
    if performer_ids:
        filter_input["performers"] = {
            "value": list(performer_ids),
            "modifier": "INCLUDES"
        }

    # Add studio filter
    if studio_ids:
        filter_input["studios"] = {
            "value": list(studio_ids),
            "modifier": "INCLUDES"
        }

    # Add tag filters (INCLUDES for positive, EXCLUDES for negative)
    # Note: StashDB doesn't support multiple tag filters in one query,
    # so we prioritize excludes if both are provided
    if excluded_tag_ids:
        filter_input["tags"] = {
            "value": list(excluded_tag_ids),
            "modifier": "EXCLUDES"
        }
    elif tag_ids:
        filter_input["tags"] = {
            "value": list(tag_ids),
            "modifier": "INCLUDES"
        }

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

    data = graphql_request_with_retry(
        url, query, {"input": filter_input}, api_key,
        plugin_settings=plugin_settings,
        operation_name=f"browse scenes page {page}"
    )
    return _scenes_page(data, page, per_page)


# ============================================================================
# Fingerprint Lookup (the fingerprint index)
# ============================================================================

# stash-box refuses more scenes per findScenesBySceneFingerprints call ("too many scenes")
FINGERPRINT_BATCH_SIZE = 40


def find_scenes_by_fingerprints(url, api_key, fingerprint_batches, plugin_settings=None):
    """Look up stash-box scenes by file fingerprints, for up to 40 local scenes per call.

    Args:
        url: stash-box GraphQL endpoint URL
        api_key: API key for authentication
        fingerprint_batches: one list per local scene of {"hash", "algorithm"}, where
            algorithm is MD5, OSHASH or PHASH (phash as Stash's hex string)
        plugin_settings: Plugin configuration (retries, Retry-After budget, timeout)

    Returns:
        One list per input scene, in input order, of the stash-box scenes its
        fingerprints match: [{"id", "duration"}]. The stash-box applies its own
        phash distance.

    Raises:
        ValueError: more than 40 scenes.
        StashBoxAPIError: the request failed, or the result doesn't line up with the input.
    """
    if len(fingerprint_batches) > FINGERPRINT_BATCH_SIZE:
        raise ValueError(f"At most {FINGERPRINT_BATCH_SIZE} scenes per fingerprint lookup, "
                         f"got {len(fingerprint_batches)}")

    query = """
    query FindScenesBySceneFingerprints($fingerprints: [[FingerprintQueryInput!]!]!) {
        findScenesBySceneFingerprints(fingerprints: $fingerprints) {
            id
            duration
        }
    }
    """
    variables = {"fingerprints": [
        [{"hash": fp["hash"], "algorithm": fp["algorithm"]} for fp in batch]
        for batch in fingerprint_batches
    ]}

    data = graphql_request_with_retry(
        url, query, variables, api_key,
        plugin_settings=plugin_settings,
        operation_name=f"fingerprint lookup of {len(fingerprint_batches)} scenes"
    )
    groups = data.get("findScenesBySceneFingerprints") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        raise StashBoxAPIError("The response has no findScenesBySceneFingerprints result")
    if len(groups) != len(fingerprint_batches):
        raise StashBoxAPIError(f"The fingerprint lookup returned {len(groups)} results "
                               f"for {len(fingerprint_batches)} scenes")
    return [
        [s for s in (group or []) if isinstance(s, dict) and s.get("id")]
        for group in groups
    ]

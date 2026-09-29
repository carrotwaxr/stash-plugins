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
import os
import re
import socket
import ssl
import time
import urllib.request
import urllib.error

import log


def _read_plugin_version():
    """Read the version from sceneMatcher.yml next to this module."""
    try:
        yml = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sceneMatcher.yml")
        with open(yml, encoding="utf-8") as f:
            m = re.search(r"^version:\s*(\S+)", f.read(), re.MULTILINE)
        if m:
            return m.group(1).strip("\"'")
    except OSError:
        pass
    return "unknown"


PLUGIN_VERSION = _read_plugin_version()
USER_AGENT = f"stash-plugins-sceneMatcher/{PLUGIN_VERSION}"


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

# Fewer scenes than this from the combined performer+studio query means the separate
# performer and studio queries are worth adding (see find_matches_thorough)
MIN_COMBINED_RESULTS_THRESHOLD = 10

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
    "max_pages_performer": 10,  # Max pages for performer and combined scene queries
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

    def __init__(self, message, status_code=None, retryable=False, is_auth_error=False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.is_auth_error = is_auth_error


# Longest Retry-After we will honour, and the most time one request may spend waiting.
MAX_RETRY_AFTER = 60.0
MAX_TOTAL_WAIT = 90.0

# Every stash-box request and wait in one operation (a phase-1 or phase-2 search) shares
# this budget, so the reply reaches the browser before it gives up at 120 s.
OPERATION_BUDGET_SECONDS = 100.0


class Deadline:
    """The time left for one operation. No wait may end past it, and request timeouts
    are cut to it."""

    def __init__(self, seconds=OPERATION_BUDGET_SECONDS):
        self.seconds = seconds
        self._end = time.monotonic() + seconds

    def remaining(self):
        return max(0.0, self._end - time.monotonic())

    def expired(self):
        return self.remaining() <= 0.0

    def fits(self, wait):
        """True when waiting `wait` seconds still ends before the deadline."""
        return wait < self.remaining()


class BudgetExceeded(StashBoxAPIError):
    """The operation's time budget ran out. What was collected before it is still good."""

    def __init__(self, seconds):
        super().__init__(
            f"stopped after {seconds:.0f} s; the stash-box is rate-limiting or slow to answer, "
            f"try again later",
            retryable=True)

_AUTH_MESSAGE_RE = re.compile(r"unauthori[sz]ed|forbidden|not authori[sz]ed", re.IGNORECASE)


def _is_auth_message(message):
    return bool(_AUTH_MESSAGE_RE.search(message or ""))


def _parse_retry_after(value):
    """Retry-After as seconds, or None when absent or not a number (HTTP-dates are ignored)."""
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _snippet(text, length=200):
    text = " ".join((text or "").split())
    return text[:length] + ("..." if len(text) > length else "")


def get_config(plugin_settings, key):
    """Get a config value, preferring plugin settings over defaults.

    Validates and coerces types to ensure safe values:
    - Integer settings: clamped to minimum of 1 (max_retries: minimum of 0)
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
            return max(0 if key == "max_retries" else 1, int(value))
        except (TypeError, ValueError):
            return DEFAULT_CONFIG.get(key, 1)

    if key in float_keys:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return DEFAULT_CONFIG.get(key, 0.0)

    return value


def graphql_request_with_retry(url, query, variables=None, api_key=None,
                                plugin_settings=None, operation_name=None, deadline=None):
    """
    Make a GraphQL request with retry logic for transient failures.

    Args:
        url: GraphQL endpoint URL
        query: GraphQL query string
        variables: Query variables dict
        api_key: API key for authentication
        plugin_settings: Plugin configuration for retry/timeout settings
        operation_name: Human-readable name for logging
        deadline: the operation's Deadline, shared by all its requests (a fresh
            OPERATION_BUDGET_SECONDS one when omitted). No request starts after it,
            no wait (Retry-After, rate-limit pause, backoff) runs past it, and the
            request timeout is cut to the time left.

    Returns:
        The response "data" dict (GraphQL errors alongside non-null data are
        logged and the data returned)

    Raises:
        BudgetExceeded: when the deadline is reached, or a wait would pass it.
        StashBoxAPIError: on HTTP/GraphQL/parse failures, auth failures
            (is_auth_error), rate limits that are too long to wait out, or
            after max retries.
    """
    if deadline is None:
        deadline = Deadline()
    max_retries = get_config(plugin_settings, "max_retries")
    initial_delay = get_config(plugin_settings, "initial_retry_delay")
    max_delay = get_config(plugin_settings, "max_retry_delay")
    backoff_multiplier = get_config(plugin_settings, "retry_backoff_multiplier")
    timeout = get_config(plugin_settings, "request_timeout")
    rate_limit_pause = get_config(plugin_settings, "rate_limit_pause")
    label = operation_name or "request"

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

    last_error = None
    delay = initial_delay
    waited = 0.0

    def out_of_time(wait):
        log.LogWarning(f"{label}: a {wait:.0f}s wait would pass the {deadline.seconds:.0f}s budget; stopping")
        return BudgetExceeded(deadline.seconds)

    def backoff(reason, attempt):
        nonlocal delay, waited
        if not deadline.fits(delay):
            raise out_of_time(delay)
        log.LogWarning(
            f"{reason} on {label}. Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
        )
        time.sleep(delay)
        waited += delay
        delay = min(delay * backoff_multiplier, max_delay)

    for attempt in range(max_retries + 1):
        if deadline.expired():
            raise BudgetExceeded(deadline.seconds)
        try:
            # At least a second, so a nearly spent budget still gets a real attempt
            request_timeout = min(timeout, max(1.0, deadline.remaining()))
            with urllib.request.urlopen(req, timeout=request_timeout, context=SSL_CONTEXT) as response:
                body = response.read().decode("utf-8", errors="replace")
            try:
                result = json.loads(body)
            except ValueError:
                log.LogError(f"Non-JSON response from {url}: {_snippet(body)}")
                raise StashBoxAPIError(
                    f"Unexpected non-JSON response from {url}: {_snippet(body)}")
            if not isinstance(result, dict):
                raise StashBoxAPIError(
                    f"Unexpected response from {url}: {_snippet(body)}")

            errors = result.get("errors")
            if errors:
                messages = [e.get("message", str(e)) if isinstance(e, dict) else str(e)
                            for e in errors]
                if result.get("data") is None:
                    joined = "; ".join(messages)
                    log.LogError(f"GraphQL errors on {label}: {joined}")
                    raise StashBoxAPIError(
                        f"GraphQL error: {joined}",
                        is_auth_error=_is_auth_message(joined))
                log.LogWarning(f"GraphQL errors on {label} (data returned): {messages}")

            return result.get("data")

        except StashBoxAPIError:
            raise

        except urllib.error.HTTPError as e:
            status_code = e.code
            last_error = e

            if status_code in (401, 403):
                log.LogError(f"HTTP {status_code} on {label}: authentication failed")
                raise StashBoxAPIError(
                    f"HTTP {status_code}: {e.reason or 'Unauthorized'}",
                    status_code=status_code,
                    is_auth_error=True
                )

            # Handle rate limiting specially
            if status_code == 429:
                retry_after = _parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
                wait = rate_limit_pause if retry_after is None else retry_after
                if attempt >= max_retries:
                    log.LogError("Rate limited (429) - max retries exceeded")
                    raise StashBoxAPIError(
                        f"Rate limited by the stash-box after {max_retries} retries",
                        status_code=429
                    )
                if wait > MAX_RETRY_AFTER or waited + wait > MAX_TOTAL_WAIT:
                    log.LogError(f"Rate limited (429) on {label}: would need to wait {wait:.0f}s")
                    raise StashBoxAPIError(
                        f"Rate limited by the stash-box (asked to wait {wait:.0f}s); try again later",
                        status_code=429
                    )
                if not deadline.fits(wait):
                    raise out_of_time(wait)
                log.LogWarning(
                    f"Rate limited (429) on {label}. "
                    f"Pausing {wait}s before retry {attempt + 1}/{max_retries}"
                )
                time.sleep(wait)
                waited += wait
                continue

            # Check if this is a retryable error
            if status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                backoff(f"HTTP {status_code}", attempt)
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

            # A bad certificate won't fix itself on retry
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                log.LogError(f"TLS certificate verification failed for {url}: {e.reason}")
                raise StashBoxAPIError(
                    f"TLS certificate verification failed for {url}: {e.reason}",
                    retryable=False
                )

            # Connection errors are often transient
            if attempt < max_retries:
                backoff(f"Connection error: {e.reason}", attempt)
                continue

            log.LogError(f"URL error after {max_retries} retries: {e.reason}")
            raise StashBoxAPIError(
                f"Connection failed: {e.reason}",
                retryable=True
            )

        except (TimeoutError, socket.timeout) as e:
            last_error = e
            if attempt < max_retries:
                backoff("Read timeout", attempt)
                continue
            log.LogError(f"Timed out after {max_retries} retries on {label}")
            raise StashBoxAPIError(
                f"Request timed out after {max_retries} retries",
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
                    plugin_settings=None, operation_name=None, max_pages=None, deadline=None):
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
        deadline: the operation's Deadline (see graphql_request_with_retry); paging
            stops, keeping what it has, when it is reached

    Returns:
        (items, total, error): every item collected, the server's total count,
        and the StashBoxAPIError that stopped a later page (None when clean).

    Raises:
        StashBoxAPIError: when the first page fails (nothing to return).
    """
    if deadline is None:
        deadline = Deadline()
    request_delay = get_config(plugin_settings, "request_delay")
    per_page = get_config(plugin_settings, "per_page")

    if max_pages is None:
        max_pages = get_config(plugin_settings, "max_pages_performer")

    all_items = []
    total = 0
    error = None
    page = 1

    while page <= max_pages:
        variables = build_variables_fn(page, per_page)

        try:
            data = graphql_request_with_retry(
                url, query, variables, api_key,
                plugin_settings=plugin_settings,
                operation_name=f"{operation_name or 'query'} (page {page})",
                deadline=deadline,
            )
        except StashBoxAPIError as e:
            if page == 1:
                raise
            # Later page: keep what we have and report the error
            log.LogWarning(
                f"Stopping pagination on {operation_name} at page {page} due to error: {e}. "
                f"Returning {len(all_items)} items collected so far."
            )
            error = e
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
            if not deadline.fits(request_delay):
                log.LogWarning(f"{operation_name}: time budget spent; returning {len(all_items)} items")
                error = BudgetExceeded(deadline.seconds)
                break
            time.sleep(request_delay)

    return all_items, total, error


class ScenesList(list):
    """A list of scenes that also says whether paging stopped short of the server's count.

    total: the stash-box count for the (last) query; truncated: fewer scenes came
    back than that count, because of the page cap.
    """
    total = None
    truncated = False


def _scenes_list(items, total):
    out = ScenesList(items)
    out.total = total
    out.truncated = bool(total) and len(items) < total
    return out


def _paged_scene_query(url, api_key, query, build_variables, extract, plugin_settings,
                       operation_name, max_pages, deadline=None):
    items, total, error = paginated_query(
        url, api_key, query, build_variables, extract,
        plugin_settings=plugin_settings, operation_name=operation_name, max_pages=max_pages,
        deadline=deadline)
    return items, total, error


def _extract_scenes(data):
    query_data = data.get("queryScenes", {})
    return query_data.get("scenes", []), query_data.get("count", 0)


def _performer_modifier_queries(url, api_key, query, performer_ids, studio_id,
                                plugin_settings, operation_name, max_pages, deadline=None):
    """Run a performer (optionally + studio) query. With 2+ performers: INCLUDES_ALL, then
    always INCLUDES too, merged and deduped by id (scoring decides the order). The
    all-performers query can find plenty without the right scene when one performer is
    wrongly linked; the any-performer query still finds it. INCLUDES_ALL runs first so
    scenes with every performer are kept even when the broader query hits its page cap.
    With 1 performer: INCLUDES only. Each query has its own page cap.

    Returns (scenes, error) with scenes a ScenesList; raises if the first page of the
    first query fails.
    """
    def make_builder(modifier):
        def build_variables(page, per_page):
            inp = {"performers": {"value": list(performer_ids), "modifier": modifier}}
            if studio_id:
                inp["studios"] = {"value": [studio_id], "modifier": "INCLUDES"}
            inp.update({"page": page, "per_page": per_page, "sort": "DATE", "direction": "DESC"})
            return {"input": inp}
        return build_variables

    modifiers = ["INCLUDES_ALL", "INCLUDES"] if len(performer_ids) > 1 else ["INCLUDES"]
    merged = {}
    error = None
    last = ([], 0)
    for n, modifier in enumerate(modifiers):
        try:
            items, total, error = _paged_scene_query(
                url, api_key, query, make_builder(modifier), _extract_scenes,
                plugin_settings, f"{operation_name} ({modifier})", max_pages, deadline)
        except StashBoxAPIError as e:
            if n == 0:
                raise
            log.LogWarning(f"{operation_name}: {modifier} query failed: {e}; keeping earlier results")
            error = e
            break
        for sc_ in items:
            merged.setdefault(sc_["id"], sc_)
        last = (items, total)
        if error:
            break
    out = _scenes_list(list(merged.values()), last[1])
    # Truncation is judged on the last query that ran, as that is the broadest one
    out.truncated = bool(last[1]) and len(last[0]) < last[1]
    return out, error


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


def query_scenes_by_studio(url, api_key, studio_id, plugin_settings=None, deadline=None):
    """Query StashDB for all scenes from a studio. Returns (scenes, error); raises if page 1 fails."""
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

    max_pages = get_config(plugin_settings, "max_pages_studio")
    items, total, error = _paged_scene_query(
        url, api_key, query, build_variables, _extract_scenes,
        plugin_settings, "scenes for studio", max_pages, deadline)
    scenes = _scenes_list(items, total)

    log.LogInfo(f"StashDB: Found {len(scenes)} scenes for studio")
    return scenes, error


def search_scenes_by_text(url, api_key, search_term, limit=25, plugin_settings=None, deadline=None):
    """Search StashDB scenes by text query. Raises StashBoxAPIError on failure."""
    if not search_term or len(search_term) < 3:
        return []

    query = f"""
    query SearchScene($term: String!, $limit: Int) {{
        searchScene(term: $term, limit: $limit) {{
            {SCENE_FIELDS}
        }}
    }}
    """

    data = graphql_request_with_retry(
        url, query, {"term": search_term, "limit": limit}, api_key,
        plugin_settings=plugin_settings,
        operation_name=f"text search '{search_term[:30]}...'",
        deadline=deadline,
    )
    scenes = ((data or {}).get("searchScene")) or []
    log.LogInfo(f"StashDB text search '{search_term[:30]}...': found {len(scenes)} scenes")
    return scenes


def _scene_query_text():
    return f"""
    query QueryScenes($input: SceneQueryInput!) {{
        queryScenes(input: $input) {{
            count
            scenes {{
                {SCENE_FIELDS}
            }}
        }}
    }}
    """


def query_scenes_combined(url, api_key, performer_ids, studio_id, plugin_settings=None, max_pages=None,
                          deadline=None):
    """Query StashDB for scenes by performers (all of them, then any of them) and studio.

    Returns (scenes, error); raises if page 1 fails. max_pages defaults to the
    stashbox_max_pages_performer setting.
    """
    if not performer_ids or not studio_id:
        return [], None
    if max_pages is None:
        max_pages = get_config(plugin_settings, "max_pages_performer")

    scenes, error = _performer_modifier_queries(
        url, api_key, _scene_query_text(), performer_ids, studio_id,
        plugin_settings, "combined performer+studio query", max_pages, deadline)
    log.LogInfo(f"StashDB combined (performer+studio): found {len(scenes)} scenes")
    return scenes, error


def query_scenes_by_performers(url, api_key, performer_ids, plugin_settings=None, max_pages=None,
                               deadline=None):
    """Query StashDB for scenes featuring the given performers (all of them, then any of them).

    Returns (scenes, error); raises if page 1 fails. max_pages defaults to the
    stashbox_max_pages_performer setting.
    """
    if not performer_ids:
        return [], None
    if max_pages is None:
        max_pages = get_config(plugin_settings, "max_pages_performer")

    scenes, error = _performer_modifier_queries(
        url, api_key, _scene_query_text(), performer_ids, None,
        plugin_settings, f"scenes for {len(performer_ids)} performers", max_pages, deadline)
    log.LogInfo(f"StashDB: Found {len(scenes)} scenes for {len(performer_ids)} performers")
    return scenes, error

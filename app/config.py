"""Runtime configuration via environment variables.

Override via Kubernetes secrets or docker-compose environment blocks
if needed.
"""

import os


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {name} must be a float, got {raw!r}"
        ) from exc


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {name} must be an integer, got {raw!r}"
        ) from exc


# --- Scoring ---

# Minimum weighted fuzzy score (0–1) for accepted candidate.
HIGH_CONFIDENCE_THRESHOLD: float = _float(
    "HIGH_CONFIDENCE_THRESHOLD", 0.75
)

# --- HTTP client ---

# Hard timeout (seconds) for each outbound API request.
HTTP_TIMEOUT: float = _float("HTTP_TIMEOUT", 5.0)

# Maximum number of retries on 429 / 5xx / timeout before giving up.
HTTP_MAX_RETRIES: int = _int("HTTP_MAX_RETRIES", 2)

# Base delay (seconds) for exponential backoff between retries.
HTTP_BACKOFF_BASE: float = _float("HTTP_BACKOFF_BASE", 1.0)

# --- Concurrency ---

# Maximum number of outbound API requests in flight at once.
MAX_CONCURRENT_REQUESTS: int = _int("MAX_CONCURRENT_REQUESTS", 3)

# --- Cache ---

# Maximum number of entries in the LRU cache.
CACHE_MAX_SIZE: int = _int("CACHE_MAX_SIZE", 3000)

"""Shared HTTP helpers."""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2
_BACKOFF_BASE = 1.0  # seconds


async def get_with_retry(
    client: httpx.AsyncClient, url: str, **kwargs
) -> httpx.Response:
    """GET with exponential backoff on 429 responses.

    Raises httpx.HTTPError after _MAX_RETRIES exhausted.
    """
    for attempt in range(_MAX_RETRIES + 1):
        resp = await client.get(url, **kwargs)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        delay = _BACKOFF_BASE * (2 ** attempt)
        logger.warning(
            "Rate limited by %s, retrying in %.1fs (attempt %d/%d)",
            url, delay, attempt + 1, _MAX_RETRIES,
        )
        await asyncio.sleep(delay)

    raise httpx.HTTPStatusError(
        f"Rate limit not resolved after {_MAX_RETRIES} retries",
        request=resp.request,
        response=resp,
    )

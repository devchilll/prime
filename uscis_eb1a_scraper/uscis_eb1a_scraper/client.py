"""A small, polite HTTP client around ``requests``.

Responsibilities:
    * send a browser-like User-Agent (USCIS 403s non-browser clients);
    * rate-limit requests to the USCIS host;
    * retry transient failures with exponential backoff;
    * honour the agent-proxy CA bundle if one is configured in the environment.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

from .config import (
    DEFAULT_DELAY_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
)

logger = logging.getLogger(__name__)

# Status codes worth retrying (transient server / rate-limit responses).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class HttpClient:
    """Thin wrapper over ``requests.Session`` with retries and rate limiting."""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        verify: Optional[str] = None,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_request_ts = 0.0

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "application/pdf;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

        # Respect a custom CA bundle when running behind an inspecting proxy.
        if verify is not None:
            self.session.verify = verify
        else:
            ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get(
                "SSL_CERT_FILE"
            )
            if ca_bundle:
                self.session.verify = ca_bundle

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        wait = self.delay_seconds - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def get(self, url: str, **kwargs) -> requests.Response:
        """GET *url* with throttling + exponential-backoff retries.

        Raises ``requests.HTTPError`` for non-retryable 4xx/5xx responses and
        ``requests.RequestException`` if all retries are exhausted.
        """
        kwargs.setdefault("timeout", self.timeout)
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                response = self.session.get(url, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("GET %s failed (attempt %d): %s", url, attempt + 1, exc)
            else:
                if response.status_code in _RETRYABLE_STATUS:
                    logger.warning(
                        "GET %s -> HTTP %d (attempt %d), will retry",
                        url,
                        response.status_code,
                        attempt + 1,
                    )
                    last_exc = requests.HTTPError(
                        f"HTTP {response.status_code} for {url}", response=response
                    )
                else:
                    response.raise_for_status()
                    return response

            if attempt < self.max_retries:
                backoff = 2 ** attempt  # 1s, 2s, 4s, 8s, ...
                time.sleep(backoff)

        assert last_exc is not None
        raise last_exc

    def get_text(self, url: str, **kwargs) -> str:
        return self.get(url, **kwargs).text

    def get_bytes(self, url: str, **kwargs) -> bytes:
        return self.get(url, **kwargs).content

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

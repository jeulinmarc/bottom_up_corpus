"""HTTP fetcher with SEC fair-access compliance.

Parallels ``cb_corpus.http``. Provides a small :class:`Fetcher` that:

* sends a declared, contact-carrying ``User-Agent`` (SEC requirement),
* throttles per host to stay at/under the SEC's 10 req/s ceiling,
* retries transient failures with exponential backoff,
* streams large bodies (complete submissions can be many MB).
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import requests

from .config import Config


class Fetcher:
    """Polite, throttled HTTP client shared across discovery and download.

    Not thread-safe: the per-host throttle state is unguarded, so a single
    ``Fetcher`` is meant to be reused *sequentially* within one thread. The
    pipeline is single-threaded, so no lock is needed; if concurrent crawling is
    ever added, give each worker its own ``Fetcher`` or guard ``_throttle``.
    """

    def __init__(self, config: Config | None = None, session: requests.Session | None = None):
        self.config = config or Config()
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.config.user_agent,
                "Accept-Encoding": "gzip, deflate",
            }
        )
        # Last-request timestamp per host, for spacing.
        self._last_request: dict[str, float] = {}

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        delay = self.config.min_delay_seconds
        if delay <= 0:
            return
        last = self._last_request.get(host)
        if last is not None:
            wait = delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def get(self, url: str, *, stream: bool = False, timeout: float | None = None) -> requests.Response:
        """GET ``url`` with throttling + retries; returns the response.

        Raises the last exception (or ``requests.HTTPError``) if all attempts
        fail. 429/503 responses are treated as retryable.
        """
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            self._throttle(url)
            try:
                resp = self.session.get(
                    url,
                    stream=stream,
                    timeout=timeout or self.config.timeout,
                )
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"{resp.status_code} for {url}", response=resp)
                resp.raise_for_status()
                return resp
            except (requests.RequestException, requests.HTTPError) as exc:
                last_exc = exc
                if attempt < self.config.max_retries:
                    time.sleep(2 ** attempt)  # 1s, 2s, 4s, ...
                    continue
                raise
        # Unreachable, but keeps type-checkers happy.
        assert last_exc is not None
        raise last_exc

    def get_text(self, url: str, *, timeout: float | None = None) -> str:
        """Fetch and decode a text body."""
        resp = self.get(url, timeout=timeout)
        resp.encoding = resp.encoding or "utf-8"
        return resp.text

    def get_json(self, url: str, *, timeout: float | None = None):
        """Fetch and parse a JSON body (used by data.sec.gov endpoints)."""
        return self.get(url, timeout=timeout).json()

    def download(self, url: str, dest, *, chunk_size: int = 1 << 16) -> int:
        """Stream ``url`` to ``dest`` (a path-like). Returns bytes written."""
        from pathlib import Path

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        resp = self.get(url, stream=True, timeout=self.config.download_timeout)
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    fh.write(chunk)
                    written += len(chunk)
        return written

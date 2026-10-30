"""HTTP access to Vinted.

Deliberately slow and well-behaved: this is a personal-use scraper. A plain browser
User-Agent is enough to read the public HTML pages (see docs/vinted-api-notes.md); we
do not impersonate TLS fingerprints or try to defeat the bot protection.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass

import httpx

from .parse import ItemDetail, Listing, parse_catalog, parse_item
from .search_url import SearchQuery

_LOGGER = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
# Served instead of the real page when Vinted's bot protection kicks in.
_CHALLENGE_MARKER = "/_fs-ch-"


class VintedBlocked(RuntimeError):
    """Vinted served a bot-protection challenge instead of the page."""


@dataclass
class ClientSettings:
    min_delay: float = 6.0
    max_delay: float = 12.0
    timeout: float = 30.0
    max_retries: int = 4
    locale: str = "fr-FR,fr;q=0.9"
    # Scales the retry backoff; tests set it to 0 to avoid real waiting.
    backoff_factor: float = 1.0


class VintedClient:
    """Fetches catalog pages and item pages, one at a time."""

    def __init__(
        self,
        settings: ClientSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings or ClientSettings()
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._session_ready = False
        # Vinted is read one request at a time, never in parallel.
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> VintedClient:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT, "Accept-Language": self._settings.locale},
            follow_redirects=True,
            timeout=self._settings.timeout,
            transport=self._transport,
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._session_ready = False

    async def _ensure_session(self, domain: str) -> None:
        """Hit the home page once to pick up the session cookies."""
        if self._session_ready:
            return
        assert self._client is not None
        response = await self._client.get(f"https://{domain}/")
        response.raise_for_status()
        self._session_ready = True
        _LOGGER.debug("Vinted session established on %s", domain)

    async def _sleep(self) -> None:
        await asyncio.sleep(random.uniform(self._settings.min_delay, self._settings.max_delay))

    async def _get(self, url: str, *, allow_404: bool = False) -> httpx.Response:
        """GET with pacing, retries and bot-protection detection."""
        assert self._client is not None, "VintedClient must be used as an async context manager"
        last_error: Exception | None = None

        for attempt in range(self._settings.max_retries):
            if attempt:
                # Exponential backoff, jittered.
                await asyncio.sleep(
                    2**attempt * random.uniform(1.5, 3.0) * self._settings.backoff_factor
                )
            await self._sleep()
            try:
                response = await self._client.get(url)
            except httpx.HTTPError as err:
                last_error = err
                _LOGGER.warning("Erreur réseau sur %s : %s", url, err)
                continue

            if response.status_code == 404 and allow_404:
                return response
            if response.status_code in (403, 429) or _CHALLENGE_MARKER in response.text[:2000]:
                last_error = VintedBlocked(f"Vinted a bloqué la requête ({response.status_code})")
                _LOGGER.warning("Blocage Vinted sur %s (HTTP %s)", url, response.status_code)
                # The session cookies may be stale; take them again.
                self._session_ready = False
                continue
            if response.status_code >= 500:
                last_error = RuntimeError(f"HTTP {response.status_code}")
                continue

            response.raise_for_status()
            return response

        raise last_error or RuntimeError(f"Échec de la requête {url}")

    async def fetch_catalog(self, query: SearchQuery, max_pages: int = 3) -> list[Listing]:
        """Read the first `max_pages` result pages of a search."""
        async with self._lock:
            await self._ensure_session(query.domain)
            seen: dict[str, Listing] = {}
            for page in range(1, max_pages + 1):
                response = await self._get(query.page_url(page))
                listings = parse_catalog(response.text)
                if not listings:
                    _LOGGER.info("Page %s sans article, arrêt de la pagination", page)
                    break
                new = [item for item in listings if item.item_id not in seen]
                for item in listings:
                    seen.setdefault(item.item_id, item)
                _LOGGER.debug("Page %s : %s articles (%s nouveaux)", page, len(listings), len(new))
                if not new:
                    break
            return list(seen.values())

    async def fetch_item(
        self, item_id: str, domain: str = "www.vinted.fr", url: str | None = None
    ) -> ItemDetail:
        """Read one item page to find out whether it is still for sale.

        The stored URL carries the slug: without it Vinted answers 307 and we would
        spend two requests per item against the rate limit.
        """
        async with self._lock:
            await self._ensure_session(domain)
            response = await self._get(url or f"https://{domain}/items/{item_id}", allow_404=True)
            return parse_item(item_id, response.text, response.status_code)

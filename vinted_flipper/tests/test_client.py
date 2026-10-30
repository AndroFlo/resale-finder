"""Client behaviour (pacing, retries, blocking) against a stubbed transport."""

import httpx
import pytest
from conftest import fixture
from vinted_flipper.vinted.client import ClientSettings, VintedBlocked, VintedClient
from vinted_flipper.vinted.search_url import parse_search_url

# No real waiting in tests.
FAST = ClientSettings(min_delay=0, max_delay=0, max_retries=3, backoff_factor=0)


def client_with(handler, settings=FAST):
    return VintedClient(settings, transport=httpx.MockTransport(handler))


async def test_session_is_established_before_the_first_page():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(200, text="home")
        return httpx.Response(200, text=fixture("catalog_sample.html"))

    async with client_with(handler) as client:
        listings = await client.fetch_catalog(parse_search_url(
            "https://www.vinted.fr/catalog?search_text=nike"), max_pages=1)

    assert seen[0] == "https://www.vinted.fr/"
    assert len(listings) == 6


async def test_pagination_stops_when_a_page_adds_nothing():
    pages = []

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text="home")
        pages.append(request.url.params.get("page"))
        return httpx.Response(200, text=fixture("catalog_sample.html"))

    async with client_with(handler) as client:
        listings = await client.fetch_catalog(parse_search_url(
            "https://www.vinted.fr/catalog?search_text=nike"), max_pages=5)

    # Same items on page 2 -> no point asking for pages 3 to 5.
    assert pages == ["1", "2"]
    assert len(listings) == 6


async def test_rate_limit_is_retried_then_succeeds():
    attempts = []

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text="home")
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, text=fixture("catalog_sample.html"))

    async with client_with(handler) as client:
        listings = await client.fetch_catalog(parse_search_url(
            "https://www.vinted.fr/catalog?search_text=nike"), max_pages=1)

    assert len(attempts) == 3
    assert len(listings) == 6


async def test_persistent_blocking_is_reported():
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text="home")
        return httpx.Response(403, text="blocked")

    async with client_with(handler) as client:
        with pytest.raises(VintedBlocked):
            await client.fetch_catalog(parse_search_url(
                "https://www.vinted.fr/catalog?search_text=nike"), max_pages=1)


async def test_bot_challenge_page_is_not_parsed_as_results():
    """A 200 response can still be a challenge page; it must not look like an empty search."""
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text="home")
        return httpx.Response(200, text='<html><link href="/_fs-ch-abc/assets/x.css"></html>')

    async with client_with(handler) as client:
        with pytest.raises(VintedBlocked):
            await client.fetch_catalog(parse_search_url(
                "https://www.vinted.fr/catalog?search_text=nike"), max_pages=1)


async def test_item_is_fetched_by_its_full_url_to_avoid_a_redirect():
    """Without the slug Vinted answers 307, costing a second request each time."""
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(200, text="home")
        return httpx.Response(200, text=fixture("item_live1.html"))

    async with client_with(handler) as client:
        detail = await client.fetch_item(
            "10593234882", url="https://www.vinted.fr/items/10593234882-echarpe-beige"
        )

    assert seen[-1].endswith("-echarpe-beige")
    assert detail.status == "active"


async def test_missing_item_is_removed_not_an_error():
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text="home")
        return httpx.Response(404, text="not found")

    async with client_with(handler) as client:
        assert (await client.fetch_item("1")).status == "removed"

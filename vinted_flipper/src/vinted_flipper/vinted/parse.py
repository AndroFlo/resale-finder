"""Parsing of Vinted HTML pages.

Vinted's JSON API (/api/v2/catalog/items) is behind bot protection, so we read the
regular HTML pages instead. See docs/vinted-api-notes.md for the reverse engineering
notes behind every pattern used here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# <a href="/items/123-slug" data-testid="product-item-id-123--overlay-link" title="...">
_LISTING_RE = re.compile(
    r'<a href="(?P<href>/items/(?P<id>\d+)-[^"?]*)[^"]*"'
    r'[^>]*?data-testid="product-item-id-(?P=id)--overlay-link"'
    r'[^>]*?title="(?P<title>[^"]*)"'
)
_FAVOURITES_RE = re.compile(
    r'aria-label="[^"]*?par (?P<count>\d+) utilisateur[^"]*"\s*'
    r'data-testid="product-item-id-(?P<id>\d+)--favourite"'
)
_PRICE_RE = re.compile(r"^[\d\s.,]+\s*€$")
# A price at the end of a field list: "…, 1 234,50 €, 1 300,00 €"
_TRAILING_PRICE_RE = re.compile(r"(?<=,\s)\d[\d\s \xa0.,]*\s*€")

# Fields inside the title attribute, by prefix (the free-text title may contain commas).
_TITLE_FIELDS = {"brand": "Marque:", "size": "Taille:", "condition": "État:"}

# Escaped JSON inside the embedded Next.js flight data: \"can_buy\":true
_FLAG_RE_CACHE: dict[str, re.Pattern[str]] = {}

ACTIVE = "active"
SOLD = "sold"
RESERVED = "reserved"
HIDDEN = "hidden"
REMOVED = "removed"


def _unescape(text: str) -> str:
    return text.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'").replace(
        "&lt;", "<"
    ).replace("&gt;", ">")


def _price(raw: str) -> float | None:
    """'1 234,50 €' -> 1234.50"""
    cleaned = raw.replace("€", "").replace(" ", "").replace("\xa0", "").strip()
    cleaned = cleaned.replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


@dataclass
class Listing:
    """One item as seen in a catalog page."""

    item_id: str
    url: str
    title: str
    brand: str | None = None
    size: str | None = None
    condition: str | None = None
    price: float | None = None
    total_price: float | None = None
    favourites: int | None = None
    photo_url: str | None = None


def parse_catalog(html: str) -> list[Listing]:
    """Extract every item of a catalog results page."""
    listings: dict[str, Listing] = {}
    for match in _LISTING_RE.finditer(html):
        item_id = match.group("id")
        title_attr = _unescape(match.group("title"))

        # Prices come last and may themselves contain a comma ("1 234,50 €"), so pull
        # them out of the whole string before splitting the rest on commas.
        prices = [m.group(0) for m in _TRAILING_PRICE_RE.finditer(title_attr)]
        remainder = _TRAILING_PRICE_RE.sub("", title_attr).rstrip(" ,")

        parts = [p.strip() for p in remainder.split(",")]
        values = {
            name: next(
                (p.split(":", 1)[1].strip() for p in parts if p.startswith(prefix)), None
            )
            for name, prefix in _TITLE_FIELDS.items()
        }
        # The free-text title is whatever is left once the labelled fields are removed.
        labelled = tuple(_TITLE_FIELDS.values())
        head = [p for p in parts if p and not p.startswith(labelled)]
        listings[item_id] = Listing(
            item_id=item_id,
            url="https://www.vinted.fr" + match.group("href"),
            title=", ".join(head).strip(),
            price=_price(prices[0]) if prices else None,
            total_price=_price(prices[1]) if len(prices) > 1 else None,
            **values,
        )

    for match in _FAVOURITES_RE.finditer(html):
        listing = listings.get(match.group("id"))
        if listing is not None:
            listing.favourites = int(match.group("count"))

    for item_id, listing in listings.items():
        photo = re.search(
            rf'data-testid="product-item-id-{item_id}--image--img"[^>]*?src="([^"]+)"', html
        ) or re.search(
            rf'<img src="([^"]+)"[^>]*?data-testid="product-item-id-{item_id}--image--img"', html
        )
        if photo:
            listing.photo_url = _unescape(photo.group(1))

    return list(listings.values())


def _flag(html: str, name: str) -> bool | None:
    """Read a boolean from the escaped JSON embedded in an item page."""
    pattern = _FLAG_RE_CACHE.get(name)
    if pattern is None:
        pattern = _FLAG_RE_CACHE[name] = re.compile(rf'\\"{name}\\":(true|false)')
    match = pattern.search(html)
    return None if match is None else match.group(1) == "true"


def _amount(html: str, key: str) -> float | None:
    match = re.search(rf'\\"{key}\\":\{{\\"amount\\":\\"([\d.]+)\\"', html)
    return float(match.group(1)) if match else None


def _fee(html: str, fee_type: str) -> float | None:
    """Buyer protection / shipping fee, read from its typed block."""
    match = re.search(
        rf'\\"finalPrice\\":\{{\\"amount\\":\\"([\d.]+)\\"[^}}]*\}}(?:(?!\\"type\\").){{0,400}}?'
        rf'\\"type\\":\\"{fee_type}\\"',
        html,
    )
    if match:
        return float(match.group(1))
    match = re.search(
        rf'\\"type\\":\\"{fee_type}\\"(?:(?!\\"type\\").){{0,400}}?'
        rf'\\"finalPrice\\":\{{\\"amount\\":\\"([\d.]+)\\"',
        html,
    )
    return float(match.group(1)) if match else None


def _breadcrumbs(html: str) -> list[str]:
    """Category path of the item itself.

    Only the `breadcrumbs` block describes the item: the other /catalog/ links on the
    page belong to the navigation menu and would otherwise leak in.
    """
    block = re.search(r'\\"breadcrumbs\\":\[(.*?)\](?:,\\"|\})', html, re.S)
    if not block:
        return []
    return re.findall(r'\\"title\\":\\"([^"\\]+)\\"', block.group(1))


@dataclass
class ItemDetail:
    """State and pricing of a single item, read from its own page."""

    item_id: str
    status: str
    price: float | None = None
    total_price: float | None = None
    buyer_protection_fee: float | None = None
    shipping_fee: float | None = None
    brand_id: str | None = None
    catalog_id: str | None = None
    favourites: int | None = None
    breadcrumbs: list[str] = field(default_factory=list)


def parse_item(item_id: str, html: str, http_status: int = 200) -> ItemDetail:
    """Determine whether an item is still for sale.

    A sold item keeps returning HTTP 200; `can_buy` is what actually flips to false.
    `is_reserved` / `is_hidden` are the other reasons it can be false, and must not be
    counted as sales.
    """
    if http_status == 404:
        return ItemDetail(item_id=item_id, status=REMOVED)

    can_buy = _flag(html, "can_buy")
    if can_buy is None:
        status = REMOVED if http_status >= 400 else ACTIVE
    elif can_buy:
        status = ACTIVE
    elif _flag(html, "is_reserved"):
        status = RESERVED
    elif _flag(html, "is_hidden"):
        status = HIDDEN
    else:
        status = SOLD

    brand = re.search(r'\\"brand_id\\":\\"(\d+)\\"', html)
    catalog = re.search(r'\\"catalog_id\\":\\"(\d+)\\"', html)
    favourites = re.search(r'\\"favourite_count\\":(\d+)', html)
    crumbs = _breadcrumbs(html)

    return ItemDetail(
        item_id=item_id,
        status=status,
        price=_amount(html, "originalAskingAmount"),
        total_price=_amount(html, "totalAmount"),
        buyer_protection_fee=_fee(html, "buyer_protection"),
        shipping_fee=_fee(html, "shipping"),
        brand_id=brand.group(1) if brand else None,
        catalog_id=catalog.group(1) if catalog else None,
        favourites=int(favourites.group(1)) if favourites else None,
        breadcrumbs=crumbs,
    )

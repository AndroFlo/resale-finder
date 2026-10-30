"""Parsing is checked against real pages captured from vinted.fr."""

import pytest
from conftest import fixture

from vinted_flipper.vinted.parse import ACTIVE, REMOVED, SOLD, parse_catalog, parse_item


@pytest.fixture(scope="module")
def listings():
    return parse_catalog(fixture("catalog_sample.html"))


def test_every_listing_is_extracted(listings):
    assert len(listings) == 6
    assert all(item.item_id.isdigit() for item in listings)
    assert all(item.url.startswith("https://www.vinted.fr/items/") for item in listings)


def test_listings_carry_the_fields_we_price_on(listings):
    for item in listings:
        assert item.brand, f"marque manquante sur {item.item_id}"
        assert item.price and item.price > 0
        assert item.total_price and item.total_price >= item.price
        assert item.condition
        assert item.photo_url and item.photo_url.startswith("https://")


def test_title_keeps_its_commas():
    html = (
        '<a href="/items/42-x" data-testid="product-item-id-42--overlay-link" '
        'title="Robe bleue, rouge et verte, Marque: Zara, Taille: M, '
        'État: Bon état, 10.00 €, 11.20 €"><div></div></a>'
    )
    (item,) = parse_catalog(html)
    assert item.title == "Robe bleue, rouge et verte"
    assert item.brand == "Zara"
    assert item.size == "M"
    assert item.price == 10.0
    assert item.total_price == 11.20


def test_french_price_format():
    html = (
        '<a href="/items/7-y" data-testid="product-item-id-7--overlay-link" '
        'title="Manteau, Marque: Burberry, État: Bon état, 1 234,50 €, 1 300,00 €">'
        "<div></div></a>"
    )
    (item,) = parse_catalog(html)
    assert item.price == 1234.50
    assert item.total_price == 1300.00


def test_active_item_is_for_sale():
    detail = parse_item("10593234882", fixture("item_live1.html"))
    assert detail.status == ACTIVE
    assert detail.price == 55.0
    assert detail.total_price == 58.45
    assert detail.buyer_protection_fee == 3.45
    assert detail.shipping_fee == 5.55
    assert detail.brand_id == "88"


def test_sold_item_is_detected_despite_http_200():
    detail = parse_item("10581479031", fixture("item_sold2.html"))
    assert detail.status == SOLD


def test_breadcrumbs_describe_the_item_not_the_menu():
    detail = parse_item("10593234882", fixture("item_live1.html"))
    assert detail.breadcrumbs[:2] == ["Maison", "Animaux"]


def test_404_means_removed_not_sold():
    assert parse_item("1", "<html>nope</html>", 404).status == REMOVED


@pytest.mark.parametrize(
    "flags, expected",
    [
        ('\\"can_buy\\":true', ACTIVE),
        ('\\"can_buy\\":false,\\"is_reserved\\":false,\\"is_hidden\\":false', SOLD),
        ('\\"can_buy\\":false,\\"is_reserved\\":true', "reserved"),
        ('\\"can_buy\\":false,\\"is_reserved\\":false,\\"is_hidden\\":true', "hidden"),
    ],
)
def test_reserved_and_hidden_are_not_sales(flags, expected):
    assert parse_item("1", flags).status == expected

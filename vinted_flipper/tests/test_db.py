"""Item lifecycle: seen, gone from results, then confirmed sold or removed."""

import pytest
from vinted_flipper.db import Database
from vinted_flipper.vinted.parse import ItemDetail, Listing


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def search_id(db):
    return db.add_search("Test", "https://www.vinted.fr/catalog?x=1", "www.vinted.fr", {"x": ["1"]})


def listing(item_id="1", price=20.0, favourites=None):
    return Listing(
        item_id=item_id,
        url=f"https://www.vinted.fr/items/{item_id}-x",
        title="Chemise",
        brand="Ralph Lauren",
        size="M",
        condition="Très bon état",
        price=price,
        total_price=price * 1.05 + 0.70,
        favourites=favourites,
        photo_url="https://images1.vinted.net/x.webp",
    )


def test_first_sighting_is_new_then_not(db, search_id):
    assert db.upsert_listing(listing(), search_id) is True
    assert db.upsert_listing(listing(), search_id) is False


def test_item_is_kept_when_it_leaves_the_results(db, search_id):
    db.upsert_listing(listing("1"), search_id)
    db.upsert_listing(listing("2"), search_id)

    assert db.mark_missing(search_id, ["1"]) == 1

    # Nothing is deleted: the vanished item is exactly what we need to check later.
    assert db.get_item("2") is not None
    assert db.get_item("2")["missing_since"] is not None
    assert db.get_item("1")["missing_since"] is None


def test_missing_items_are_checked_first(db, search_id):
    db.upsert_listing(listing("1"), search_id)
    db.upsert_listing(listing("2"), search_id)
    db.mark_missing(search_id, ["1"])

    queue = db.items_to_check(10, "2099-01-01T00:00:00+00:00")
    assert queue[0]["item_id"] == "2"


def test_item_reappearing_clears_the_missing_flag(db, search_id):
    db.upsert_listing(listing("1"), search_id)
    db.mark_missing(search_id, [])
    assert db.get_item("1")["missing_since"] is not None

    db.upsert_listing(listing("1"), search_id)
    assert db.get_item("1")["missing_since"] is None


def test_sale_is_recorded_with_its_date(db, search_id):
    db.upsert_listing(listing("1"), search_id)
    detail = ItemDetail(item_id="1", status="sold", price=20.0, total_price=21.70,
                        shipping_fee=4.0, brand_id="88", catalog_id="1805",
                        breadcrumbs=["Hommes", "Vêtements", "Chemises"])

    assert db.apply_detail(detail) == "sold"
    item = db.get_item("1")
    assert item["status"] == "sold"
    assert item["sold_at"] is not None
    assert item["shipping_fee"] == 4.0
    assert item["category"] == "Hommes > Vêtements > Chemises"
    assert len(db.sold_items()) == 1


def test_removed_item_is_not_counted_as_a_sale(db, search_id):
    db.upsert_listing(listing("1"), search_id)
    db.apply_detail(ItemDetail(item_id="1", status="removed"))

    assert db.get_item("1")["status"] == "removed"
    assert db.sold_items() == []


def test_price_changes_are_recorded(db, search_id):
    db.upsert_listing(listing("1", price=20.0), search_id)
    db.upsert_listing(listing("1", price=20.0), search_id)   # unchanged, no new row
    db.upsert_listing(listing("1", price=15.0), search_id)   # drop

    history = db.price_history("1")
    assert [row["price"] for row in history] == [20.0, 15.0]


def test_deals_disappear_once_the_item_is_gone(db, search_id):
    db.upsert_listing(listing("1"), search_id)
    db.save_deal({"item_id": "1", "search_id": search_id, "cost": 21.7, "resale": 50.0,
                  "margin": 28.3, "margin_pct": 130.0, "comparables": 5,
                  "confidence": "moyenne", "median_days": 7})
    assert len(db.list_deals()) == 1

    db.apply_detail(ItemDetail(item_id="1", status="sold"))
    db.purge_stale_deals()
    assert db.list_deals() == []


def test_a_deal_is_only_notified_once(db, search_id):
    db.upsert_listing(listing("1"), search_id)
    db.save_deal({"item_id": "1", "search_id": search_id, "cost": 21.7, "resale": 50.0,
                  "margin": 28.3, "margin_pct": 130.0, "comparables": 5,
                  "confidence": "moyenne", "median_days": 7})

    assert len(db.unnotified_deals()) == 1
    db.mark_notified(["1"])
    assert db.unnotified_deals() == []


def test_deleting_a_search_keeps_the_observed_items(db, search_id):
    db.upsert_listing(listing("1"), search_id)
    db.delete_search(search_id)

    # The sales history stays usable even if the search is removed.
    assert db.get_item("1") is not None

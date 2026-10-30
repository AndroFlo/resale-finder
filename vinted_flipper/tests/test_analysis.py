"""Pricing logic, on synthetic data where the right answer is known."""

from datetime import UTC, datetime, timedelta

import pytest
from vinted_flipper.analysis import (
    DealCriteria,
    build_clusters,
    buy_cost,
    cluster_key,
    evaluate,
)


def make_item(**overrides):
    base = {
        "item_id": "1",
        "brand": "Ralph Lauren",
        "catalog_id": "1805",
        "category": "Hommes > Chemises",
        "size": "M",
        "condition": "Très bon état",
        "price": 20.0,
        "total_price": 21.70,
        "shipping_fee": None,
        "first_seen": (datetime.now(UTC) - timedelta(days=10)).isoformat(),
        "sold_at": None,
        "status": "active",
    }
    base.update(overrides)
    return base


def sold_at(days_ago: int, price: float, **overrides):
    now = datetime.now(UTC)
    return make_item(
        price=price,
        total_price=price * 1.05 + 0.70,
        status="sold",
        first_seen=(now - timedelta(days=days_ago + 7)).isoformat(),
        sold_at=(now - timedelta(days=days_ago)).isoformat(),
        **overrides,
    )


def test_cost_uses_the_total_vinted_charges():
    # Vinted already includes buyer protection in the total; shipping adds to it.
    assert buy_cost(make_item(total_price=21.70, shipping_fee=4.0)) == pytest.approx(25.70)


def test_cost_is_estimated_when_only_the_asking_price_is_known():
    cost = buy_cost(make_item(price=20.0, total_price=None))
    assert cost == pytest.approx(21.70)


def test_median_of_sales_is_the_resale_price():
    sales = [sold_at(5, p) for p in (40.0, 45.0, 50.0, 55.0, 60.0)]
    clusters = build_clusters(sales, sales)
    stats = clusters[cluster_key(sales[0])]
    assert stats.sales == 5
    assert stats.median_price == 50.0
    assert stats.median_days_to_sell == pytest.approx(7, abs=0.1)


def test_a_cheap_item_in_a_proven_cluster_is_a_deal():
    sales = [sold_at(5, p) for p in (45.0, 50.0, 55.0, 50.0, 48.0)]
    clusters = build_clusters(sales, sales)
    cheap = make_item(item_id="99", price=20.0, total_price=21.70)

    deal = evaluate(cheap, clusters, DealCriteria())
    assert deal is not None
    assert deal["resale"] == 50.0
    assert deal["cost"] == pytest.approx(21.70)
    assert deal["margin"] == pytest.approx(28.30)
    assert deal["comparables"] == 5


def test_no_deal_without_enough_comparable_sales():
    sales = [sold_at(5, 50.0), sold_at(6, 52.0)]
    clusters = build_clusters(sales, sales)
    assert evaluate(make_item(price=5.0, total_price=5.95), clusters, DealCriteria()) is None


def test_withdrawn_items_never_count_as_sales():
    sales = [sold_at(5, p) for p in (45.0, 50.0, 55.0)]
    removed = [make_item(item_id=f"r{i}", status="removed", price=200.0) for i in range(5)]
    clusters = build_clusters(sales, sales + removed)
    assert clusters[cluster_key(sales[0])].median_price == 50.0


def test_thin_margin_is_rejected():
    sales = [sold_at(5, p) for p in (22.0, 23.0, 24.0, 22.5, 23.5)]
    clusters = build_clusters(sales, sales)
    assert evaluate(make_item(price=20.0), clusters, DealCriteria()) is None


def test_slow_selling_cluster_is_rejected():
    sales = [sold_at(200, p, ) for p in (45.0, 50.0, 55.0, 52.0)]
    for sale in sales:  # took a very long time to sell
        sale["first_seen"] = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    clusters = build_clusters(sales, sales)
    criteria = DealCriteria(max_days_to_sell=45.0)
    assert evaluate(make_item(price=10.0), clusters, criteria) is None


def test_falls_back_to_a_looser_cluster_when_size_is_too_specific():
    sales = [sold_at(5, p, size=s) for p, s in [(50.0, "S"), (52.0, "L"), (48.0, "XL")]]
    precise = build_clusters(sales, sales, with_size=True)
    loose = build_clusters(sales, sales, with_size=False)

    item = make_item(size="M", price=15.0, total_price=16.45)
    assert evaluate(item, precise, DealCriteria()) is None
    deal = evaluate(item, precise, DealCriteria(), fallback=loose)
    assert deal is not None and deal["comparables"] == 3


def test_different_brands_are_not_comparable():
    assert cluster_key(make_item(brand="Nike")) != cluster_key(make_item(brand="Adidas"))

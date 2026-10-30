"""Turn observed sales into resale estimates and deals.

The resale price of an item is the median price of comparable items that actually sold.
Items that were merely withdrawn never count as sales.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Below this many observed sales a cluster cannot price anything.
MIN_COMPARABLES = 3
CONFIDENCE_THRESHOLDS = ((12, "élevée"), (6, "moyenne"), (MIN_COMPARABLES, "faible"))


def _parse(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def cluster_key(item: Any, *, with_size: bool = True) -> tuple[str, ...]:
    """Group comparable items: same brand, category, size and condition.

    `catalog_id` is only known once an item page has been read, so we fall back to the
    brand alone for items we have only seen in results.
    """
    key = [_norm(item["brand"]), _norm(item["catalog_id"]), _norm(item["condition"])]
    if with_size:
        key.append(_norm(item["size"]))
    return tuple(key)


@dataclass
class ClusterStats:
    """What comparable sales tell us about a group of items."""

    key: tuple[str, ...]
    label: str
    sales: int
    median_price: float
    p25: float
    p75: float
    median_days_to_sell: float | None
    sell_through: float
    confidence: str

    @property
    def is_usable(self) -> bool:
        return self.sales >= MIN_COMPARABLES


def _confidence(sales: int) -> str:
    for threshold, label in CONFIDENCE_THRESHOLDS:
        if sales >= threshold:
            return label
    return "insuffisante"


def _label(item: Any) -> str:
    parts = [item["brand"] or "?", item["category"] or item["catalog_id"] or "—"]
    if item["size"]:
        parts.append(f"T. {item['size']}")
    if item["condition"]:
        parts.append(item["condition"])
    return " · ".join(str(p) for p in parts)


def build_clusters(
    sold: list[Any], all_items: list[Any], *, with_size: bool = True
) -> dict[tuple[str, ...], ClusterStats]:
    """Compute per-cluster statistics from sold items."""
    groups: dict[tuple[str, ...], list[Any]] = {}
    for item in sold:
        if item["price"] is None:
            continue
        groups.setdefault(cluster_key(item, with_size=with_size), []).append(item)

    # Denominator of the sell-through rate: everything we ever tracked in that cluster.
    totals: dict[tuple[str, ...], int] = {}
    for item in all_items:
        key = cluster_key(item, with_size=with_size)
        totals[key] = totals.get(key, 0) + 1

    stats: dict[tuple[str, ...], ClusterStats] = {}
    for key, items in groups.items():
        prices = sorted(float(item["price"]) for item in items)
        quantiles = (
            statistics.quantiles(prices, n=4) if len(prices) >= 4 else [prices[0], prices[-1]]
        )
        durations = []
        for item in items:
            first, sold_at = _parse(item["first_seen"]), _parse(item["sold_at"])
            if first and sold_at and sold_at >= first:
                durations.append((sold_at - first).total_seconds() / 86400)

        stats[key] = ClusterStats(
            key=key,
            label=_label(items[0]),
            sales=len(prices),
            median_price=statistics.median(prices),
            p25=quantiles[0],
            p75=quantiles[-1],
            median_days_to_sell=statistics.median(durations) if durations else None,
            sell_through=len(prices) / max(totals.get(key, len(prices)), 1),
            confidence=_confidence(len(prices)),
        )
    return stats


@dataclass
class DealCriteria:
    """Thresholds an opportunity must clear."""

    min_margin: float = 8.0
    min_margin_pct: float = 25.0
    max_days_to_sell: float | None = 45.0
    min_comparables: int = MIN_COMPARABLES


def buy_cost(item: Any) -> float | None:
    """What the item actually costs us, fees included.

    Vinted shows the buyer-protection-inclusive total in results; shipping is only known
    once we have read the item page.
    """
    total = item["total_price"]
    if total is None:
        price = item["price"]
        if price is None:
            return None
        # Buyer protection, as charged by Vinted: 0.70 € + 5 %.
        total = float(price) * 1.05 + 0.70
    shipping = item["shipping_fee"] if "shipping_fee" in item.keys() else None
    return float(total) + float(shipping or 0)


def evaluate(
    item: Any,
    clusters: dict[tuple[str, ...], ClusterStats],
    criteria: DealCriteria,
    fallback: dict[tuple[str, ...], ClusterStats] | None = None,
) -> dict[str, Any] | None:
    """Score one active item against its cluster; None if it is not a deal.

    Resale is the cluster median: that is what comparable items really sold for, and we
    would be selling at the same place.
    """
    stats = clusters.get(cluster_key(item))
    if stats is None or stats.sales < criteria.min_comparables:
        # Too thin with size; try the looser grouping.
        stats = (fallback or {}).get(cluster_key(item, with_size=False))
    if stats is None or stats.sales < criteria.min_comparables:
        return None

    cost = buy_cost(item)
    if cost is None or cost <= 0:
        return None

    resale = stats.median_price
    margin = resale - cost
    margin_pct = margin / cost * 100

    if margin < criteria.min_margin or margin_pct < criteria.min_margin_pct:
        return None
    if (
        criteria.max_days_to_sell is not None
        and stats.median_days_to_sell is not None
        and stats.median_days_to_sell > criteria.max_days_to_sell
    ):
        return None

    return {
        "item_id": item["item_id"],
        "cost": round(cost, 2),
        "resale": round(resale, 2),
        "margin": round(margin, 2),
        "margin_pct": round(margin_pct, 1),
        "comparables": stats.sales,
        "confidence": stats.confidence,
        "median_days": stats.median_days_to_sell,
        "cluster_label": stats.label,
    }


def find_deals(
    db: Any, criteria: DealCriteria, window_days: int = 90
) -> list[dict[str, Any]]:
    """Re-score every active item against recent sales."""
    since = (datetime.now(UTC) - timedelta(days=window_days)).isoformat(timespec="seconds")
    sold = db.sold_items(since=since)
    if not sold:
        _LOGGER.info("Aucune vente observée : estimation impossible pour l'instant")
        return []

    tracked = db.browse_items(limit=100_000)
    precise = build_clusters(sold, tracked, with_size=True)
    loose = build_clusters(sold, tracked, with_size=False)

    deals = []
    for item in db.active_items():
        deal = evaluate(item, precise, criteria, fallback=loose)
        if deal:
            deals.append(deal)

    deals.sort(key=lambda d: d["margin"], reverse=True)
    _LOGGER.info("%s bonnes affaires sur %s ventes observées", len(deals), len(sold))
    return deals


def market_overview(db: Any, window_days: int = 90) -> list[ClusterStats]:
    """Cluster statistics for the market tab."""
    since = (datetime.now(UTC) - timedelta(days=window_days)).isoformat(timespec="seconds")
    clusters = build_clusters(db.sold_items(since=since), db.browse_items(limit=100_000))
    return sorted(clusters.values(), key=lambda c: c.sales, reverse=True)

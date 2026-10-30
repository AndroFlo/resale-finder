"""The hourly cycle: scan searches, then check item pages."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from .analysis import DealCriteria, find_deals
from .config import Settings
from .db import Database, utcnow
from .vinted.client import ClientSettings, VintedClient
from .vinted.search_url import SearchQuery

_LOGGER = logging.getLogger(__name__)


class Collector:
    """Runs scans and keeps item statuses up to date."""

    def __init__(self, db: Database, settings: Settings, notifier=None) -> None:
        self._db = db
        self._settings = settings
        self._notifier = notifier
        self.last_run: str | None = None
        self.last_error: str | None = None

    def _client(self) -> VintedClient:
        return VintedClient(
            ClientSettings(
                min_delay=self._settings.min_delay, max_delay=self._settings.max_delay
            )
        )

    def _criteria(self, search=None) -> DealCriteria:
        """Add-on defaults, overridden per search when set."""
        min_margin = self._settings.min_margin
        min_pct = self._settings.min_margin_pct
        if search is not None:
            if search["min_margin"] is not None:
                min_margin = search["min_margin"]
            if search["min_margin_pct"] is not None:
                min_pct = search["min_margin_pct"]
        return DealCriteria(
            min_margin=min_margin,
            min_margin_pct=min_pct,
            max_days_to_sell=self._settings.max_days_to_sell,
        )

    async def scan_search(self, search_id: int, client: VintedClient | None = None) -> dict:
        """Fetch one search's result pages and record every listing."""
        search = self._db.get_search(search_id)
        if search is None:
            raise LookupError(f"Recherche {search_id} introuvable")

        query = SearchQuery(domain=search["domain"], params=json.loads(search["params"]))
        scan_id = self._db.start_scan(search_id)
        owns_client = client is None
        try:
            if owns_client:
                client = self._client()
                await client.__aenter__()
            listings = await client.fetch_catalog(query, max_pages=search["max_pages"])
            new_items = sum(self._db.upsert_listing(item, search_id) for item in listings)
            gone = self._db.mark_missing(search_id, [item.item_id for item in listings])
            self._db.update_search(search_id, last_scan_at=utcnow())
            self._db.finish_scan(scan_id, found=len(listings), new_items=new_items)
            _LOGGER.info(
                "Recherche « %s » : %s articles (%s nouveaux, %s disparus)",
                search["name"], len(listings), new_items, gone,
            )
            return {"found": len(listings), "new": new_items, "missing": gone}
        except Exception as err:
            self._db.finish_scan(scan_id, error=str(err))
            _LOGGER.error("Échec du scan « %s » : %s", search["name"], err)
            raise
        finally:
            if owns_client and client is not None:
                await client.__aexit__(None, None, None)

    async def check_items(self, client: VintedClient, limit: int | None = None) -> dict:
        """Visit item pages to detect sales, gone-from-results first."""
        limit = limit or self._settings.max_checks_per_cycle
        stale_before = (
            datetime.now(UTC) - timedelta(hours=self._settings.recheck_hours)
        ).isoformat(timespec="seconds")
        rows = self._db.items_to_check(limit, stale_before)

        outcome = {"checked": 0, "sold": 0, "removed": 0}
        for row in rows:
            try:
                detail = await client.fetch_item(row["item_id"], url=row["url"])
            except Exception as err:
                _LOGGER.warning("Vérification impossible de %s : %s", row["item_id"], err)
                continue
            status = self._db.apply_detail(detail)
            outcome["checked"] += 1
            if status in outcome:
                outcome[status] += 1
        if outcome["checked"]:
            _LOGGER.info(
                "%s fiches vérifiées : %s vendues, %s retirées",
                outcome["checked"], outcome["sold"], outcome["removed"],
            )
        return outcome

    def refresh_deals(self) -> list[dict]:
        """Re-score active items and store the resulting deals."""
        self._db.purge_stale_deals()
        deals = find_deals(self._db, self._criteria())
        for deal in deals:
            self._db.save_deal(deal)
        return deals

    async def run_cycle(self) -> dict:
        """One full pass: every enabled search, then item checks, then scoring."""
        summary = {"searches": 0, "found": 0, "new": 0, "checked": 0, "sold": 0, "deals": 0}
        searches = self._db.list_searches(only_enabled=True)
        if not searches:
            _LOGGER.info("Aucune recherche active")

        self.last_error = None
        async with self._client() as client:
            for search in searches:
                try:
                    result = await self.scan_search(search["id"], client=client)
                    summary["searches"] += 1
                    summary["found"] += result["found"]
                    summary["new"] += result["new"]
                except Exception as err:
                    self.last_error = str(err)

            if searches:
                checks = await self.check_items(client)
                summary["checked"] = checks["checked"]
                summary["sold"] = checks["sold"]

        deals = self.refresh_deals()
        summary["deals"] = len(deals)
        self.last_run = utcnow()

        if self._notifier is not None:
            await self._notifier.publish(summary, self._db)
        return summary

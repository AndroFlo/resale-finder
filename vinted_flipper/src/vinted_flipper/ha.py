"""Talk to Home Assistant through the Supervisor proxy.

Outside Home Assistant (local development) every call is a no-op, so the rest of the
app does not need to care.
"""

from __future__ import annotations

import logging
import os

import httpx

_LOGGER = logging.getLogger(__name__)

CORE_API = "http://supervisor/core/api"
DEALS_ENTITY = "sensor.vinted_flipper_deals"
SCAN_ENTITY = "sensor.vinted_flipper_last_scan"
DEAL_EVENT = "vinted_deal_found"


class HomeAssistant:
    """Publishes sensors, fires events and sends notifications."""

    def __init__(self, notify_service: str = "", notify_min_margin: float = 15.0) -> None:
        self._token = os.environ.get("SUPERVISOR_TOKEN", "")
        self._notify_service = notify_service.strip()
        self._notify_min_margin = notify_min_margin

    @property
    def available(self) -> bool:
        return bool(self._token)

    async def _post(self, path: str, payload: dict) -> bool:
        if not self.available:
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{CORE_API}/{path}",
                    headers={"Authorization": f"Bearer {self._token}"},
                    json=payload,
                )
            if response.status_code >= 400:
                _LOGGER.warning("Home Assistant a refusé %s : HTTP %s", path, response.status_code)
                return False
            return True
        except httpx.HTTPError as err:
            _LOGGER.warning("Appel Home Assistant impossible (%s) : %s", path, err)
            return False

    async def publish(self, summary: dict, db) -> None:
        """Refresh sensors and notify about deals we have not announced yet."""
        if not self.available:
            return

        deals = db.list_deals()
        top = [
            {
                "titre": deal["title"],
                "marque": deal["brand"],
                "marge": deal["margin"],
                "marge_pct": deal["margin_pct"],
                "cout": deal["cost"],
                "revente": deal["resale"],
                "url": deal["url"],
            }
            for deal in deals[:5]
        ]
        await self._post(
            f"states/{DEALS_ENTITY}",
            {
                "state": len(deals),
                "attributes": {
                    "friendly_name": "Vinted bonnes affaires",
                    "unit_of_measurement": "annonces",
                    "icon": "mdi:tag-search",
                    "top": top,
                    "marge_max": deals[0]["margin"] if deals else 0,
                },
            },
        )
        await self._post(
            f"states/{SCAN_ENTITY}",
            {
                "state": summary.get("found", 0),
                "attributes": {
                    "friendly_name": "Vinted dernier scan",
                    "unit_of_measurement": "annonces",
                    "icon": "mdi:magnify-scan",
                    **{k: v for k, v in summary.items() if k != "found"},
                },
            },
        )

        notified = []
        for deal in db.unnotified_deals():
            if deal["margin"] < self._notify_min_margin:
                continue
            await self._post(f"events/{DEAL_EVENT}", dict(deal))
            if self._notify_service and await self._send_notification(deal):
                notified.append(deal["item_id"])
            elif not self._notify_service:
                notified.append(deal["item_id"])
        if notified:
            db.mark_notified(notified)
            _LOGGER.info("%s bonne(s) affaire(s) notifiée(s)", len(notified))

    async def _send_notification(self, deal) -> bool:
        title = deal["title"] or "Article Vinted"
        message = (
            f"{deal['brand'] or ''} — achat {deal['cost']:.2f} €, "
            f"revente ≈ {deal['resale']:.2f} € (+{deal['margin']:.2f} €, "
            f"{deal['margin_pct']:.0f} %)"
        ).strip(" —")
        payload = {
            "title": f"Vinted : {title}"[:80],
            "message": message,
            "data": {
                "url": deal["url"],
                "clickAction": deal["url"],
                "actions": [{"action": "URI", "title": "Voir l'annonce", "uri": deal["url"]}],
            },
        }
        if deal["photo_url"]:
            payload["data"]["image"] = deal["photo_url"]
        return await self._post(f"services/notify/{self._notify_service}", payload)

    async def send_test_notification(self) -> bool:
        """Used by the 'test notification' button in the UI."""
        if not self.available or not self._notify_service:
            return False
        return await self._post(
            f"services/notify/{self._notify_service}",
            {
                "title": "Vinted Flipper",
                "message": "Les notifications fonctionnent.",
            },
        )

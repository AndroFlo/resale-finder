"""Entry point: web UI plus the recurring scan."""

from __future__ import annotations

import logging

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .collector import Collector
from .config import load_settings
from .db import Database
from .ha import HomeAssistant
from .web.app import create_app

_LOGGER = logging.getLogger(__name__)


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    db = Database(settings.db_path)
    ha = HomeAssistant(settings.notify_service, settings.notify_min_margin)
    collector = Collector(db, settings, notifier=ha)
    app = create_app(db, collector, settings, ha)

    scheduler = AsyncIOScheduler()

    @app.on_event("startup")
    async def _start() -> None:
        scheduler.add_job(
            collector.run_cycle,
            "interval",
            minutes=settings.scan_interval_minutes,
            id="scan",
            # Skip rather than pile up if a cycle runs long.
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        _LOGGER.info(
            "Vinted Flipper démarré — scan toutes les %s min, base %s",
            settings.scan_interval_minutes,
            settings.db_path,
        )
        if not ha.available:
            _LOGGER.info("Home Assistant non détecté : capteurs et notifications désactivés")

    @app.on_event("shutdown")
    async def _stop() -> None:
        scheduler.shutdown(wait=False)
        db.close()

    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()

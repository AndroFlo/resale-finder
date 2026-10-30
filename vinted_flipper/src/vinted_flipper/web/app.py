"""Web UI, served inside Home Assistant through Ingress."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..analysis import market_overview
from ..vinted.search_url import InvalidSearchUrl, parse_search_url

_LOGGER = logging.getLogger(__name__)
_HERE = Path(__file__).parent

STATUS_LABELS = {
    "active": "En vente",
    "sold": "Vendu",
    "removed": "Retiré",
    "reserved": "Réservé",
    "hidden": "Masqué",
}


def _format_datetime(value: str | None) -> str:
    """ISO timestamp -> '30/10 à 11h57', in local time."""
    if not value:
        return "jamais"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo:
        parsed = parsed.astimezone()
    return parsed.strftime("%d/%m à %Hh%M")


def _ingress_base(request: Request) -> str:
    """Home Assistant serves the add-on under a path prefix; links must keep it."""
    return request.headers.get("X-Ingress-Path", "")


def create_app(db, collector, settings, ha=None) -> FastAPI:
    app = FastAPI(title="Vinted Flipper", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    templates.env.filters["money"] = lambda v: "—" if v is None else f"{float(v):.2f} €"
    templates.env.filters["status_label"] = lambda v: STATUS_LABELS.get(v, v)
    templates.env.filters["datetime"] = _format_datetime

    def render(request: Request, template: str, **context) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name=template,
            context={
                "base": _ingress_base(request),
                "counts": db.counts_by_status(),
                "deal_count": len(db.list_deals()),
                **context,
            },
        )

    # Only one scan at a time, whether scheduled or triggered from the UI.
    scan_lock = asyncio.Lock()

    async def run_cycle_guarded():
        if scan_lock.locked():
            _LOGGER.info("Scan déjà en cours, demande ignorée")
            return
        async with scan_lock:
            try:
                await collector.run_cycle()
            except Exception:
                _LOGGER.exception("Le scan a échoué")

    @app.get("/", response_class=HTMLResponse)
    async def deals_page(request: Request, min_margin: float = 0.0):
        deals = [d for d in db.list_deals() if d["margin"] >= min_margin]
        return render(
            request,
            "deals.html",
            deals=deals,
            min_margin=min_margin,
            scanning=scan_lock.locked(),
            last_run=collector.last_run,
            last_error=collector.last_error,
            has_searches=bool(db.list_searches()),
            sold_count=db.counts_by_status().get("sold", 0),
        )

    @app.get("/searches", response_class=HTMLResponse)
    async def searches_page(request: Request, error: str = "", added: str = ""):
        return render(
            request,
            "searches.html",
            searches=db.list_searches(),
            error=error,
            added=added,
            scanning=scan_lock.locked(),
        )

    @app.post("/searches")
    async def add_search(
        request: Request,
        url: str = Form(...),
        name: str = Form(""),
        max_pages: int = Form(3),
        min_margin: str = Form(""),
        min_margin_pct: str = Form(""),
    ):
        base = _ingress_base(request)
        try:
            query = parse_search_url(url)
        except InvalidSearchUrl as err:
            return RedirectResponse(f"{base}/searches?error={err}", status_code=303)

        label = name.strip() or query.suggested_name()
        search_id = db.add_search(
            name=label,
            url=url.strip(),
            domain=query.domain,
            params=query.params,
            max_pages=max(1, min(int(max_pages), 20)),
            min_margin=float(min_margin) if min_margin.strip() else None,
            min_margin_pct=float(min_margin_pct) if min_margin_pct.strip() else None,
        )
        asyncio.create_task(run_cycle_guarded())
        return RedirectResponse(f"{base}/searches?added={search_id}", status_code=303)

    @app.post("/searches/{search_id}/toggle")
    async def toggle_search(request: Request, search_id: int):
        search = db.get_search(search_id)
        if search is not None:
            db.update_search(search_id, enabled=0 if search["enabled"] else 1)
        return RedirectResponse(f"{_ingress_base(request)}/searches", status_code=303)

    @app.post("/searches/{search_id}/delete")
    async def remove_search(request: Request, search_id: int):
        db.delete_search(search_id)
        return RedirectResponse(f"{_ingress_base(request)}/searches", status_code=303)

    @app.post("/scan")
    async def trigger_scan(request: Request):
        asyncio.create_task(run_cycle_guarded())
        return RedirectResponse(f"{_ingress_base(request)}/", status_code=303)

    @app.get("/preview")
    async def preview_url(url: str):
        """Live feedback on the pasted URL, before it is saved."""
        try:
            return JSONResponse({"ok": True, "filters": parse_search_url(url).describe()})
        except InvalidSearchUrl as err:
            return JSONResponse({"ok": False, "error": str(err)})

    @app.get("/items", response_class=HTMLResponse)
    async def items_page(
        request: Request, status: str = "", search_id: str = "", page: int = 1
    ):
        page = max(1, page)
        per_page = 100
        items = db.browse_items(
            status=status or None,
            search_id=int(search_id) if search_id.strip() else None,
            limit=per_page,
            offset=(page - 1) * per_page,
        )
        return render(
            request,
            "items.html",
            items=items,
            status=status,
            search_id=search_id,
            page=page,
            has_next=len(items) == per_page,
            searches=db.list_searches(),
        )

    @app.get("/market", response_class=HTMLResponse)
    async def market_page(request: Request):
        return render(request, "market.html", clusters=market_overview(db))

    @app.get("/item/{item_id}", response_class=HTMLResponse)
    async def item_page(request: Request, item_id: str):
        item = db.get_item(item_id)
        if item is None:
            return RedirectResponse(f"{_ingress_base(request)}/items", status_code=303)
        deal = next((d for d in db.list_deals() if d["item_id"] == item_id), None)
        return render(
            request,
            "item.html",
            item=item,
            history=db.price_history(item_id),
            deal=deal,
        )

    @app.post("/item/{item_id}/dismiss")
    async def dismiss(request: Request, item_id: str):
        db.dismiss_deal(item_id)
        return RedirectResponse(f"{_ingress_base(request)}/", status_code=303)

    @app.get("/logs", response_class=HTMLResponse)
    async def logs_page(request: Request):
        return render(
            request,
            "logs.html",
            scans=db.recent_scans(),
            settings=settings,
            ha_available=bool(ha and ha.available),
        )

    @app.post("/test-notification")
    async def test_notification(request: Request):
        sent = bool(ha and await ha.send_test_notification())
        return RedirectResponse(
            f"{_ingress_base(request)}/logs?notify={'ok' if sent else 'ko'}", status_code=303
        )

    @app.get("/api/deals")
    async def api_deals():
        return JSONResponse([dict(deal) for deal in db.list_deals()])

    return app

"""SQLite storage.

Items are never deleted: an item that leaves the search results is what lets us tell a
sale from a withdrawal later on.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    domain        TEXT    NOT NULL DEFAULT 'www.vinted.fr',
    params        TEXT    NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    max_pages     INTEGER NOT NULL DEFAULT 3,
    min_margin    REAL,
    min_margin_pct REAL,
    created_at    TEXT    NOT NULL,
    last_scan_at  TEXT
);

CREATE TABLE IF NOT EXISTS items (
    item_id       TEXT    PRIMARY KEY,
    url           TEXT    NOT NULL,
    title         TEXT,
    brand         TEXT,
    brand_id      TEXT,
    size          TEXT,
    condition     TEXT,
    catalog_id    TEXT,
    category      TEXT,
    price         REAL,
    total_price   REAL,
    shipping_fee  REAL,
    photo_url     TEXT,
    favourites    INTEGER,
    status        TEXT    NOT NULL DEFAULT 'active',
    first_seen    TEXT    NOT NULL,
    last_seen     TEXT    NOT NULL,
    last_checked  TEXT,
    sold_at       TEXT,
    missing_since TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_status  ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_cluster ON items(brand, catalog_id, size, condition);
CREATE INDEX IF NOT EXISTS idx_items_checked ON items(last_checked);

CREATE TABLE IF NOT EXISTS item_searches (
    item_id   TEXT NOT NULL,
    search_id INTEGER NOT NULL,
    PRIMARY KEY (item_id, search_id),
    FOREIGN KEY (item_id)   REFERENCES items(item_id)   ON DELETE CASCADE,
    FOREIGN KEY (search_id) REFERENCES searches(id)     ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL,
    price       REAL,
    total_price REAL,
    favourites  INTEGER,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_price_item ON price_history(item_id);

CREATE TABLE IF NOT EXISTS deals (
    item_id      TEXT PRIMARY KEY,
    search_id    INTEGER,
    cost         REAL NOT NULL,
    resale       REAL NOT NULL,
    margin       REAL NOT NULL,
    margin_pct   REAL NOT NULL,
    comparables  INTEGER NOT NULL,
    confidence   TEXT NOT NULL,
    median_days  REAL,
    found_at     TEXT NOT NULL,
    notified     INTEGER NOT NULL DEFAULT 0,
    dismissed    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id   INTEGER,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    found       INTEGER NOT NULL DEFAULT 0,
    new_items   INTEGER NOT NULL DEFAULT 0,
    checked     INTEGER NOT NULL DEFAULT 0,
    sold        INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    """Thin wrapper over sqlite3 with the queries the app needs."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- searches ---------------------------------------------------------

    def add_search(
        self,
        name: str,
        url: str,
        domain: str,
        params: dict[str, list[str]],
        max_pages: int = 3,
        min_margin: float | None = None,
        min_margin_pct: float | None = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO searches(name, url, domain, params, max_pages, min_margin, "
            "min_margin_pct, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (name, url, domain, json.dumps(params), max_pages, min_margin, min_margin_pct,
             utcnow()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def list_searches(self, only_enabled: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM searches"
        if only_enabled:
            sql += " WHERE enabled = 1"
        return list(self._conn.execute(sql + " ORDER BY id"))

    def get_search(self, search_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM searches WHERE id = ?", (search_id,)
        ).fetchone()

    def update_search(self, search_id: int, **fields: Any) -> None:
        allowed = {"name", "enabled", "max_pages", "min_margin", "min_margin_pct", "last_scan_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE searches SET {assignments} WHERE id = ?",
            (*updates.values(), search_id),
        )
        self._conn.commit()

    def delete_search(self, search_id: int) -> None:
        self._conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))
        self._conn.commit()

    # --- items ------------------------------------------------------------

    def upsert_listing(self, listing: Any, search_id: int) -> bool:
        """Record an item seen in results. Returns True if it is new to us."""
        now = utcnow()
        existing = self._conn.execute(
            "SELECT price, total_price, favourites FROM items WHERE item_id = ?",
            (listing.item_id,),
        ).fetchone()

        if existing is None:
            self._conn.execute(
                "INSERT INTO items(item_id, url, title, brand, size, condition, price, "
                "total_price, photo_url, favourites, first_seen, last_seen) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (listing.item_id, listing.url, listing.title, listing.brand, listing.size,
                 listing.condition, listing.price, listing.total_price, listing.photo_url,
                 listing.favourites, now, now),
            )
        else:
            self._conn.execute(
                "UPDATE items SET last_seen = ?, price = ?, total_price = ?, "
                "favourites = COALESCE(?, favourites), missing_since = NULL, "
                "photo_url = COALESCE(?, photo_url) WHERE item_id = ?",
                (now, listing.price, listing.total_price, listing.favourites,
                 listing.photo_url, listing.item_id),
            )

        changed = existing is not None and (
            existing["price"] != listing.price or existing["favourites"] != listing.favourites
        )
        if existing is None or changed:
            self._conn.execute(
                "INSERT INTO price_history(item_id, price, total_price, favourites, recorded_at) "
                "VALUES(?,?,?,?,?)",
                (listing.item_id, listing.price, listing.total_price, listing.favourites, now),
            )

        self._conn.execute(
            "INSERT OR IGNORE INTO item_searches(item_id, search_id) VALUES(?,?)",
            (listing.item_id, search_id),
        )
        self._conn.commit()
        return existing is None

    def mark_missing(self, search_id: int, seen_ids: Iterable[str]) -> int:
        """Flag tracked items of this search that no longer appear in the results."""
        seen = set(seen_ids)
        rows = self._conn.execute(
            "SELECT i.item_id FROM items i JOIN item_searches s ON s.item_id = i.item_id "
            "WHERE s.search_id = ? AND i.status = 'active' AND i.missing_since IS NULL",
            (search_id,),
        ).fetchall()
        missing = [row["item_id"] for row in rows if row["item_id"] not in seen]
        if missing:
            now = utcnow()
            self._conn.executemany(
                "UPDATE items SET missing_since = ? WHERE item_id = ?",
                [(now, item_id) for item_id in missing],
            )
            self._conn.commit()
        return len(missing)

    def items_to_check(self, limit: int, stale_before: str) -> list[sqlite3.Row]:
        """Items whose page should be visited, gone-from-results first."""
        return list(
            self._conn.execute(
                "SELECT item_id, url FROM items WHERE status = 'active' "
                "AND (last_checked IS NULL OR last_checked < ?) "
                "ORDER BY (missing_since IS NULL), COALESCE(last_checked, first_seen) "
                "LIMIT ?",
                (stale_before, limit),
            )
        )

    def apply_detail(self, detail: Any) -> str:
        """Store the outcome of an item-page check and return the new status."""
        now = utcnow()
        sold_at = now if detail.status == "sold" else None
        category = " > ".join(detail.breadcrumbs[:4]) if detail.breadcrumbs else None
        self._conn.execute(
            "UPDATE items SET status = ?, last_checked = ?, "
            "sold_at = COALESCE(sold_at, ?), "
            "price = COALESCE(?, price), total_price = COALESCE(?, total_price), "
            "shipping_fee = COALESCE(?, shipping_fee), brand_id = COALESCE(?, brand_id), "
            "catalog_id = COALESCE(?, catalog_id), category = COALESCE(?, category), "
            "favourites = COALESCE(?, favourites) WHERE item_id = ?",
            (detail.status, now, sold_at, detail.price, detail.total_price,
             detail.shipping_fee, detail.brand_id, detail.catalog_id, category,
             detail.favourites, detail.item_id),
        )
        self._conn.commit()
        return detail.status

    def get_item(self, item_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM items WHERE item_id = ?", (item_id,)
        ).fetchone()

    def price_history(self, item_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM price_history WHERE item_id = ? ORDER BY recorded_at",
                (item_id,),
            )
        )

    def sold_items(self, since: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM items WHERE status = 'sold'"
        args: list[Any] = []
        if since:
            sql += " AND sold_at >= ?"
            args.append(since)
        return list(self._conn.execute(sql, args))

    def active_items(self, search_id: int | None = None) -> list[sqlite3.Row]:
        if search_id is None:
            return list(self._conn.execute("SELECT * FROM items WHERE status = 'active'"))
        return list(
            self._conn.execute(
                "SELECT i.* FROM items i JOIN item_searches s ON s.item_id = i.item_id "
                "WHERE s.search_id = ? AND i.status = 'active'",
                (search_id,),
            )
        )

    def browse_items(
        self,
        status: str | None = None,
        search_id: int | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        sql = "SELECT DISTINCT i.* FROM items i"
        args: list[Any] = []
        if search_id is not None:
            sql += " JOIN item_searches s ON s.item_id = i.item_id AND s.search_id = ?"
            args.append(search_id)
        if status:
            sql += " WHERE i.status = ?"
            args.append(status)
        sql += " ORDER BY i.last_seen DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        return list(self._conn.execute(sql, args))

    def counts_by_status(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) n FROM items GROUP BY status")
        return {row["status"]: row["n"] for row in rows}

    # --- deals ------------------------------------------------------------

    def save_deal(self, deal: dict[str, Any]) -> bool:
        """Record a deal. Returns True if it was not already known."""
        existing = self._conn.execute(
            "SELECT 1 FROM deals WHERE item_id = ?", (deal["item_id"],)
        ).fetchone()
        self._conn.execute(
            "INSERT INTO deals(item_id, search_id, cost, resale, margin, margin_pct, "
            "comparables, confidence, median_days, found_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(item_id) DO UPDATE SET cost=excluded.cost, resale=excluded.resale, "
            "margin=excluded.margin, margin_pct=excluded.margin_pct, "
            "comparables=excluded.comparables, confidence=excluded.confidence, "
            "median_days=excluded.median_days",
            (deal["item_id"], deal.get("search_id"), deal["cost"], deal["resale"],
             deal["margin"], deal["margin_pct"], deal["comparables"], deal["confidence"],
             deal.get("median_days"), utcnow()),
        )
        self._conn.commit()
        return existing is None

    def list_deals(self, include_dismissed: bool = False) -> list[sqlite3.Row]:
        sql = (
            "SELECT d.*, i.title, i.brand, i.size, i.condition, i.url, i.photo_url, "
            "i.price, i.total_price, i.status FROM deals d JOIN items i USING(item_id) "
            "WHERE i.status = 'active'"
        )
        if not include_dismissed:
            sql += " AND d.dismissed = 0"
        return list(self._conn.execute(sql + " ORDER BY d.margin DESC"))

    def unnotified_deals(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT d.*, i.title, i.brand, i.url, i.photo_url FROM deals d "
                "JOIN items i USING(item_id) "
                "WHERE d.notified = 0 AND d.dismissed = 0 AND i.status = 'active' "
                "ORDER BY d.margin DESC"
            )
        )

    def mark_notified(self, item_ids: Iterable[str]) -> None:
        self._conn.executemany(
            "UPDATE deals SET notified = 1 WHERE item_id = ?", [(i,) for i in item_ids]
        )
        self._conn.commit()

    def dismiss_deal(self, item_id: str) -> None:
        self._conn.execute("UPDATE deals SET dismissed = 1 WHERE item_id = ?", (item_id,))
        self._conn.commit()

    def purge_stale_deals(self) -> None:
        """Drop deals whose item is no longer for sale."""
        self._conn.execute(
            "DELETE FROM deals WHERE item_id IN "
            "(SELECT item_id FROM items WHERE status != 'active')"
        )
        self._conn.commit()

    # --- scans ------------------------------------------------------------

    def start_scan(self, search_id: int | None) -> int:
        cur = self._conn.execute(
            "INSERT INTO scans(search_id, started_at) VALUES(?,?)", (search_id, utcnow())
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_scan(self, scan_id: int, **fields: Any) -> None:
        allowed = {"found", "new_items", "checked", "sold", "error"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        assignments = "".join(f", {k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE scans SET finished_at = ?{assignments} WHERE id = ?",
            (utcnow(), *updates.values(), scan_id),
        )
        self._conn.commit()

    def recent_scans(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT s.*, se.name AS search_name FROM scans s "
                "LEFT JOIN searches se ON se.id = s.search_id "
                "ORDER BY s.id DESC LIMIT ?",
                (limit,),
            )
        )

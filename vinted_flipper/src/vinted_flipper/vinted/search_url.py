"""Understand the catalog URLs the user pastes from their browser."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

# Filters we know how to describe back to the user, in display order.
_FILTER_LABELS = {
    "search_text": "Recherche",
    "catalog[]": "Catégories",
    "brand_ids[]": "Marques",
    "size_ids[]": "Tailles",
    "status_ids[]": "États",
    "color_ids[]": "Couleurs",
    "material_ids[]": "Matières",
    "price_from": "Prix min",
    "price_to": "Prix max",
    "currency": "Devise",
    "order": "Tri",
}

# Vinted uses both spellings depending on where the link comes from.
_ALIASES = {
    "catalog_ids[]": "catalog[]",
    "brand_id[]": "brand_ids[]",
    "size_id[]": "size_ids[]",
    "status[]": "status_ids[]",
}

_CONDITIONS = {
    "6": "Neuf avec étiquette",
    "1": "Neuf sans étiquette",
    "2": "Très bon état",
    "3": "Bon état",
    "4": "Satisfaisant",
}


class InvalidSearchUrl(ValueError):
    """The pasted URL is not a Vinted catalog search."""


@dataclass
class SearchQuery:
    """A parsed Vinted catalog URL."""

    domain: str
    params: dict[str, list[str]]

    def page_url(self, page: int = 1) -> str:
        """Rebuild the catalog URL for a given results page."""
        pairs = [(key, value) for key, values in self.params.items() for value in values]
        pairs.append(("page", str(page)))
        return f"https://{self.domain}/catalog?{urlencode(pairs)}"

    def describe(self) -> dict[str, str]:
        """Human-readable filters, so the user can check what we understood."""
        out: dict[str, str] = {}
        for key, label in _FILTER_LABELS.items():
            values = self.params.get(key)
            if not values:
                continue
            if key == "status_ids[]":
                values = [_CONDITIONS.get(v, v) for v in values]
            out[label] = ", ".join(values)
        return out

    def suggested_name(self) -> str:
        """A short label for a search the user did not name.

        Keeps what reads well (the keywords, the price cap) and leaves out the numeric
        ids and the sort order, which mean nothing to a human.
        """
        bits: list[str] = []
        if text := self.params.get("search_text"):
            bits.append(" ".join(text))
        if conditions := self.params.get("status_ids[]"):
            bits.append(", ".join(_CONDITIONS.get(c, c) for c in conditions))
        for key, template in (("price_to", "≤ {} €"), ("price_from", "≥ {} €")):
            if value := self.params.get(key):
                bits.append(template.format(value[0]))
        if not bits:
            counts = [
                f"{len(self.params[key])} {label.lower()}"
                for key, label in (("brand_ids[]", "marques"), ("catalog[]", "catégories"))
                if self.params.get(key)
            ]
            bits = counts or ["Recherche Vinted"]
        return " · ".join(bits)[:60]


def parse_search_url(url: str) -> SearchQuery:
    """Turn a pasted catalog URL into something we can page through.

    Unknown parameters are kept as-is: Vinted adds filters over time and passing them
    through keeps the search faithful to what the user set up in their browser.
    """
    url = url.strip()
    if not url:
        raise InvalidSearchUrl("URL vide")
    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if not domain.startswith("www.vinted."):
        raise InvalidSearchUrl(f"Ce n'est pas une URL Vinted : {parsed.netloc or url}")
    if "/catalog" not in parsed.path:
        raise InvalidSearchUrl(
            "L'URL doit être une page de recherche (/catalog). "
            "Faites votre recherche sur Vinted puis copiez l'URL du navigateur."
        )

    params: dict[str, list[str]] = {}
    for key, values in parse_qs(parsed.query, keep_blank_values=False).items():
        key = _ALIASES.get(key, key)
        if key in ("page", "time", "disabled_personalization"):
            continue
        params.setdefault(key, []).extend(values)

    # /catalog/1805-shirts carries the category in the path.
    tail = parsed.path.rstrip("/").split("/catalog/")
    if len(tail) > 1 and tail[-1]:
        catalog_id = tail[-1].split("-")[0]
        if catalog_id.isdigit() and catalog_id not in params.get("catalog[]", []):
            params.setdefault("catalog[]", []).append(catalog_id)

    if not params:
        raise InvalidSearchUrl(
            "Aucun filtre détecté dans cette URL. Ajoutez au moins un mot-clé, "
            "une marque ou une catégorie sur Vinted avant de copier l'URL."
        )

    params.setdefault("order", ["newest_first"])
    return SearchQuery(domain=domain, params=params)

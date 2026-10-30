# CLAUDE.md

Add-on Home Assistant qui repère des articles revendables sur Vinted. Dépôt d'add-ons HA
(`repository.yaml` à la racine), l'add-on lui-même est dans `vinted_flipper/`.

Échanges avec l'utilisateur en **français**, code et commits en **anglais**.

## Architecture

```
config.yaml / Dockerfile / run.sh      packaging add-on (Ingress, API Core via Supervisor)
src/vinted_flipper/
  __main__.py      uvicorn + APScheduler (cycle horaire)
  config.py        options : /data/options.json (add-on) ou variables d'env (dev)
  vinted/
    search_url.py  URL de recherche collée → SearchQuery (pagination + description des filtres)
    client.py      httpx, session, rythme lent, backoff ; une requête à la fois (lock)
    parse.py       extraction HTML : parse_catalog() et parse_item()
  db.py            SQLite ; aucun article n'est jamais supprimé
  collector.py     run_cycle() : scan des recherches → vérification des fiches → scoring
  analysis.py      clusters, médianes de ventes, marges, seuils
  ha.py            capteurs, événement, notifications (no-op hors HA)
  web/app.py       FastAPI + Jinja2 ; liens préfixés par X-Ingress-Path
```

## Points à connaître

- **L'API JSON de Vinted est bloquée** (`/api/v2/catalog/items` → page Fastly `_fs-ch-`).
  On lit les **pages HTML**. Tous les motifs d'extraction sont documentés et justifiés dans
  `docs/vinted-api-notes.md` : le lire avant de toucher à `parse.py`.
- **Un article vendu répond HTTP 200.** Le seul marqueur fiable est `can_buy:false` dans le
  JSON embarqué, avec `is_reserved` / `is_hidden` pour écarter les faux positifs. Un 404 veut
  dire retiré, pas vendu — et un article retiré ne doit jamais compter comme une vente.
- **Toujours vérifier une fiche par son URL complète** (avec le slug) : sans lui Vinted répond
  307 et chaque article coûte deux requêtes contre la limite de débit.
- **Vinted limite le débit** : HTTP 429 observé après ~30 requêtes rapprochées. D'où
  `min_delay`/`max_delay` à 6–12 s et `max_checks_per_cycle` à 25. Ne pas accélérer sans mesurer.
- **Les prix sont fournis par Vinted** : le second prix d'une annonce inclut déjà les frais de
  protection acheteur, et la fiche donne le port. On n'estime les frais que par défaut.
- **Attention au format des prix** : « 1 234,50 € » contient une virgule, alors que les champs
  de l'attribut `title` sont eux-mêmes séparés par des virgules. Les prix sont donc extraits
  de la chaîne entière avant tout découpage (`_TRAILING_PRICE_RE`).

## Tests

```bash
cd vinted_flipper && .venv/bin/python -m pytest tests/
```

Les fixtures de `tests/fixtures/` sont de **vrais extraits** de pages vinted.fr (un catalogue,
un article en vente, un article vendu). Si Vinted change son HTML, ces tests échouent en
premier : les recapturer plutôt que d'assouplir les assertions.

## Lancer en local

```bash
cd vinted_flipper
DATA_DIR=./data PYTHONPATH=src .venv/bin/python -m vinted_flipper   # http://localhost:8099
```

Hors Home Assistant, `ha.py` ne fait rien (pas de `SUPERVISOR_TOKEN`) : l'interface et la
collecte fonctionnent quand même.

## Release

Bump de `version` dans `vinted_flipper/config.yaml` + entrée dans `CHANGELOG.md`, comme pour
tout add-on HACS.

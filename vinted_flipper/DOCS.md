# Vinted Flipper

Add-on Home Assistant qui repère sur Vinted les articles achetables nettement moins cher que
le prix auquel des articles comparables se sont **réellement vendus**.

## Principe

1. Vous collez l'URL d'une recherche Vinted (avec vos filtres) dans l'interface.
2. Toutes les heures, l'add-on relève les annonces de cette recherche et les enregistre.
3. En tâche de fond, il revisite les fiches pour voir lesquelles sont passées à « vendu ».
4. À partir de ces ventes observées, il calcule un prix de revente médian par groupe
   d'articles comparables (marque + catégorie + taille + état).
5. Une annonce dont le coût d'achat est nettement sous ce prix est signalée comme bonne affaire,
   avec une notification sur votre téléphone.

L'achat et la revente restent manuels : l'add-on ne fait qu'observer et estimer.

## Installation

Dans Home Assistant : **Paramètres → Modules complémentaires → Boutique → ⋮ → Dépôts**,
ajoutez `https://github.com/AndroFlo/resale-finder`, puis installez « Vinted Flipper ».

L'interface apparaît ensuite dans la barre latérale.

## Options

| Option | Défaut | Rôle |
|---|---|---|
| `scan_interval_minutes` | 60 | Fréquence des scans |
| `max_pages` | 3 | Pages de résultats par recherche (96 articles par page) |
| `recheck_hours` | 24 | Intervalle minimum entre deux vérifications d'une même fiche |
| `max_checks_per_cycle` | 25 | Fiches vérifiées par cycle (limites de Vinted) |
| `min_margin` | 8 € | Marge minimale pour signaler une affaire |
| `min_margin_pct` | 25 % | Marge minimale en pourcentage |
| `max_days_to_sell` | 45 | Ignore les groupes qui s'écoulent trop lentement (0 = sans limite) |
| `notify_service` | — | Service de notification, ex. `mobile_app_pixel` |
| `notify_min_margin` | 15 € | Marge à partir de laquelle une notification est envoyée |
| `min_delay` / `max_delay` | 6 / 12 s | Délai entre deux requêtes vers Vinted |

## Entités créées

- `sensor.vinted_flipper_deals` — nombre de bonnes affaires, les 5 meilleures en attributs.
- `sensor.vinted_flipper_last_scan` — bilan du dernier scan.
- Événement `vinted_deal_found` à chaque nouvelle affaire, utilisable en automatisation.

## Patience nécessaire

Les estimations reposent sur des ventes **observées**. Comptez quelques jours avant que les
premiers prix de revente soient fiables : l'add-on doit d'abord voir des articles se vendre.
L'onglet « Marché » montre l'avancement de cette collecte.

## Développement

```bash
cd vinted_flipper
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/
DATA_DIR=./data PYTHONPATH=src .venv/bin/python -m vinted_flipper  # http://localhost:8099
```

Les notes de reverse engineering du site sont dans [`docs/vinted-api-notes.md`](docs/vinted-api-notes.md).

## Avertissement

Vinted n'expose pas d'API publique et ses conditions d'utilisation encadrent la collecte
automatisée. Cet add-on lit les pages publiques à rythme lent, pour un usage personnel.
Vous restez responsable de son utilisation.

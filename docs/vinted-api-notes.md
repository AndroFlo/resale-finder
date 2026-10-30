# Notes de reverse engineering Vinted (spike du 2026-10-30)

## Accès HTTP

Un client HTTP ordinaire (`httpx`) avec un User-Agent de navigateur et `Accept-Language: fr-FR`
suffit. Aucune impersonation TLS n'est nécessaire.

- `GET https://www.vinted.fr/` d'abord : pose les cookies de session
  (`access_token_web`, `anon_id`, `v_udt`, `refresh_token_web`).
- **`/api/v2/catalog/items` est bloqué** (renvoie une page de vérification Fastly `_fs-ch-…`).
  On n'utilise donc pas l'API JSON : on lit les **pages HTML** du catalogue.
- Pagination : `&page=N`, 96 articles par page, pas de doublon entre pages.
- Testé : 3 pages à 3 s d'intervalle sans blocage.

## Page catalogue — source du scan horaire

`GET https://www.vinted.fr/catalog?<filtres de l'utilisateur>&page=N`

Chaque article apparaît sous forme de lien, l'attribut `title` portant toutes les données utiles :

```html
<a href="/items/10593234882-lauren-ralph-lauren-deluxe-pet-bed..."
   data-testid="product-item-id-10593234882--overlay-link"
   title="Lauren Ralph Lauren Deluxe Pet Bed…, Marque: Ralph Lauren, État: Neuf avec étiquette, 55.00 €, 58.45 €">
```

Format de `title`, champs séparés par des virgules :
`<titre>, Marque: <marque>, [Taille: <taille>,] État: <état>, <prix article> €, <prix total> €`

- Le **premier prix** est le prix demandé par le vendeur, le **second le prix total**
  (frais de protection acheteur inclus, port exclu).
- Extraction validée : **95/95 articles** d'une page, tous champs présents.
- Favoris : `aria-label="…ajouté aux favoris par N utilisateur…"` sur
  `data-testid="product-item-id-<id>--favourite"` (souvent absent → `null`).

Attention : le titre peut contenir des virgules, donc on lit les champs par leur préfixe
(`Marque:`, `Taille:`, `État:`) et les prix par motif, jamais par position d'index.

## Fiche article — détection des ventes

`GET https://www.vinted.fr/items/<id>` (l'« slug » après l'id est facultatif).

La fiche d'un article vendu **reste accessible en HTTP 200**. Le marqueur fiable est le champ
`can_buy` du flux Next.js embarqué (présent en échappé : `\"can_buy\":true|false`).

| Cas | HTTP | `can_buy` | Statut retenu |
|---|---|---|---|
| En vente (3 articles testés) | 200 | `true` | `active` |
| **Vendu** (`10581479031`) | 200 | `false` | `sold` |
| Inexistant / supprimé | 404 | — | `removed` |

`is_reserved` et `is_hidden` sont deux autres causes possibles de `can_buy:false` : on les lit aussi
pour ne pas compter une annonce réservée ou masquée comme une vente.

Pièges constatés :
- « Enlevé ! » dans la page est le libellé du **bouton favori**, pas un statut de vente.
- `itemProp="status"` désigne l'**état** de l'article (« Neuf avec étiquette »), pas la vente.
- Le mot « vendu » apparaît des dizaines de fois dans le dictionnaire de traductions embarqué :
  inutilisable comme marqueur.

### Prix réel d'achat (décomposition exacte, fiche article)

```json
{"type":"buyer_protection","finalPrice":{"amount":"3.45"}}
{"type":"shipping","finalPrice":{"amount":"5.55"}}
"originalAskingAmount":{"amount":"55"}
"totalAmount":{"amount":"58.45"}
```

Vinted donne donc le coût d'achat réel : pas besoin d'estimer les frais. Le port est connu par
article (`areMultipleShippingOptionsAvailable` indique si plusieurs options existent).

### Autres champs de la fiche

- `brand_id`, `catalog_id` et le fil d'Ariane (`breadcrumbs`) avec les libellés et URLs de catégorie.
- `favourite_count`.

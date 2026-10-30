# Changelog

## 0.1.1

- Corrige l'échec de construction de l'image : la base Alpine 3.19 fournit Python 3.11
  alors que le paquet exige 3.12. Passage à Alpine 3.20 (Python 3.12).

## 0.1.0

Première version.

- Scan horaire des recherches Vinted à partir d'URLs copiées depuis le navigateur.
- Suivi du cycle de vie des annonces pour distinguer les ventes des retraits.
- Estimation du prix de revente par groupe d'articles comparables.
- Interface dans la barre latérale de Home Assistant (Ingress).
- Capteurs `sensor.vinted_flipper_deals` / `sensor.vinted_flipper_last_scan`,
  événement `vinted_deal_found` et notifications mobiles.

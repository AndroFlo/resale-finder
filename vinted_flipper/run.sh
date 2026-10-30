#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
export DATA_DIR=/data
bashio::log.info "Démarrage de Vinted Flipper…"
exec python3 -m vinted_flipper

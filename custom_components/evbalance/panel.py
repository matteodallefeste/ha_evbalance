# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Registrazione del pannello sidebar e comando websocket per EV Balance.

Il pannello è un custom element servito come file JS statico (nessuno step di
build). Tutti i valori live (potenze, corrente, limite) sono già esposti come
entità, quindi il frontend li legge direttamente da ``hass.states``; a questo
comando websocket spetta solo dire *quali* entità e *quali* statistic_id
usare, risolvendoli dal registro entità a partire dagli unique_id noti.

L'energia per fascia degli ultimi mesi non richiede storage custom: i sensori
energia hanno ``state_class = total_increasing`` e device_class ``energy``,
quindi il Recorder ne registra già le long-term statistics (tenute a tempo
indefinito). Il frontend le interroga con il comando core
``recorder/statistics_during_period`` sugli statistic_id restituiti qui.
"""

from __future__ import annotations

import os
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.frontend import async_remove_panel
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import (
    CONF_HOLD_SECONDS,
    CONF_MAX_CURRENT,
    CONF_MAX_POWER_W,
    CONF_MIN_CURRENT,
    CONF_NAME,
    CONF_PAUSE_CURRENT,
    CONF_PHASES,
    CONF_SAFETY_MARGIN_W,
    CONF_SHOW_PANEL,
    CONF_SOURCES,
    CONF_SOURCES_INCLUDE_EV_CHARGER,
    CONF_TARIFF_PRESET,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE,
    CONF_EV_CHARGER_CURRENT,
    CONF_EV_CHARGER_POWER,
    DEFAULT_HOLD_SECONDS,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MIN_CURRENT,
    DEFAULT_PAUSE_CURRENT,
    DEFAULT_PHASES,
    DEFAULT_SAFETY_MARGIN_W,
    DEFAULT_SHOW_PANEL,
    DEFAULT_SOURCES_INCLUDE_EV_CHARGER,
    DEFAULT_TARIFF_PRESET,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VOLTAGE,
    DOMAIN,
    PANEL_ICON,
    PANEL_JS_FILENAME,
    PANEL_JS_VERSION,
    PANEL_STATIC_URL,
    PANEL_TITLE,
    PANEL_URL_PATH,
    WS_TYPE_PANEL,
)
from .energy import preset_bands

WEBCOMPONENT_NAME = "evbalance-panel"
_STATIC_FLAG = "static_registered"
_WS_FLAG = "ws_registered"

WS_TYPE_CONFIG_GET = "evbalance/config/get"
WS_TYPE_CONFIG_SET = "evbalance/config/set"

# Chiavi che vivono nell'entry.data (struttura) vs entry.options (a caldo).
_DATA_KEYS = (
    CONF_NAME,
    CONF_EV_CHARGER_POWER,
    CONF_EV_CHARGER_CURRENT,
    CONF_MAX_POWER_W,
    CONF_VOLTAGE,
    CONF_PHASES,
    CONF_MIN_CURRENT,
    CONF_MAX_CURRENT,
)
_OPTION_KEYS = (
    CONF_SOURCES,
    CONF_SOURCES_INCLUDE_EV_CHARGER,
    CONF_SAFETY_MARGIN_W,
    CONF_PAUSE_CURRENT,
    CONF_HOLD_SECONDS,
    CONF_UPDATE_INTERVAL,
    CONF_TARIFF_PRESET,
    CONF_SHOW_PANEL,
)

# Coercizione per chiave (i valori arrivano da JSON: numeri, bool, liste, stringhe).
_INT_KEYS = (
    CONF_PHASES,
    CONF_MIN_CURRENT,
    CONF_MAX_CURRENT,
    CONF_PAUSE_CURRENT,
    CONF_HOLD_SECONDS,
    CONF_UPDATE_INTERVAL,
)
_FLOAT_KEYS = (CONF_MAX_POWER_W, CONF_VOLTAGE, CONF_SAFETY_MARGIN_W)
_BOOL_KEYS = (CONF_SOURCES_INCLUDE_EV_CHARGER, CONF_SHOW_PANEL)

# unique_id (senza prefisso entry_id) -> dominio piattaforma, per le entità live.
_LIVE_ENTITIES: dict[str, str] = {
    "total_power": "sensor",
    "sources_power": "sensor",
    "ev_charger_power": "sensor",
    "target_current": "sensor",
    "active_band": "sensor",
    "charging_blocked": "binary_sensor",
    "balancing": "switch",
}


# --- Websocket API ------------------------------------------------------


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Registra il comando websocket del pannello (una sola volta)."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get(_WS_FLAG):
        return
    websocket_api.async_register_command(hass, _ws_panel)
    websocket_api.async_register_command(hass, _ws_config_get)
    websocket_api.async_register_command(hass, _ws_config_set)
    data[_WS_FLAG] = True


@websocket_api.websocket_command({vol.Required("type"): WS_TYPE_PANEL})
@callback
def _ws_panel(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Restituisce metadati e mappe entità/statistiche per il pannello.

    Risolve gli entity_id dal registro tramite gli unique_id deterministici
    generati dalle entità, così il frontend non deve indovinarli.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_found", "Nessuna config entry")
        return

    entry = entries[0]
    ent_reg = er.async_get(hass)

    def resolve(platform: str, unique_suffix: str) -> str | None:
        unique_id = f"{entry.entry_id}_{unique_suffix}"
        return ent_reg.async_get_entity_id(platform, DOMAIN, unique_id)

    entities = {
        key: resolve(platform, key) for key, platform in _LIVE_ENTITIES.items()
    }

    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    preset = getattr(coordinator, "tariff_preset", "arera")
    bands = list(preset_bands(preset))

    # Statistic_id dei sensori energia "totale casa" per fascia (periodo daily):
    # basta un sensore total_increasing per fascia, i delta mensili si ottengono
    # dalle long-term statistics via recorder/statistics_during_period.
    band_stats: dict[str, str | None] = {}
    for band in bands:
        suffix = f"{slugify('total')}_{band.lower()}_daily_energy"
        band_stats[band] = resolve("sensor", suffix)

    max_power_w = None
    if coordinator is not None and coordinator.data:
        max_power_w = coordinator.data.get(CONF_MAX_POWER_W)

    connection.send_result(
        msg["id"],
        {
            "title": entry.title,
            "preset": preset,
            "bands": bands,
            "entities": entities,
            "band_stats": band_stats,
            "max_power_w": max_power_w,
        },
    )


# --- Websocket: lettura/scrittura configurazione dal pannello ----------


def _entry_value(entry, key: str, default: Any) -> Any:
    """Valore corrente: options ha priorità su data, poi default."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


def _current_config(entry) -> dict[str, Any]:
    """Snapshot completo della configurazione per il form del pannello."""
    return {
        CONF_NAME: entry.title,
        CONF_EV_CHARGER_POWER: _entry_value(entry, CONF_EV_CHARGER_POWER, None),
        CONF_EV_CHARGER_CURRENT: _entry_value(entry, CONF_EV_CHARGER_CURRENT, None),
        CONF_MAX_POWER_W: _entry_value(entry, CONF_MAX_POWER_W, 3300),
        CONF_VOLTAGE: _entry_value(entry, CONF_VOLTAGE, DEFAULT_VOLTAGE),
        CONF_PHASES: int(_entry_value(entry, CONF_PHASES, DEFAULT_PHASES)),
        CONF_MIN_CURRENT: _entry_value(entry, CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT),
        CONF_MAX_CURRENT: _entry_value(entry, CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT),
        CONF_SOURCES: list(_entry_value(entry, CONF_SOURCES, []) or []),
        CONF_SOURCES_INCLUDE_EV_CHARGER: bool(
            _entry_value(
                entry, CONF_SOURCES_INCLUDE_EV_CHARGER, DEFAULT_SOURCES_INCLUDE_EV_CHARGER
            )
        ),
        CONF_SAFETY_MARGIN_W: _entry_value(
            entry, CONF_SAFETY_MARGIN_W, DEFAULT_SAFETY_MARGIN_W
        ),
        CONF_PAUSE_CURRENT: _entry_value(entry, CONF_PAUSE_CURRENT, DEFAULT_PAUSE_CURRENT),
        CONF_HOLD_SECONDS: _entry_value(entry, CONF_HOLD_SECONDS, DEFAULT_HOLD_SECONDS),
        CONF_UPDATE_INTERVAL: _entry_value(
            entry, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        ),
        CONF_TARIFF_PRESET: _entry_value(
            entry, CONF_TARIFF_PRESET, DEFAULT_TARIFF_PRESET
        ),
        CONF_SHOW_PANEL: bool(_entry_value(entry, CONF_SHOW_PANEL, DEFAULT_SHOW_PANEL)),
    }


@websocket_api.websocket_command({vol.Required("type"): WS_TYPE_CONFIG_GET})
@callback
def _ws_config_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Restituisce la configurazione corrente per popolare il form."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_found", "Nessuna config entry")
        return
    connection.send_result(
        msg["id"],
        {
            "config": _current_config(entries[0]),
            "can_edit": connection.user.is_admin,
        },
    )


def _coerce(key: str, value: Any) -> Any:
    """Converte un valore del form nel tipo atteso dalla config entry."""
    if key in _BOOL_KEYS:
        return bool(value)
    if key in _INT_KEYS:
        return int(value)
    if key in _FLOAT_KEYS:
        return float(value)
    if key == CONF_SOURCES:
        return [str(v) for v in (value or [])]
    return value


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TYPE_CONFIG_SET,
        vol.Required("config"): dict,
    }
)
@callback
def _ws_config_set(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Aggiorna la config entry dai valori del form (solo admin)."""
    if not connection.user.is_admin:
        connection.send_error(
            msg["id"], "unauthorized", "Servono privilegi di amministratore"
        )
        return

    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_found", "Nessuna config entry")
        return
    entry = entries[0]

    incoming = msg["config"]
    data = dict(entry.data)
    options = dict(entry.options)
    try:
        for key, raw in incoming.items():
            if key in _DATA_KEYS:
                data[key] = _coerce(key, raw)
            elif key in _OPTION_KEYS:
                options[key] = _coerce(key, raw)
            # chiavi sconosciute ignorate
    except (TypeError, ValueError):
        connection.send_error(msg["id"], "invalid_format", "Valori non validi")
        return

    if int(data.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT)) >= int(
        data.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT)
    ):
        connection.send_error(
            msg["id"], "min_ge_max", "La corrente minima deve essere inferiore alla massima"
        )
        return

    title = str(data.get(CONF_NAME) or entry.title)
    # async_update_entry innesca l'update listener -> reload dell'integrazione.
    hass.config_entries.async_update_entry(
        entry, title=title, data=data, options=options
    )
    connection.send_result(msg["id"], {"ok": True})


# --- Registrazione pannello --------------------------------------------


async def _async_register_static(hass: HomeAssistant) -> None:
    """Serve la cartella www/ del pannello come statica (una sola volta).

    Si serve l'intera cartella (non il solo file principale) così il modulo del
    pannello può importare il modulo fratello delle traduzioni via path relativo.
    """
    data = hass.data.setdefault(DOMAIN, {})
    if data.get(_STATIC_FLAG):
        return
    path = os.path.join(os.path.dirname(__file__), "www")
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_STATIC_URL, path, True)]
        )
    except ImportError:  # pragma: no cover - HA più vecchi
        hass.http.register_static_path(PANEL_STATIC_URL, path, True)
    data[_STATIC_FLAG] = True


async def async_register_panel(hass: HomeAssistant) -> None:
    """Registra (o ri-registra) il pannello nella sidebar."""
    await _async_register_static(hass)
    async_remove_panel_if_present(hass)
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=WEBCOMPONENT_NAME,
        frontend_url_path=PANEL_URL_PATH,
        module_url=f"{PANEL_STATIC_URL}/{PANEL_JS_FILENAME}?v={PANEL_JS_VERSION}",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        require_admin=False,
    )


@callback
def async_remove_panel_if_present(hass: HomeAssistant) -> None:
    """Rimuove il pannello dalla sidebar se presente."""
    async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)

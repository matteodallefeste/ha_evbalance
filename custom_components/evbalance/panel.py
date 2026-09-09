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

import hashlib
import os
import socket
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.frontend import async_remove_panel
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import (
    CONF_ALLOWED_BANDS,
    CONF_CONTROL_MODE,
    CONF_HOLD_SECONDS,
    CONF_MAX_CURRENT,
    CONF_MAX_POWER_W,
    CONF_MIN_CURRENT,
    CONF_NAME,
    CONF_OCPP_CONNECTOR,
    CONF_OCPP_CP_ID,
    CONF_OCPP_METER_INTERVAL,
    CONF_OCPP_PASSWORD,
    CONF_OCPP_PORT,
    CONF_OCPP_USE_HA_PORT,
    CONF_PAUSE_CURRENT,
    CONF_PHASES,
    CONF_SAFETY_MARGIN_W,
    CONF_SHOW_PANEL,
    CONF_SOURCES,
    CONF_SOURCES_INCLUDE_EV_CHARGER,
    CONF_TARIFF_PRESET,
    CONF_TARIFF_PRICES,
    CONF_TARIFFS,
    CONF_CURRENCY,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE,
    CONF_EV_CHARGER_CURRENT,
    CONF_EV_CHARGER_POWER,
    CONF_EV_CHARGER_SWITCH,
    CONF_EV_CHARGER_SWITCH_INVERT,
    DEFAULT_ALLOWED_BANDS,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CURRENCY,
    DEFAULT_EV_CHARGER_SWITCH_INVERT,
    DEFAULT_HOLD_SECONDS,
    DEFAULT_OCPP_CONNECTOR,
    DEFAULT_OCPP_CP_ID,
    DEFAULT_OCPP_METER_INTERVAL,
    DEFAULT_OCPP_PASSWORD,
    DEFAULT_OCPP_PORT,
    DEFAULT_OCPP_USE_HA_PORT,
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
    MODE_ENTITIES,
    MODE_OCPP,
    PANEL_ICON,
    PANEL_JS_FILENAME,
    PANEL_JS_VERSION,
    PANEL_TRANSLATIONS_FILENAME,
    PANEL_STATIC_URL,
    PANEL_TITLE,
    PANEL_URL_PATH,
    WS_TYPE_PANEL,
)
from .energy import DEFAULT_SCHEME, scheme_from_dict, scheme_to_dict
from .tariff_loader import get_presets

WEBCOMPONENT_NAME = "evbalance-panel"
_STATIC_FLAG = "static_registered"
_WS_FLAG = "ws_registered"
_LOCAL_IP_KEY = "local_ip"

WS_TYPE_CONFIG_GET = "evbalance/config/get"
WS_TYPE_CONFIG_SET = "evbalance/config/set"

# Chiavi che vivono nell'entry.data (struttura) vs entry.options (a caldo).
_DATA_KEYS = (
    CONF_NAME,
    CONF_CONTROL_MODE,
    CONF_EV_CHARGER_POWER,
    CONF_EV_CHARGER_CURRENT,
    CONF_MAX_POWER_W,
    CONF_VOLTAGE,
    CONF_PHASES,
    CONF_MIN_CURRENT,
    CONF_MAX_CURRENT,
)
_OPTION_KEYS = (
    CONF_EV_CHARGER_SWITCH,
    CONF_EV_CHARGER_SWITCH_INVERT,
    CONF_SOURCES,
    CONF_SOURCES_INCLUDE_EV_CHARGER,
    CONF_SAFETY_MARGIN_W,
    CONF_PAUSE_CURRENT,
    CONF_HOLD_SECONDS,
    CONF_UPDATE_INTERVAL,
    CONF_TARIFF_PRESET,
    CONF_TARIFFS,
    CONF_TARIFF_PRICES,
    CONF_CURRENCY,
    CONF_SHOW_PANEL,
    CONF_ALLOWED_BANDS,
    CONF_OCPP_PORT,
    CONF_OCPP_CP_ID,
    CONF_OCPP_PASSWORD,
    CONF_OCPP_CONNECTOR,
    CONF_OCPP_METER_INTERVAL,
    CONF_OCPP_USE_HA_PORT,
)

# Coercizione per chiave (i valori arrivano da JSON: numeri, bool, liste, stringhe).
_INT_KEYS = (
    CONF_PHASES,
    CONF_MIN_CURRENT,
    CONF_MAX_CURRENT,
    CONF_PAUSE_CURRENT,
    CONF_HOLD_SECONDS,
    CONF_UPDATE_INTERVAL,
    CONF_OCPP_PORT,
    CONF_OCPP_CONNECTOR,
    CONF_OCPP_METER_INTERVAL,
)
_FLOAT_KEYS = (CONF_MAX_POWER_W, CONF_VOLTAGE, CONF_SAFETY_MARGIN_W)
_BOOL_KEYS = (
    CONF_SOURCES_INCLUDE_EV_CHARGER,
    CONF_EV_CHARGER_SWITCH_INVERT,
    CONF_SHOW_PANEL,
    CONF_OCPP_USE_HA_PORT,
)

# unique_id (senza prefisso entry_id) -> dominio piattaforma, per le entità live.
_LIVE_ENTITIES: dict[str, str] = {
    "total_power": "sensor",
    "sources_power": "sensor",
    "ev_charger_power": "sensor",
    "target_current": "sensor",
    "active_band": "sensor",
    "charging_blocked": "binary_sensor",
    "balancing": "switch",
    "charging_allowed": "switch",
    "ocpp_status": "sensor",
    "ocpp_session_energy": "sensor",
    "ocpp_connected": "binary_sensor",
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
    scheme = getattr(coordinator, "tariff_scheme", None) or DEFAULT_SCHEME
    preset = getattr(coordinator, "tariff_preset", scheme.id)
    bands = list(scheme.band_ids)
    band_meta = {
        b.id: {"label": b.label or b.id, "rank": b.rank, "color": b.color}
        for b in scheme.bands
    }

    # Statistic_id dei sensori energia per fascia (periodo daily): basta un
    # sensore total_increasing per fascia, i delta storici si ottengono dalle
    # long-term statistics via recorder/statistics_during_period. Esponiamo sia
    # il "totale casa" sia la sola "EV Charger", così il pannello può mostrare
    # la quota EV vs resto casa per fascia.
    band_stats: dict[str, str | None] = {}
    band_stats_ev: dict[str, str | None] = {}
    for band in bands:
        band_stats[band] = resolve(
            "sensor", f"{slugify('total')}_{band.lower()}_daily_energy"
        )
        band_stats_ev[band] = resolve(
            "sensor", f"{slugify('ev_charger')}_{band.lower()}_daily_energy"
        )

    max_power_w = None
    if coordinator is not None and coordinator.data:
        max_power_w = coordinator.data.get(CONF_MAX_POWER_W)

    band_prices = dict(_entry_value(entry, CONF_TARIFF_PRICES, {}) or {})
    currency = _entry_value(entry, CONF_CURRENCY, DEFAULT_CURRENCY)

    connection.send_result(
        msg["id"],
        {
            "title": entry.title,
            # La modalità di controllo decide cosa mostrare nel tab Live.
            "control_mode": _entry_value(entry, CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE),
            # Indirizzo LAN da proporre alla wallbox (vedi _local_ip).
            "local_ip": hass.data.get(DOMAIN, {}).get(_LOCAL_IP_KEY),
            "preset": preset,
            "bands": bands,
            "band_meta": band_meta,
            "entities": entities,
            "band_stats": band_stats,
            "band_stats_ev": band_stats_ev,
            "band_prices": band_prices,
            "currency": currency,
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
        CONF_EV_CHARGER_SWITCH: _entry_value(entry, CONF_EV_CHARGER_SWITCH, None),
        CONF_EV_CHARGER_SWITCH_INVERT: bool(
            _entry_value(
                entry, CONF_EV_CHARGER_SWITCH_INVERT, DEFAULT_EV_CHARGER_SWITCH_INVERT
            )
        ),
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
        CONF_TARIFFS: _entry_value(entry, CONF_TARIFFS, None),
        CONF_TARIFF_PRICES: dict(_entry_value(entry, CONF_TARIFF_PRICES, {}) or {}),
        CONF_CURRENCY: _entry_value(entry, CONF_CURRENCY, DEFAULT_CURRENCY),
        CONF_SHOW_PANEL: bool(_entry_value(entry, CONF_SHOW_PANEL, DEFAULT_SHOW_PANEL)),
        CONF_ALLOWED_BANDS: list(
            _entry_value(entry, CONF_ALLOWED_BANDS, DEFAULT_ALLOWED_BANDS) or []
        ),
        # La modalità di controllo si sceglie quando si aggiunge
        # l'integrazione: il pannello la mostra ma non la cambia.
        CONF_CONTROL_MODE: _entry_value(entry, CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE),
        CONF_OCPP_PORT: int(_entry_value(entry, CONF_OCPP_PORT, DEFAULT_OCPP_PORT)),
        CONF_OCPP_CP_ID: _entry_value(entry, CONF_OCPP_CP_ID, DEFAULT_OCPP_CP_ID),
        CONF_OCPP_PASSWORD: _entry_value(entry, CONF_OCPP_PASSWORD, DEFAULT_OCPP_PASSWORD),
        CONF_OCPP_CONNECTOR: int(
            _entry_value(entry, CONF_OCPP_CONNECTOR, DEFAULT_OCPP_CONNECTOR)
        ),
        CONF_OCPP_METER_INTERVAL: int(
            _entry_value(entry, CONF_OCPP_METER_INTERVAL, DEFAULT_OCPP_METER_INTERVAL)
        ),
        CONF_OCPP_USE_HA_PORT: bool(
            _entry_value(entry, CONF_OCPP_USE_HA_PORT, DEFAULT_OCPP_USE_HA_PORT)
        ),
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
    presets = [scheme_to_dict(scheme) for scheme in get_presets(hass).values()]
    connection.send_result(
        msg["id"],
        {
            "config": _current_config(entries[0]),
            "can_edit": connection.user.is_admin,
            "presets": presets,
        },
    )


def _coerce(key: str, value: Any) -> Any:
    """Converte un valore del form nel tipo atteso dalla config entry."""
    if key == CONF_CONTROL_MODE:
        if value not in (MODE_ENTITIES, MODE_OCPP):
            raise ValueError(f"modalità di controllo sconosciuta: {value!r}")
        return value
    if key in _BOOL_KEYS:
        return bool(value)
    if key in _INT_KEYS:
        return int(value)
    if key in _FLOAT_KEYS:
        return float(value)
    if key in (CONF_SOURCES, CONF_ALLOWED_BANDS):
        return [str(v) for v in (value or [])]
    if key == CONF_TARIFFS:
        if not value:
            return None
        # Valida costruendo lo schema: uno schema custom invalido solleva
        # ValueError -> il chiamante risponde "invalid_format".
        scheme_from_dict(value, scheme_id="custom")
        return value
    if key == CONF_TARIFF_PRICES:
        # {band_id: prezzo €/kWh}. Scarta valori vuoti/non numerici o negativi.
        out: dict[str, float] = {}
        for band, price in (value or {}).items():
            if price in (None, ""):
                continue
            p = float(price)
            if p < 0:
                raise ValueError(f"prezzo negativo per {band!r}")
            out[str(band)] = p
        return out
    if key == CONF_CURRENCY:
        return str(value or DEFAULT_CURRENCY).strip() or DEFAULT_CURRENCY
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


def _local_ip() -> str | None:
    """IP con cui Home Assistant è raggiungibile sulla propria LAN.

    Serve al pannello per suggerire alla wallbox un indirizzo che esista
    davvero: `location.hostname` del browser è l'indirizzo con cui sta
    navigando *l'utente*, che da fuori casa è quello pubblico — e la wallbox,
    attaccata alla rete di casa, lì non trova nessuno.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # Non invia nulla: serve solo a far scegliere la rotta al sistema.
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def _panel_fingerprint() -> str:
    """Impronta dei sorgenti JS del pannello, usata come token anti-cache.

    La cartella www/ è servita con header di cache lunghi: se l'URL del modulo
    non cambia, il browser continua a mostrare il pannello vecchio anche dopo
    un aggiornamento. Legandolo al contenuto dei file il token si aggiorna da
    solo, senza dipendere da un numero di versione da ricordare. Il pannello
    propaga lo stesso token all'import del modulo di traduzioni, così i due
    restano sempre allineati.
    """
    folder = os.path.join(os.path.dirname(__file__), "www")
    digest = hashlib.sha256()
    for name in (PANEL_JS_FILENAME, PANEL_TRANSLATIONS_FILENAME):
        try:
            with open(os.path.join(folder, name), "rb") as handle:
                digest.update(handle.read())
        except OSError:
            return PANEL_JS_VERSION
    return digest.hexdigest()[:10]


async def async_register_panel(hass: HomeAssistant) -> None:
    """Registra (o ri-registra) il pannello nella sidebar."""
    await _async_register_static(hass)
    async_remove_panel_if_present(hass)
    version = await hass.async_add_executor_job(_panel_fingerprint)
    data = hass.data.setdefault(DOMAIN, {})
    if _LOCAL_IP_KEY not in data:
        data[_LOCAL_IP_KEY] = await hass.async_add_executor_job(_local_ip)
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=WEBCOMPONENT_NAME,
        frontend_url_path=PANEL_URL_PATH,
        module_url=f"{PANEL_STATIC_URL}/{PANEL_JS_FILENAME}?v={version}",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        require_admin=False,
    )


@callback
def async_remove_panel_if_present(hass: HomeAssistant) -> None:
    """Rimuove il pannello dalla sidebar se presente."""
    async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)

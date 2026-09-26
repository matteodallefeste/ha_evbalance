# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""The EV Balance integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CONTROL_MODE,
    CONF_OCPP_CONNECTOR,
    CONF_OCPP_CP_ID,
    CONF_OCPP_METER_INTERVAL,
    CONF_OCPP_PASSWORD,
    CONF_OCPP_PORT,
    CONF_OCPP_PROFILE_PURPOSE,
    CONF_OCPP_USE_HA_PORT,
    CONF_SHOW_PANEL,
    DEFAULT_CONTROL_MODE,
    DEFAULT_OCPP_CONNECTOR,
    DEFAULT_OCPP_CP_ID,
    DEFAULT_OCPP_METER_INTERVAL,
    DEFAULT_OCPP_PASSWORD,
    DEFAULT_OCPP_PORT,
    DEFAULT_OCPP_PROFILE_PURPOSE,
    DEFAULT_OCPP_USE_HA_PORT,
    DEFAULT_SHOW_PANEL,
    DOMAIN,
    MODE_OCPP,
    OCPP_CSMS_KEY,
    OCPP_OPTION_KEYS,
    OCPP_VIEW_FLAG,
    PLATFORMS,
)
from .coordinator import EVBalanceCoordinator
from .ocpp_server import OcppCsms, OcppView
from .panel import (
    async_register_card,
    async_register_panel,
    async_register_websocket,
    async_remove_card_if_present,
    async_remove_panel_if_present,
)
from .tariff_loader import async_load_presets

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up globale: carica i preset tariffa e registra il websocket del pannello."""
    await async_load_presets(hass)
    async_register_websocket(hass)
    return True


def _entry_value(entry: ConfigEntry, key: str, default=None):
    """Valore corrente: le options hanno priorità sui dati iniziali."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


async def _async_start_csms(hass: HomeAssistant, entry: ConfigEntry) -> OcppCsms:
    """Avvia il server OCPP e lo rende raggiungibile dalla view di HA."""
    csms = OcppCsms(
        hass,
        port=int(_entry_value(entry, CONF_OCPP_PORT, DEFAULT_OCPP_PORT)),
        cp_id=str(_entry_value(entry, CONF_OCPP_CP_ID, DEFAULT_OCPP_CP_ID) or ""),
        password=str(_entry_value(entry, CONF_OCPP_PASSWORD, DEFAULT_OCPP_PASSWORD) or ""),
        connector=int(_entry_value(entry, CONF_OCPP_CONNECTOR, DEFAULT_OCPP_CONNECTOR)),
        meter_interval=int(
            _entry_value(entry, CONF_OCPP_METER_INTERVAL, DEFAULT_OCPP_METER_INTERVAL)
        ),
        profile_purpose=str(
            _entry_value(entry, CONF_OCPP_PROFILE_PURPOSE, DEFAULT_OCPP_PROFILE_PURPOSE)
        ),
        use_ha_port=bool(
            _entry_value(entry, CONF_OCPP_USE_HA_PORT, DEFAULT_OCPP_USE_HA_PORT)
        ),
    )
    data = hass.data.setdefault(DOMAIN, {})
    data[OCPP_CSMS_KEY] = csms

    try:
        await csms.async_start()
    except RuntimeError as err:
        data.pop(OCPP_CSMS_KEY, None)
        raise ConfigEntryNotReady(str(err)) from err

    # Le view di HA non si possono rimuovere: la registriamo una volta sola e
    # lascia che sia lei a ritrovare il CSMS attivo a ogni richiesta.
    if csms.use_ha_port and not data.get(OCPP_VIEW_FLAG):
        hass.http.register_view(OcppView(lambda: hass.data.get(DOMAIN, {}).get(OCPP_CSMS_KEY)))
        data[OCPP_VIEW_FLAG] = True

    return csms


@callback
def _async_normalize_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Porta le impostazioni OCPP nelle sole options.

    Le prime versioni le scrivevano sia nei dati (config flow) sia nelle options
    (pannello e options flow). `_entry_value` dà la precedenza alle options,
    quindi il valore *usato* era già quello giusto, ma la copia rimasta nei dati
    poteva mostrare un indirizzo diverso da quello realmente in vigore.
    """
    stale = [key for key in OCPP_OPTION_KEYS if key in entry.data]
    if not stale:
        return
    options = dict(entry.options)
    for key in stale:
        options.setdefault(key, entry.data[key])   # le options restano sovrane
    data = {k: v for k, v in entry.data.items() if k not in OCPP_OPTION_KEYS}
    _LOGGER.debug("Impostazioni OCPP spostate nelle options: %s", ", ".join(stale))
    hass.config_entries.async_update_entry(entry, data=data, options=options)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EV Balance from a config entry."""
    _async_normalize_entry(hass, entry)

    csms: OcppCsms | None = None
    if _entry_value(entry, CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE) == MODE_OCPP:
        csms = await _async_start_csms(hass, entry)

    coordinator = EVBalanceCoordinator(hass, entry, csms)

    if csms is not None:

        @callback
        def _ocpp_changed() -> None:
            """Novità dalla wallbox: aggiorna subito senza aspettare il ciclo."""
            hass.async_create_task(coordinator.async_request_refresh())

        entry.async_on_unload(csms.add_listener(_ocpp_changed))

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    # La card per le dashboard è indipendente dal pannello in sidebar: chi
    # tiene nascosto il pannello può comunque metterla in una dashboard.
    await async_register_card(hass)

    if entry.options.get(CONF_SHOW_PANEL, DEFAULT_SHOW_PANEL):
        await async_register_panel(hass)
    else:
        async_remove_panel_if_present(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    async_remove_panel_if_present(hass)
    async_remove_card_if_present(hass)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = hass.data.get(DOMAIN, {})
        coordinator = data.pop(entry.entry_id, None)
        csms = getattr(coordinator, "csms", None)
        if csms is not None:
            await csms.async_stop()
            if data.get(OCPP_CSMS_KEY) is csms:
                data.pop(OCPP_CSMS_KEY, None)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload quando cambiano le options."""
    await hass.config_entries.async_reload(entry.entry_id)

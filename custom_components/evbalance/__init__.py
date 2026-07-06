# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""The EV Balance integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import CONF_SHOW_PANEL, DEFAULT_SHOW_PANEL, DOMAIN, PLATFORMS
from .coordinator import EVBalanceCoordinator
from .panel import (
    async_register_panel,
    async_register_websocket,
    async_remove_panel_if_present,
)
from .tariff_loader import async_load_presets


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up globale: carica i preset tariffa e registra il websocket del pannello."""
    await async_load_presets(hass)
    async_register_websocket(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EV Balance from a config entry."""
    coordinator = EVBalanceCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    if entry.options.get(CONF_SHOW_PANEL, DEFAULT_SHOW_PANEL):
        await async_register_panel(hass)
    else:
        async_remove_panel_if_present(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    async_remove_panel_if_present(hass)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload quando cambiano le options."""
    await hass.config_entries.async_reload(entry.entry_id)

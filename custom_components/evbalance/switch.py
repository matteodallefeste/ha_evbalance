# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Switch per abilitare/disabilitare il bilanciamento."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import EVBalanceEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BalancingSwitch(coordinator), ChargingSwitch(coordinator)])


class BalancingSwitch(EVBalanceEntity, SwitchEntity):
    """Se OFF, l'integrazione legge le potenze ma non tocca la EV Charger."""

    _attr_translation_key = "balancing"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "balancing")

    @property
    def is_on(self) -> bool:
        return self.coordinator.balancing_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_balancing(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_balancing(False)


class ChargingSwitch(EVBalanceEntity, SwitchEntity, RestoreEntity):
    """Consenso manuale alla ricarica: se OFF la wallbox resta ferma.

    Si sovrappone al bilanciamento: con lo switch su OFF la ricarica è ferma
    anche se ci sarebbe budget. È lo stop "a mano" che serve per fermare la
    macchina senza staccare il cavo, e la scelta viene mantenuta al riavvio.
    """

    _attr_translation_key = "charging_allowed"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "charging_allowed")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self.coordinator.charging_allowed = last.state == "on"

    @property
    def is_on(self) -> bool:
        return self.coordinator.charging_allowed

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_charging_allowed(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_charging_allowed(False)

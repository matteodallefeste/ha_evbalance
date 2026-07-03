"""Switch per abilitare/disabilitare il bilanciamento."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import EVBalanceEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BalancingSwitch(coordinator)])


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

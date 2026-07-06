# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Binary sensor: rischio sovraccarico / ricarica in pausa."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import EVBalanceEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ChargingBlockedBinarySensor(coordinator)])


class ChargingBlockedBinarySensor(EVBalanceEntity, BinarySensorEntity):
    """ON quando la EV Charger è stata messa in pausa per evitare il sovraccarico."""

    _attr_translation_key = "charging_blocked"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "charging_blocked")

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.get("charging_blocked"))

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {"reasons": data.get("reasons", [])}

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
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODE_OCPP
from .entity import EVBalanceEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [ChargingBlockedBinarySensor(coordinator)]
    if coordinator.control_mode == MODE_OCPP:
        entities.append(OcppConnectedBinarySensor(coordinator))
    async_add_entities(entities)


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


class OcppConnectedBinarySensor(EVBalanceEntity, BinarySensorEntity):
    """ON quando la wallbox è collegata al server OCPP di EV Balance."""

    _attr_translation_key = "ocpp_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "ocpp_connected")

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.get("ocpp_connected"))

    @property
    def extra_state_attributes(self) -> dict:
        data = (self.coordinator.data or {}).get("ocpp") or {}
        return {"charge_point_id": data.get("cp_id")}

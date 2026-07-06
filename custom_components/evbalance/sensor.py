# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Sensori di potenza, stato e energia (per sorgente, fascia e periodo)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import DOMAIN
from .energy import energy_increment_kwh, preset_bands
from .entity import EVBalanceEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        EVStatusSensor(coordinator, "total_power", "total_w",
                       UnitOfPower.WATT, SensorDeviceClass.POWER),
        EVStatusSensor(coordinator, "sources_power", "sources_w",
                       UnitOfPower.WATT, SensorDeviceClass.POWER),
        EVStatusSensor(coordinator, "ev_charger_power", "ev_charger_w",
                       UnitOfPower.WATT, SensorDeviceClass.POWER),
        EVCurrentSensor(coordinator),
        EVBandSensor(coordinator),
    ]

    # Sorgenti di energia da tracciare: totale, EV Charger e ogni sorgente.
    energy_keys: list[tuple[str, str]] = [
        ("total", "Totale"),
        ("ev_charger", "EV Charger"),
    ]
    for eid in coordinator.sources:
        st = hass.states.get(eid)
        label = st.attributes.get("friendly_name") if st else None
        energy_keys.append((eid, label or eid))

    bands = preset_bands(coordinator.tariff_preset)
    for key, label in energy_keys:
        for band in bands:
            for period in ("daily", "monthly"):
                entities.append(
                    EVEnergySensor(coordinator, key, label, band, period)
                )

    async_add_entities(entities)


class EVStatusSensor(EVBalanceEntity, SensorEntity):
    """Espone un valore numerico dal dict del coordinator."""

    def __init__(self, coordinator, key, data_key, unit, device_class) -> None:
        super().__init__(coordinator, key)
        self._data_key = data_key
        self._attr_translation_key = key
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get(self._data_key, 0), 1)


class EVCurrentSensor(EVBalanceEntity, SensorEntity):
    """Corrente attualmente concessa alla EV Charger (A)."""

    _attr_translation_key = "target_current"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "target_current")

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("target_current")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "applied_current": data.get("applied_current"),
            "reasons": data.get("reasons", []),
            "balancing_enabled": data.get("balancing_enabled"),
        }


class EVBandSensor(EVBalanceEntity, SensorEntity):
    """Fascia oraria attualmente attiva."""

    _attr_translation_key = "active_band"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "active_band")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("active_band")


class EVEnergySensor(EVBalanceEntity, RestoreSensor):
    """Energia (kWh) per sorgente + fascia, con reset giornaliero o mensile.

    Integra la potenza istantanea nel tempo (somma di Riemann) accumulando solo
    quando la fascia della sorgente coincide con quella del sensore.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator, key: str, label: str, band: str, period: str) -> None:
        uid = f"{slugify(key)}_{band.lower()}_{period}_energy"
        super().__init__(coordinator, uid)
        self._source_key = key
        self._band = band
        self._period = period  # "daily" | "monthly"
        self._attr_name = f"{label} {band} {'giornaliero' if period == 'daily' else 'mensile'}"
        self._value: float = 0.0
        self._period_id: str | None = None
        self._attr_icon = "mdi:lightning-bolt"

    def _period_key(self, now: datetime) -> str:
        if self._period == "daily":
            return now.date().isoformat()
        return f"{now.year}-{now.month:02d}"

    def _power_for_source(self, data: dict) -> float:
        if self._source_key == "total":
            return data.get("total_w", 0.0)
        if self._source_key == "ev_charger":
            return data.get("ev_charger_w", 0.0)
        return data.get("per_source", {}).get(self._source_key, 0.0)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._value = float(last.native_value)
            except (TypeError, ValueError):
                self._value = 0.0
        self._period_id = self._period_key(dt_util.now())

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data or {}
        now = dt_util.now()

        current_period = self._period_key(now)
        if self._period_id is None:
            self._period_id = current_period
        elif current_period != self._period_id:
            # Rollover di giorno/mese: azzera.
            self._value = 0.0
            self._period_id = current_period

        if data.get("active_band") == self._band:
            self._value += energy_increment_kwh(
                self._power_for_source(data), data.get("elapsed_s", 0.0)
            )

        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        return round(self._value, 4)

    @property
    def extra_state_attributes(self) -> dict:
        return {"band": self._band, "period": self._period, "source": self._source_key}

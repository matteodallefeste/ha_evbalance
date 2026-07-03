"""Base entity per EV Balance (device grouping + coordinator)."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EVBalanceCoordinator


class EVBalanceEntity(CoordinatorEntity[EVBalanceCoordinator]):
    """Entità base collegata al coordinator e allo stesso device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EVBalanceCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="EV Balance",
            model="Energy Load Balancer",
        )

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Coordinator: legge le potenze, calcola il bilanciamento, attua sulla EV Charger."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .balancer import (
    BalancerConfig,
    BalancerInputs,
    BalancerState,
    next_current,
    watts_per_amp,
)
from .const import (
    CONF_CURRENT_STEPS,
    CONF_HOLD_SECONDS,
    CONF_MAX_CURRENT,
    CONF_MAX_POWER_W,
    CONF_MIN_CURRENT,
    CONF_PAUSE_CURRENT,
    CONF_PHASES,
    CONF_SAFETY_MARGIN_W,
    CONF_SOURCES,
    CONF_SOURCES_INCLUDE_EV_CHARGER,
    CONF_TARIFF_PRESET,
    CONF_TARIFFS,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE,
    CONF_EV_CHARGER_CURRENT,
    CONF_EV_CHARGER_POWER,
    DEFAULT_CURRENT_STEPS,
    DEFAULT_HOLD_SECONDS,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MIN_CURRENT,
    DEFAULT_PAUSE_CURRENT,
    DEFAULT_PHASES,
    DEFAULT_SAFETY_MARGIN_W,
    DEFAULT_SOURCES_INCLUDE_EV_CHARGER,
    DEFAULT_TARIFF_PRESET,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VOLTAGE,
    DOMAIN,
)
from .energy import TariffScheme, active_band
from .tariff_loader import holidays_for_scheme, resolve_scheme

_LOGGER = logging.getLogger(__name__)


def _to_float(value: object) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f


class EVBalanceCoordinator(DataUpdateCoordinator[dict]):
    """Cuore dell'integrazione."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        interval = self._opt(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=max(3, int(interval))),
        )
        self.state = BalancerState()
        self.balancing_enabled = True   # pilotato dallo switch
        self._last_ts: float | None = None

    # --- helper lettura config ---
    def _opt(self, key: str, default=None):
        if key in self.entry.options:
            return self.entry.options[key]
        return self.entry.data.get(key, default)

    @property
    def sources(self) -> list[str]:
        return list(self._opt(CONF_SOURCES, []) or [])

    @property
    def sources_include_ev_charger(self) -> bool:
        return bool(
            self._opt(CONF_SOURCES_INCLUDE_EV_CHARGER, DEFAULT_SOURCES_INCLUDE_EV_CHARGER)
        )

    @property
    def tariff_preset(self) -> str:
        return self._opt(CONF_TARIFF_PRESET, DEFAULT_TARIFF_PRESET)

    @property
    def tariff_scheme(self) -> TariffScheme:
        """Schema tariffario risolto (preset built-in o custom dalle options)."""
        return resolve_scheme(
            self.hass, self.tariff_preset, self._opt(CONF_TARIFFS, None)
        )

    def _read_w(self, entity_id: str) -> float:
        """Legge un sensore di potenza in W (0 se non disponibile)."""
        st = self.hass.states.get(entity_id)
        if st is None:
            return 0.0
        val = _to_float(st.state)
        if val is None:
            return 0.0
        # Se il sensore è in kW lo riportiamo in W.
        unit = st.attributes.get("unit_of_measurement", "")
        if isinstance(unit, str) and unit.lower() in ("kw", "kwh"):
            val *= 1000.0
        return val

    def _build_config(self) -> BalancerConfig:
        voltage = float(self._opt(CONF_VOLTAGE, DEFAULT_VOLTAGE))
        phases = int(self._opt(CONF_PHASES, DEFAULT_PHASES))
        return BalancerConfig(
            max_power_w=float(self._opt(CONF_MAX_POWER_W, 3300)),
            safety_margin_w=float(
                self._opt(CONF_SAFETY_MARGIN_W, DEFAULT_SAFETY_MARGIN_W)
            ),
            watts_per_amp=watts_per_amp(voltage, phases),
            min_current=int(self._opt(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT)),
            max_current=int(self._opt(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT)),
            pause_current=int(self._opt(CONF_PAUSE_CURRENT, DEFAULT_PAUSE_CURRENT)),
            hold_seconds=float(self._opt(CONF_HOLD_SECONDS, DEFAULT_HOLD_SECONDS)),
            current_steps=[
                int(s)
                for s in (self._opt(CONF_CURRENT_STEPS, DEFAULT_CURRENT_STEPS) or [])
            ],
        )

    async def _async_update_data(self) -> dict:
        now_mono = time.monotonic()
        elapsed = 0.0 if self._last_ts is None else now_mono - self._last_ts
        self._last_ts = now_mono

        cfg = self._build_config()

        per_source = {eid: self._read_w(eid) for eid in self.sources}
        sources_raw = sum(per_source.values())
        ev_charger_w = self._read_w(self._opt(CONF_EV_CHARGER_POWER))

        if self.sources_include_ev_charger:
            # La sorgente misura già anche la EV Charger (es. contatore/prelievo rete):
            # scorporo la potenza EV Charger per ottenere i soli altri consumi, così da
            # non contarla due volte nel budget ed evitare un loop di feedback.
            sources_w = max(0.0, sources_raw - ev_charger_w)
            total_w = sources_raw
        else:
            sources_w = sources_raw
            total_w = sources_raw + ev_charger_w

        inp = BalancerInputs(sources_w=sources_w, ev_charger_w=ev_charger_w)
        target = next_current(cfg, inp, self.state, now_mono)

        # Attuazione: scrivi sulla EV Charger solo se abilitato e se il valore cambia.
        wb_number = self._opt(CONF_EV_CHARGER_CURRENT)
        if self.balancing_enabled and wb_number:
            current_set = self.hass.states.get(wb_number)
            current_val = _to_float(current_set.state) if current_set else None
            if current_val is None or int(current_val) != target:
                await self._write_current(wb_number, target)

        now_local = dt_util.now()
        scheme = self.tariff_scheme
        holidays = holidays_for_scheme(scheme, now_local.year)
        band = active_band(scheme, now_local, holidays)

        return {
            "per_source": per_source,
            "sources_w": sources_w,
            "ev_charger_w": ev_charger_w,
            "total_w": total_w,
            "applied_current": self.state.applied_current,
            "target_current": target,
            "charging_blocked": self.state.charging_blocked,
            "active_band": band,
            "active_band_rank": scheme.rank_of(band),
            "reasons": list(self.state.reasons),
            "elapsed_s": elapsed,
            "balancing_enabled": self.balancing_enabled,
            "max_power_w": cfg.max_power_w,
        }

    async def _write_current(self, number_entity: str, amps: int) -> None:
        try:
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": number_entity, "value": amps},
                blocking=True,
            )
            _LOGGER.debug("EV Charger %s -> %sA (%s)", number_entity, amps, self.state.reasons)
        except Exception as err:  # noqa: BLE001 - non deve mai far cadere il ciclo
            _LOGGER.warning("Impossibile impostare %s a %sA: %s", number_entity, amps, err)

    async def async_set_balancing(self, enabled: bool) -> None:
        """Abilita/disabilita l'attuazione (chiamato dallo switch)."""
        self.balancing_enabled = enabled
        await self.async_request_refresh()

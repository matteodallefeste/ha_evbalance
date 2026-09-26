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

from .actuator import ChargerActuator, EntityActuator, OcppActuator
from .balancer import (
    BalancerConfig,
    BalancerInputs,
    BalancerState,
    next_current,
    watts_per_amp,
)
from .const import (
    CONF_ALLOWED_BANDS,
    CONF_CONTROL_MODE,
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
    CONF_EV_CHARGER_SWITCH,
    CONF_EV_CHARGER_SWITCH_INVERT,
    DEFAULT_ALLOWED_BANDS,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CURRENT_STEPS,
    DEFAULT_EV_CHARGER_SWITCH_INVERT,
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
    MODE_OCPP,
)
from .energy import TariffScheme, active_band, band_allowed
from .ocpp_messages import STATUS_SUSPENDED_EV, session_over
from .tariff_loader import holidays_for_scheme, resolve_scheme

_LOGGER = logging.getLogger(__name__)

# Quanto deve durare l'immobilita' dell'auto (SuspendedEV) prima di dire che la
# ricarica e' finita e spegnere "ricarica ora" (s). Un SuspendedEV di passaggio
# -- l'auto che si sveglia, o che condiziona la batteria -- non deve bastare.
CHARGE_NOW_IDLE_SECONDS = 300.0


def _to_float(value: object) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f


class EVBalanceCoordinator(DataUpdateCoordinator[dict]):
    """Cuore dell'integrazione."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, csms=None
    ) -> None:
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
        # Stop manuale: si sovrappone alla decisione del bilanciatore e la
        # ricarica resta ferma finché non viene riattivata. Non è la stessa cosa
        # di `balancing_enabled`, che invece smette del tutto di comandare.
        self.charging_allowed = True
        # "Ricarica ora": scavalca le fasce orarie fino a fine sessione. Non
        # viene ripristinato al riavvio, perche' la sessione a cui si riferiva
        # non c'e' piu'.
        self.charge_now = False
        self._charge_now_idle_since: float | None = None
        self._last_ts: float | None = None
        self.csms = csms
        self.actuator: ChargerActuator = self._build_actuator()

    @property
    def control_mode(self) -> str:
        return self._opt(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)

    def _build_actuator(self) -> ChargerActuator:
        """Sceglie come comandare la wallbox in base alla modalita' configurata."""
        if self.control_mode == MODE_OCPP and self.csms is not None:
            voltage = float(self._opt(CONF_VOLTAGE, DEFAULT_VOLTAGE))
            return OcppActuator(
                self.csms,
                min_current=int(self._opt(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT)),
                voltage=voltage,
                phases=int(self._opt(CONF_PHASES, DEFAULT_PHASES)),
            )
        return EntityActuator(
            self.hass,
            number_entity=self._opt(CONF_EV_CHARGER_CURRENT),
            switch_entity=self._opt(CONF_EV_CHARGER_SWITCH),
            switch_invert=bool(
                self._opt(CONF_EV_CHARGER_SWITCH_INVERT, DEFAULT_EV_CHARGER_SWITCH_INVERT)
            ),
        )

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
    def allowed_bands(self) -> list[str]:
        """Fasce in cui si vuole ricaricare (vuoto = tutte)."""
        return list(self._opt(CONF_ALLOWED_BANDS, DEFAULT_ALLOWED_BANDS) or [])

    @property
    def tariff_preset(self) -> str:
        return self._opt(CONF_TARIFF_PRESET, DEFAULT_TARIFF_PRESET)

    @property
    def tariff_scheme(self) -> TariffScheme:
        """Schema tariffario risolto (preset built-in o custom dalle options)."""
        return resolve_scheme(
            self.hass, self.tariff_preset, self._opt(CONF_TARIFFS, None)
        )

    def _read_w(self, entity_id: str | None) -> float:
        """Legge un sensore di potenza in W (0 se non configurato o assente)."""
        if not entity_id:
            return 0.0
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

    def _charge_now_expired(self, now: float) -> bool:
        """"Ricarica ora" ha esaurito il suo compito?

        Dura una sessione sola: finisce col cavo staccato o con la transazione
        chiusa, e a cavo inserito quando la fine carica la dichiara l'auto
        smettendo di assorbire (``SuspendedEV``) per un tempo che escluda una
        pausa di passaggio. Se la wallbox non e' collegata non si sa nulla e si
        aspetta: una disconnessione non deve annullare la scelta dell'utente.
        """
        session = self.csms.charge_point if self.csms is not None else None
        if session is None:
            self._charge_now_idle_since = None
            return False

        if session_over(session.status):
            return True

        if session.status != STATUS_SUSPENDED_EV:
            self._charge_now_idle_since = None
            return False

        if self._charge_now_idle_since is None:
            self._charge_now_idle_since = now
        return now - self._charge_now_idle_since >= CHARGE_NOW_IDLE_SECONDS

    async def _async_update_data(self) -> dict:
        now_mono = time.monotonic()
        elapsed = 0.0 if self._last_ts is None else now_mono - self._last_ts
        self._last_ts = now_mono

        cfg = self._build_config()

        per_source = {eid: self._read_w(eid) for eid in self.sources}
        sources_raw = sum(per_source.values())

        # In OCPP la potenza la misura la wallbox stessa: il sensore esterno
        # resta come riserva, per le wallbox che non mandano telemetria.
        ev_charger_w = self.actuator.read_power_w()
        if ev_charger_w is None:
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

        now_local = dt_util.now()
        scheme = self.tariff_scheme
        holidays = holidays_for_scheme(scheme, now_local.year)
        band = active_band(scheme, now_local, holidays)

        # La fascia oraria concorre alla decisione, quindi va calcolata prima
        # di attuare: fuori dalle fasce ammesse la ricarica si ferma.
        allowed = self.allowed_bands
        band_ok = band_allowed(band, allowed)

        # "Ricarica ora" scavalca le fasce, non il bilanciamento: la corrente
        # resta quella che il budget concede, così il contatore non scatta.
        if self.charge_now and self._charge_now_expired(now_mono):
            self.charge_now = False
            _LOGGER.debug("Sessione finita: 'ricarica ora' torna alle fasce")
        if self.charge_now:
            band_ok = True

        # Attuazione (solo se il bilanciamento è abilitato dallo switch).
        paused = self.state.charging_blocked or not self.charging_allowed or not band_ok
        if not self.charging_allowed:
            self.state.reasons.append("ricarica fermata manualmente")
        if self.charge_now:
            self.state.reasons.append("ricarica ora attiva: fasce ignorate")
        if not band_ok:
            self.state.reasons.append(
                f"fascia {band} non tra quelle ammesse ({', '.join(allowed)})"
            )
        if self.balancing_enabled:
            await self.actuator.async_apply(target, paused)

        data = {
            "per_source": per_source,
            "sources_w": sources_w,
            "ev_charger_w": ev_charger_w,
            "total_w": total_w,
            "applied_current": self.state.applied_current,
            "target_current": target,
            "charging_blocked": self.state.charging_blocked,
            "charging_allowed": self.charging_allowed,
            "charging_paused": paused,
            "charge_now": self.charge_now,
            "band_allowed": band_ok,
            "allowed_bands": allowed,
            "active_band": band,
            "active_band_rank": scheme.rank_of(band),
            "reasons": list(self.state.reasons),
            "elapsed_s": elapsed,
            "balancing_enabled": self.balancing_enabled,
            "max_power_w": cfg.max_power_w,
            "control_mode": self.control_mode,
            "charger_available": self.actuator.available,
        }
        data.update(self.actuator.diagnostics())
        return data

    async def async_set_charging_allowed(self, allowed: bool) -> None:
        """Consente o ferma la ricarica a mano (chiamato dallo switch)."""
        self.charging_allowed = allowed
        await self.async_request_refresh()

    async def async_set_charge_now(self, enabled: bool) -> None:
        """Avvia (o annulla) la ricarica fuori fascia (chiamato dallo switch)."""
        self.charge_now = enabled
        self._charge_now_idle_since = None
        await self.async_request_refresh()

    async def async_set_balancing(self, enabled: bool) -> None:
        """Abilita/disabilita l'attuazione (chiamato dallo switch)."""
        self.balancing_enabled = enabled
        await self.async_request_refresh()

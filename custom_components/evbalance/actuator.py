# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Attuatori: come il bilanciatore parla alla wallbox.

Il calcolo di quanti Ampere concedere sta in ``balancer.py`` e non sa nulla di
come vengono applicati. Qui ci sono i due modi di applicarli:

* ``EntityActuator`` scrive su un ``number`` e usa uno ``switch`` per la pausa
  (comportamento storico, va bene per le wallbox gia' integrate in HA);
* ``OcppActuator`` parla OCPP 1.6J direttamente con la wallbox, dove la pausa e'
  semplicemente un limite di 0 A e non serve alcuno switch.

Entrambi espongono la stessa interfaccia, cosi' il coordinator non deve sapere
quale dei due sta usando.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .ocpp_messages import effective_power_w, sanitize_limit

_LOGGER = logging.getLogger(__name__)

# Ogni quanto insistere quando la wallbox non applica il limite chiesto (s).
RETRY_INTERVAL = 30.0


class ChargerActuator:
    """Interfaccia comune agli attuatori."""

    #: True quando la wallbox e' raggiungibile e i comandi hanno senso.
    available: bool = True

    async def async_apply(self, target_amps: int, paused: bool) -> None:
        """Porta la wallbox allo stato voluto. Non deve mai sollevare."""
        raise NotImplementedError

    def read_power_w(self) -> float | None:
        """Potenza misurata dalla wallbox, o None se non la conosce."""
        return None

    def diagnostics(self) -> dict[str, Any]:
        """Informazioni da esporre nelle entita' e nel pannello."""
        return {}


class EntityActuator(ChargerActuator):
    """Wallbox pilotata tramite entita' di Home Assistant.

    Scrive la corrente su un ``number`` e, quando c'e', mette in pausa con uno
    ``switch``: scrivere un valore sotto il minimo non ferma la ricarica, la
    wallbox continuerebbe a erogare al minimo facendo scattare il contatore.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        number_entity: str | None,
        switch_entity: str | None,
        switch_invert: bool,
    ) -> None:
        self.hass = hass
        self.number_entity = number_entity
        self.switch_entity = switch_entity
        self.switch_invert = switch_invert
        self._requested_amps: int | None = None
        self.last_error: str | None = None
        self.mismatch: str | None = None

    async def async_apply(self, target_amps: int, paused: bool) -> None:
        self._check_previous_write()

        if self.switch_entity:
            if paused:
                # In pausa non tocchiamo la corrente: resta l'ultimo valore
                # valido, pronto per la ripresa.
                await self._async_set_charging(False)
            else:
                # Prima una corrente valida, poi la ripresa: cosi' non si
                # riparte mai sopra il budget.
                if self.number_entity:
                    await self._async_sync_current(target_amps)
                await self._async_set_charging(True)
        elif self.number_entity:
            await self._async_sync_current(target_amps)

    def _check_previous_write(self) -> None:
        """Il valore scritto al giro precedente e' stato davvero accettato?

        Le wallbox con minimo a 6 A rifiutano la scrittura di valori inferiori:
        senza questo controllo il bilanciatore crede di aver messo in pausa
        mentre la wallbox continua a erogare.
        """
        self.mismatch = None
        if self._requested_amps is None or not self.number_entity:
            return
        state = self.hass.states.get(self.number_entity)
        if state is None:
            return
        try:
            actual = int(float(state.state))
        except (TypeError, ValueError):
            return
        if actual != self._requested_amps:
            self.mismatch = (
                f"richiesti {self._requested_amps}A su {self.number_entity} "
                f"ma l'entita' vale {actual}A"
            )
            _LOGGER.warning(
                "La wallbox non ha accettato il valore richiesto: %s. "
                "Se il minimo della wallbox e' piu' alto, configura lo switch di "
                "pausa oppure passa al controllo OCPP.",
                self.mismatch,
            )

    async def _async_sync_current(self, amps: int) -> None:
        state = self.hass.states.get(self.number_entity)  # type: ignore[arg-type]
        try:
            current = int(float(state.state)) if state else None
        except (TypeError, ValueError):
            current = None
        if current == amps:
            self._requested_amps = amps
            return

        self._requested_amps = amps
        try:
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": self.number_entity, "value": amps},
                blocking=True,
            )
            self.last_error = None
            _LOGGER.debug("EV Charger %s -> %sA", self.number_entity, amps)
        except Exception as err:  # noqa: BLE001 - non deve mai far cadere il ciclo
            self.last_error = str(err)
            _LOGGER.warning(
                "Impossibile impostare %s a %sA: %s", self.number_entity, amps, err
            )

    async def _async_set_charging(self, charging: bool) -> None:
        """Attiva o mette in pausa la ricarica tramite lo switch della wallbox."""
        want_on = charging != self.switch_invert   # XOR: invert ribalta ON/OFF
        desired = "on" if want_on else "off"
        state = self.hass.states.get(self.switch_entity)  # type: ignore[arg-type]
        if state is not None and state.state == desired:
            return
        try:
            await self.hass.services.async_call(
                "homeassistant",
                "turn_on" if want_on else "turn_off",
                {"entity_id": self.switch_entity},
                blocking=True,
            )
            self.last_error = None
            _LOGGER.debug(
                "EV Charger switch %s -> %s (ricarica=%s)",
                self.switch_entity,
                desired,
                charging,
            )
        except Exception as err:  # noqa: BLE001
            self.last_error = str(err)
            _LOGGER.warning(
                "Impossibile impostare lo switch %s a %s: %s",
                self.switch_entity,
                desired,
                err,
            )

    def diagnostics(self) -> dict[str, Any]:
        return {
            "control_mode": "entities",
            "actuator_error": self.last_error,
            "actuator_mismatch": self.mismatch,
        }


class OcppActuator(ChargerActuator):
    """Wallbox pilotata via OCPP 1.6J: il limite e' un charging profile.

    La pausa non ha bisogno di uno switch: e' un limite di 0 A, che sospende
    l'erogazione lasciando aperta la sessione (SuspendedEVSE). E' l'unico modo
    corretto di stare sotto il minimo della wallbox.
    """

    def __init__(
        self,
        csms,
        *,
        min_current: int,
        voltage: float,
        phases: int,
    ) -> None:
        self.csms = csms
        self.min_current = min_current
        self.voltage = voltage
        self.phases = phases
        self._last_sent: float | None = None
        self._last_attempt: float = 0.0

    @property
    def available(self) -> bool:  # type: ignore[override]
        return self.csms.connected

    async def async_apply(self, target_amps: int, paused: bool) -> None:
        session = self.csms.charge_point
        if session is None:
            self._last_sent = None   # alla riconnessione ripartiamo da capo
            return

        limit = sanitize_limit(0 if paused else target_amps, self.min_current)
        now = time.monotonic()

        changed = self._last_sent is None or abs(limit - self._last_sent) > 0.05
        # Se la wallbox non sta rispettando il limite, insistiamo: e' il caso
        # noto delle wallbox che, riprendendo da 0 A, ignorano il nuovo valore.
        unconfirmed = not session.limit_confirmed and (now - self._last_attempt) >= RETRY_INTERVAL

        if not changed and not unconfirmed:
            return

        if unconfirmed and not changed:
            _LOGGER.warning(
                "La wallbox non sta applicando il limite di %.0fA (%s): rimando il profilo",
                limit,
                session.last_limit_error,
            )

        self._last_attempt = now
        if await session.async_set_limit(limit):
            self._last_sent = limit
        else:
            self._last_sent = None   # da ritentare al giro successivo

    def read_power_w(self) -> float | None:
        session = self.csms.charge_point
        if session is None:
            return None
        if not session.vehicle_connected:
            return 0.0
        return effective_power_w(session.snapshot, self.voltage, self.phases)

    def diagnostics(self) -> dict[str, Any]:
        """Telemetria OCPP raccolta sotto una sola chiave, per non collidere
        con i campi del bilanciatore."""
        session = self.csms.charge_point
        if session is None:
            return {"control_mode": "ocpp", "ocpp_connected": False, "ocpp": {}}
        return {
            "control_mode": "ocpp",
            "ocpp_connected": True,
            "ocpp": session.telemetry(),
        }

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""CSMS OCPP 1.6J integrato: la wallbox si collega direttamente a EV Balance.

Il trasporto e' aiohttp, gia' presente in Home Assistant: nessuna dipendenza
nuova da dichiarare nel manifest e nessun conflitto di versione. Il server puo'
ascoltare su una porta dedicata (default) oppure agganciarsi alla porta di Home
Assistant, utile quando HA gira in container e pubblicare una porta in piu' e'
scomodo.

La logica dei payload sta in ``ocpp_messages.py``; qui c'e' solo la connessione,
il ciclo dei messaggi e lo stato della sessione.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from aiohttp import WSMsgType, web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback

from .ocpp_messages import (
    CALL,
    CALLERROR,
    CALLRESULT,
    CAPABILITY_KEYS,
    CONNECTED_STATES,
    MeterSnapshot,
    build_charging_profile,
    build_clear_profile,
    check_limit_applied,
    next_sampled_data,
    parse_configuration,
    parse_meter_values,
    snapshot_without_flow,
    supports_phase_switching,
    supports_remote_trigger,
    supports_smart_charging,
)

_LOGGER = logging.getLogger(__name__)

OCPP_SUBPROTOCOL = "ocpp1.6"

# Intervallo di Heartbeat consegnato alla wallbox nel BootNotification (s).
BOOT_HEARTBEAT_INTERVAL = 60
# Oltre questo silenzio consideriamo morta la connessione e la chiudiamo (s).
RECEIVE_TIMEOUT = 300.0
# Tempo massimo di attesa di una risposta a un nostro comando (s).
CALL_TIMEOUT = 30.0
# Pausa prima di interrogare una wallbox appena avviata (s).
BOOT_SETTLE_SECONDS = 1.0

# Azioni che accettiamo dalla wallbox ma su cui non abbiamo nulla da dire.
_ACK_ONLY = frozenset(
    {"FirmwareStatusNotification", "DiagnosticsStatusNotification", "SecurityEventNotification"}
)


def _now_iso() -> str:
    """Istante corrente nel formato richiesto dall'OCPP."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class ChargePointSession:
    """Una wallbox connessa: ciclo dei messaggi e stato corrente."""

    def __init__(self, cp_id: str, ws: web.WebSocketResponse, csms: "OcppCsms") -> None:
        self.cp_id = cp_id
        self.ws = ws
        self.csms = csms

        self._pending: dict[str, asyncio.Future] = {}
        self._call_lock = asyncio.Lock()   # l'OCPP vuole una Call per volta

        # --- stato osservato ---
        self.boot_info: dict[str, Any] = {}
        self.configuration: dict[str, str] = {}
        self.status: str = "Unavailable"
        self.error_code: str | None = None
        self.snapshot = MeterSnapshot()
        self.transaction_id: int | None = None
        self.transaction_start_wh: float | None = None
        self.id_tag: str | None = None
        self.connected_since: float = time.monotonic()
        self.last_message: float = time.monotonic()

        # --- stato dell'attuazione ---
        self.desired_limit_a: float | None = None
        self.limit_confirmed: bool = True
        self.last_limit_error: str | None = None
        self._sampled_data: str | None = None
        self._tx_counter = 0

    # ------------------------------------------------------------------
    # Proprieta' derivate
    # ------------------------------------------------------------------

    @property
    def vehicle_connected(self) -> bool:
        return self.status in CONNECTED_STATES

    @property
    def charging(self) -> bool:
        return self.status == "Charging"

    @property
    def session_energy_kwh(self) -> float | None:
        """Energia della transazione in corso, in kWh."""
        if self.transaction_start_wh is None or self.snapshot.energy_wh is None:
            return None
        return max(0.0, (self.snapshot.energy_wh - self.transaction_start_wh) / 1000.0)

    @property
    def supports_smart_charging(self) -> bool:
        return supports_smart_charging(self.configuration)

    @property
    def supports_phase_switching(self) -> bool:
        return supports_phase_switching(self.configuration)

    # ------------------------------------------------------------------
    # Invio
    # ------------------------------------------------------------------

    async def call(self, action: str, payload: dict) -> dict | None:
        """Manda una Call e aspetta la CallResult. None se fallisce."""
        if self.ws.closed:
            return None

        async with self._call_lock:
            uid = uuid.uuid4().hex
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[uid] = future
            try:
                await self.ws.send_json([CALL, uid, action, payload])
            except (ConnectionResetError, RuntimeError) as err:
                self._pending.pop(uid, None)
                _LOGGER.warning("%s: invio di %s fallito: %s", self.cp_id, action, err)
                return None

            _LOGGER.debug("%s --> %s %s", self.cp_id, action, payload)
            try:
                return await asyncio.wait_for(future, timeout=CALL_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.warning("%s: nessuna risposta a %s", self.cp_id, action)
                return None
            finally:
                self._pending.pop(uid, None)

    # ------------------------------------------------------------------
    # Attuazione
    # ------------------------------------------------------------------

    async def async_set_limit(self, amps: float, phases: int | None = None) -> bool:
        """Impone un limite di corrente (0 = pausa) via SetChargingProfile."""
        payload = build_charging_profile(
            amps,
            connector=self.csms.connector,
            phases=phases,
            purpose=self.csms.profile_purpose,
            transaction_id=self.transaction_id,
        )
        result = await self.call("SetChargingProfile", payload)
        status = (result or {}).get("status")

        if status == "Accepted":
            self.desired_limit_a = amps
            self.csms.remember_limit(self.cp_id, amps)
            self.limit_confirmed = True
            self.last_limit_error = None
            _LOGGER.debug("%s: limite impostato a %.1fA", self.cp_id, amps)
            await self._async_request_meter_values()
            return True

        if result is None:
            self.last_limit_error = "nessuna risposta dalla wallbox"
        else:
            self.last_limit_error = f"SetChargingProfile {status}"
        self.limit_confirmed = False
        _LOGGER.warning(
            "%s: limite %.1fA rifiutato (%s)", self.cp_id, amps, self.last_limit_error
        )
        return False

    async def async_clear_profiles(self) -> bool:
        result = await self.call("ClearChargingProfile", build_clear_profile())
        return (result or {}).get("status") == "Accepted"

    async def async_stop_transaction(self) -> bool:
        """Ultima risorsa: chiude la sessione se il profilo a 0 A non funziona."""
        if self.transaction_id is None:
            return False
        result = await self.call(
            "RemoteStopTransaction", {"transactionId": self.transaction_id}
        )
        return (result or {}).get("status") == "Accepted"

    async def _async_request_meter_values(self) -> None:
        """Chiede una lettura fresca, per verificare subito l'esito del comando."""
        if not supports_remote_trigger(self.configuration):
            return
        await self.call(
            "TriggerMessage",
            {"requestedMessage": "MeterValues", "connectorId": self.csms.connector},
        )

    def verify_limit(self) -> None:
        """Confronta il limite chiesto con quello che la wallbox sta facendo."""
        if self.desired_limit_a is None or not self.vehicle_connected:
            return
        check = check_limit_applied(self.snapshot, self.desired_limit_a)
        if not check.matches:
            self.limit_confirmed = False
            self.last_limit_error = check.detail
        else:
            self.limit_confirmed = True
            self.last_limit_error = None

    # ------------------------------------------------------------------
    # Ciclo di ricezione
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Legge i messaggi finche' la wallbox resta connessa."""
        async for msg in self.ws:
            self.last_message = time.monotonic()
            if msg.type != WSMsgType.TEXT:
                if msg.type == WSMsgType.ERROR:
                    _LOGGER.warning("%s: errore websocket: %s", self.cp_id, self.ws.exception())
                continue
            try:
                message = msg.json()
            except ValueError:
                _LOGGER.warning("%s: messaggio non JSON scartato", self.cp_id)
                continue
            if not isinstance(message, list) or not message:
                continue
            await self._async_route(message)

    async def _async_route(self, message: list) -> None:
        kind = message[0]

        if kind == CALL and len(message) >= 4:
            _, uid, action, payload = message[:4]
            _LOGGER.debug("%s <-- %s %s", self.cp_id, action, payload)
            try:
                response = await self._async_handle(action, payload or {})
            except Exception:  # noqa: BLE001 - un handler rotto non deve chiudere la sessione
                _LOGGER.exception("%s: errore gestendo %s", self.cp_id, action)
                await self.ws.send_json(
                    [CALLERROR, uid, "InternalError", "handler failed", {}]
                )
                return
            if response is None:
                await self.ws.send_json(
                    [CALLERROR, uid, "NotImplemented", f"{action} non gestita", {}]
                )
            else:
                await self.ws.send_json([CALLRESULT, uid, response])
            self.csms.notify_update()
            return

        if kind == CALLRESULT and len(message) >= 3:
            future = self._pending.get(message[1])
            if future and not future.done():
                future.set_result(message[2])
            return

        if kind == CALLERROR and len(message) >= 4:
            _LOGGER.warning(
                "%s: la wallbox ha rifiutato un comando: %s %s",
                self.cp_id,
                message[2],
                message[3],
            )
            future = self._pending.get(message[1])
            if future and not future.done():
                future.set_result(None)

    async def _async_handle(self, action: str, payload: dict) -> dict | None:
        """Risposta a una Call della wallbox. None = azione non supportata."""
        if action == "BootNotification":
            self.boot_info = {
                "vendor": payload.get("chargePointVendor"),
                "model": payload.get("chargePointModel"),
                "firmware": payload.get("firmwareVersion"),
                "serial": payload.get("chargePointSerialNumber"),
            }
            _LOGGER.info(
                "Wallbox %s connessa: %s %s (firmware %s)",
                self.cp_id,
                self.boot_info["vendor"],
                self.boot_info["model"],
                self.boot_info["firmware"],
            )
            self.csms.hass.async_create_task(self._async_after_boot())
            return {
                "currentTime": _now_iso(),
                "interval": BOOT_HEARTBEAT_INTERVAL,
                "status": "Accepted",
            }

        if action == "Heartbeat":
            return {"currentTime": _now_iso()}

        if action == "StatusNotification":
            if int(payload.get("connectorId", self.csms.connector)) in (
                0,
                self.csms.connector,
            ):
                self.status = str(payload.get("status", self.status))
                error = payload.get("errorCode")
                self.error_code = None if error in (None, "NoError") else str(error)
                _LOGGER.debug("%s: stato -> %s", self.cp_id, self.status)
            return {}

        if action == "MeterValues":
            self.snapshot = self.snapshot.merged_with(parse_meter_values(payload))
            self.verify_limit()
            return {}

        if action == "StartTransaction":
            self._tx_counter += 1
            self.transaction_id = self._tx_counter
            self.id_tag = payload.get("idTag")
            meter_start = payload.get("meterStart")
            self.transaction_start_wh = (
                float(meter_start) if meter_start is not None else None
            )
            _LOGGER.info(
                "%s: ricarica avviata (transazione %s, tag %s)",
                self.cp_id,
                self.transaction_id,
                self.id_tag,
            )
            # Il profilo va riapplicato: alcune wallbox azzerano i limiti a
            # inizio transazione, ripartendo a fondo scala.
            if self.desired_limit_a is not None:
                self.csms.hass.async_create_task(
                    self.async_set_limit(self.desired_limit_a)
                )
            return {
                "transactionId": self.transaction_id,
                "idTagInfo": {"status": "Accepted"},
            }

        if action == "StopTransaction":
            _LOGGER.info(
                "%s: ricarica conclusa (motivo: %s)",
                self.cp_id,
                payload.get("reason", "non indicato"),
            )
            meter_stop = payload.get("meterStop")
            if meter_stop is not None:
                self.snapshot.energy_wh = float(meter_stop)
            self.snapshot = snapshot_without_flow(self.snapshot)
            self.transaction_id = None
            self.transaction_start_wh = None
            return {"idTagInfo": {"status": "Accepted"}}

        if action == "Authorize":
            # Autorizzazione locale: chi ha accesso fisico alla presa carica.
            # La selezione per utente arrivera' con la lista tag, non qui.
            return {"idTagInfo": {"status": "Accepted"}}

        if action == "DataTransfer":
            return {"status": "UnknownVendorId"}

        if action in _ACK_ONLY:
            return {}

        _LOGGER.debug("%s: azione non gestita: %s", self.cp_id, action)
        return None

    # ------------------------------------------------------------------
    # Sequenza di avvio
    # ------------------------------------------------------------------

    async def _async_after_boot(self) -> None:
        """Legge le capability e imposta la telemetria che ci serve."""
        # La risposta al BootNotification deve partire prima dei nostri comandi:
        # una wallbox non ancora "Accepted" e' in diritto di rifiutarli.
        await asyncio.sleep(BOOT_SETTLE_SECONDS)

        result = await self.call("GetConfiguration", {"key": list(CAPABILITY_KEYS)})
        if result:
            self.configuration = parse_configuration(result)
            _LOGGER.info(
                "%s: capability -> smart charging=%s, commutazione fasi=%s",
                self.cp_id,
                "si" if self.supports_smart_charging else "no",
                "si" if self.supports_phase_switching else "no",
            )
            if not self.supports_smart_charging:
                _LOGGER.warning(
                    "%s non dichiara il profilo SmartCharging: il controllo della "
                    "corrente potrebbe non funzionare",
                    self.cp_id,
                )

        await self._async_configure(
            "MeterValueSampleInterval", str(self.csms.meter_interval)
        )
        await self._async_negotiate_sampled_data()

        # Dopo un riavvio della wallbox i profili possono essere spariti.
        if self.desired_limit_a is not None:
            await self.async_set_limit(self.desired_limit_a)

        self.csms.notify_update()

    async def _async_configure(self, key: str, value: str) -> bool:
        result = await self.call("ChangeConfiguration", {"key": key, "value": value})
        status = (result or {}).get("status")
        if status in ("Accepted", "RebootRequired"):
            self.configuration[key] = value
            return True
        _LOGGER.debug("%s: %s=%s rifiutato (%s)", self.cp_id, key, value, status)
        return False

    async def _async_negotiate_sampled_data(self) -> None:
        """Chiede i measurand piu' ricchi possibili, scendendo se rifiutati."""
        candidate = next_sampled_data(None)
        while candidate is not None:
            if await self._async_configure("MeterValuesSampledData", candidate):
                self._sampled_data = candidate
                _LOGGER.debug("%s: telemetria richiesta -> %s", self.cp_id, candidate)
                return
            candidate = next_sampled_data(candidate)
        _LOGGER.warning(
            "%s: nessun elenco di measurand accettato, la telemetria sara' parziale",
            self.cp_id,
        )

    # ------------------------------------------------------------------

    def telemetry(self) -> dict[str, Any]:
        """Fotografia per il coordinator e le entita'."""
        return {
            "cp_id": self.cp_id,
            "status": self.status,
            "error_code": self.error_code,
            "vehicle_connected": self.vehicle_connected,
            "charging": self.charging,
            "power_w": self.snapshot.power_w,
            "currents": dict(self.snapshot.currents),
            "voltages": dict(self.snapshot.voltages),
            "current_offered_a": self.snapshot.current_offered_a,
            "energy_wh": self.snapshot.energy_wh,
            "soc": self.snapshot.soc,
            "transaction_id": self.transaction_id,
            "session_energy_kwh": self.session_energy_kwh,
            "id_tag": self.id_tag,
            "desired_limit_a": self.desired_limit_a,
            "limit_confirmed": self.limit_confirmed,
            "limit_error": self.last_limit_error,
            "boot_info": dict(self.boot_info),
            "supports_smart_charging": self.supports_smart_charging,
            "supports_phase_switching": self.supports_phase_switching,
        }


class OcppCsms:
    """Server OCPP: accetta le wallbox e ne tiene le sessioni."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        port: int,
        cp_id: str = "",
        password: str = "",
        connector: int = 1,
        meter_interval: int = 10,
        profile_purpose: str = "TxDefaultProfile",
        use_ha_port: bool = False,
    ) -> None:
        self.hass = hass
        self.port = port
        self.expected_cp_id = (cp_id or "").strip()
        self.password = password or ""
        self.connector = connector
        self.meter_interval = meter_interval
        self.profile_purpose = profile_purpose
        self.use_ha_port = use_ha_port

        self._sessions: dict[str, ChargePointSession] = {}
        # Ultimo limite accettato per wallbox: sopravvive alla disconnessione,
        # cosi' una wallbox che si riavvia non riparte senza limite.
        self._desired_limits: dict[str, float] = {}
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Ciclo di vita
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        if self.use_ha_port:
            _LOGGER.info(
                "OCPP in ascolto sulla porta di Home Assistant, URL: "
                "ws://<indirizzo-ha>:8123%s",
                OcppView.url.replace("{cp_id}", self.expected_cp_id or "EVBALANCE"),
            )
            return

        app = web.Application()
        app.router.add_get("/{tail:.*}", self._async_handle_request)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="0.0.0.0", port=self.port)
        try:
            await self._site.start()
        except OSError as err:
            self._site = None
            raise RuntimeError(
                f"impossibile aprire la porta {self.port} per il server OCPP: {err}"
            ) from err
        _LOGGER.info(
            "Server OCPP in ascolto sulla porta %s (URL per la wallbox: "
            "ws://<indirizzo-ha>:%s/%s)",
            self.port,
            self.port,
            self.expected_cp_id or "EVBALANCE",
        )

    async def async_stop(self) -> None:
        for session in list(self._sessions.values()):
            try:
                await session.ws.close()
            except Exception:  # noqa: BLE001 - stiamo comunque spegnendo
                pass
        self._sessions.clear()
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        _LOGGER.debug("Server OCPP fermato")

    # ------------------------------------------------------------------
    # Sessioni
    # ------------------------------------------------------------------

    @property
    def charge_point(self) -> ChargePointSession | None:
        """La wallbox da pilotare: quella configurata, o la prima connessa."""
        if self.expected_cp_id:
            return self._sessions.get(self.expected_cp_id)
        return next(iter(self._sessions.values()), None)

    @property
    def connected(self) -> bool:
        return self.charge_point is not None

    @callback
    def remember_limit(self, cp_id: str, amps: float) -> None:
        """Registra l'ultimo limite accettato, per riapplicarlo dopo un riavvio."""
        self._desired_limits[cp_id] = amps

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Registra un callback da chiamare a ogni novita' dalla wallbox."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def notify_update(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - un ascoltatore rotto non ferma gli altri
                _LOGGER.exception("Errore in un ascoltatore OCPP")

    # ------------------------------------------------------------------
    # Handshake
    # ------------------------------------------------------------------

    def _authorized(self, request: web.Request, cp_id: str) -> bool:
        """Verifica la basic auth quando e' stata configurata una password."""
        if not self.password:
            return True
        header = request.headers.get("Authorization", "")
        scheme, _, blob = header.partition(" ")
        if scheme.lower() != "basic":
            _LOGGER.warning(
                "%s: password configurata ma la wallbox non manda credenziali", cp_id
            )
            return False
        try:
            decoded = base64.b64decode(blob).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        _, _, provided = decoded.partition(":")
        if hmac.compare_digest(provided, self.password):
            return True
        _LOGGER.warning("%s: password OCPP errata", cp_id)
        return False

    async def _async_handle_request(self, request: web.Request) -> web.StreamResponse:
        cp_id = request.match_info.get("tail", "").strip("/").rsplit("/", 1)[-1]
        return await self.async_handle_ws(request, cp_id)

    async def async_handle_ws(
        self, request: web.Request, cp_id: str
    ) -> web.StreamResponse:
        """Accetta (o rifiuta) una wallbox, poi le gira il ciclo dei messaggi."""
        peer = request.remote
        cp_id = (cp_id or "").strip("/")
        _LOGGER.info("Connessione OCPP da %s, charge point %r", peer, cp_id)

        offered = request.headers.get("Sec-WebSocket-Protocol", "")
        if offered and OCPP_SUBPROTOCOL not in offered:
            _LOGGER.warning(
                "%s offre i subprotocolli %r invece di %s: procedo comunque",
                cp_id,
                offered,
                OCPP_SUBPROTOCOL,
            )

        if self.expected_cp_id and cp_id != self.expected_cp_id:
            _LOGGER.error(
                "Wallbox rifiutata: si presenta come %r ma la configurazione dice %r. "
                "I due valori devono coincidere (maiuscole comprese), oppure lascia "
                "vuoto il campo per accettare qualsiasi wallbox.",
                cp_id,
                self.expected_cp_id,
            )
            return web.Response(status=404, text="charge point id sconosciuto")

        if not self._authorized(request, cp_id):
            return web.Response(status=401, text="credenziali OCPP non valide")

        if not cp_id:
            cp_id = "EVBALANCE"

        ws = web.WebSocketResponse(
            protocols=(OCPP_SUBPROTOCOL,), receive_timeout=RECEIVE_TIMEOUT
        )
        await ws.prepare(request)

        session = ChargePointSession(cp_id, ws, self)
        # Riconnessione: non perdiamo il limite gia' deciso dal bilanciatore.
        session.desired_limit_a = self._desired_limits.get(cp_id)

        previous = self._sessions.get(cp_id)
        if previous is not None:
            # Doppia connessione dalla stessa wallbox: vale l'ultima.
            try:
                await previous.ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._sessions[cp_id] = session
        self.notify_update()

        try:
            await session.run()
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "%s: nessun messaggio per %.0fs, chiudo la connessione",
                cp_id,
                RECEIVE_TIMEOUT,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - la sessione non deve propagare errori a HA
            _LOGGER.exception("%s: sessione interrotta da un errore", cp_id)
        finally:
            if self._sessions.get(cp_id) is session:
                self._sessions.pop(cp_id, None)
            _LOGGER.info("Wallbox %s disconnessa", cp_id)
            self.notify_update()

        return ws


class OcppView(HomeAssistantView):
    """Endpoint OCPP sulla porta di Home Assistant (modalita' alternativa).

    Le view di HA non si possono togliere a caldo: viene registrata una volta
    sola e a ogni richiesta ritrova il CSMS attivo, cosi' un reload
    dell'integrazione non lascia in giro un riferimento morto.
    """

    url = "/api/evbalance/ocpp/{cp_id}"
    name = "api:evbalance:ocpp"
    requires_auth = False

    def __init__(self, get_csms: Callable[[], OcppCsms | None]) -> None:
        self._get_csms = get_csms

    async def get(self, request: web.Request, cp_id: str) -> web.StreamResponse:
        csms = self._get_csms()
        if csms is None:
            return web.Response(status=503, text="server OCPP non attivo")
        return await csms.async_handle_ws(request, cp_id)

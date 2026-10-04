"""Test funzionali del CSMS OCPP (`ocpp_server.py`).

Fanno girare davvero il server e gli fanno parlare una finta wallbox via
websocket: handshake, sequenza di boot, telemetria, applicazione dei limiti e
riconnessione. Home Assistant non serve (conftest ne stubba le poche API usate),
ma servono aiohttp e pytest-asyncio: senza, il modulo viene saltato.
"""

import asyncio
import base64
import contextlib
import importlib
import socket
import uuid

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("pytest_asyncio")

import aiohttp  # noqa: E402

from ocpp_messages import CALL, CALLERROR, CALLRESULT  # noqa: E402

# ocpp_server usa import relativi: va caricato dal package finto di conftest.
ocpp_server = importlib.import_module("evbalance_pkg.ocpp_server")
BOOT_SETTLE_SECONDS = ocpp_server.BOOT_SETTLE_SECONDS
OcppCsms = ocpp_server.OcppCsms


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeHass:
    """Il minimo che il server usa di Home Assistant."""

    def async_create_task(self, coro):
        return asyncio.get_running_loop().create_task(coro)


class FakeChargePoint:
    """Finta wallbox: risponde ai comandi del server e ne registra le richieste."""

    DEFAULTS = {
        "GetConfiguration": {
            "configurationKey": [
                {
                    "key": "SupportedFeatureProfiles",
                    "value": "Core,SmartCharging,RemoteTrigger",
                    "readonly": True,
                },
                {"key": "NumberOfConnectors", "value": "1", "readonly": True},
                {
                    "key": "ConnectorSwitch3to1PhaseSupported",
                    "value": "false",
                    "readonly": True,
                },
            ],
            "unknownKey": [],
        },
        "ChangeConfiguration": {"status": "Accepted"},
        "SetChargingProfile": {"status": "Accepted"},
        "ClearChargingProfile": {"status": "Accepted"},
        "TriggerMessage": {"status": "Accepted"},
        "RemoteStopTransaction": {"status": "Accepted"},
    }

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws = ws
        self.received: list[tuple[str, dict]] = []
        self.overrides: dict[str, dict] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        async for msg in self.ws:
            if msg.type is not aiohttp.WSMsgType.TEXT:
                continue
            message = msg.json()
            if message[0] == CALL:
                _, uid, action, payload = message
                self.received.append((action, payload))
                answer = self.overrides.get(action, self.DEFAULTS.get(action))
                if answer is None:
                    await self.ws.send_json(
                        [CALLERROR, uid, "NotImplemented", action, {}]
                    )
                else:
                    await self.ws.send_json([CALLRESULT, uid, answer])
            elif message[0] in (CALLRESULT, CALLERROR):
                future = self._pending.pop(message[1], None)
                if future and not future.done():
                    future.set_result(message[2] if message[0] == CALLRESULT else None)

    async def call(self, action: str, payload: dict) -> dict | None:
        uid = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[uid] = future
        await self.ws.send_json([CALL, uid, action, payload])
        return await asyncio.wait_for(future, timeout=5)

    def calls_for(self, action: str) -> list[dict]:
        return [payload for name, payload in self.received if name == action]

    async def wait_for(self, action: str, timeout: float = 5.0) -> dict:
        """Aspetta che il server mandi `action` e ne restituisce il payload."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            found = self.calls_for(action)
            if found:
                return found[-1]
            await asyncio.sleep(0.02)
        raise AssertionError(f"il server non ha mai mandato {action}")

    async def close(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        await self.ws.close()


@contextlib.asynccontextmanager
async def running_csms(**kwargs):
    """Avvia un CSMS su una porta libera e lo ferma a fine test."""
    csms = OcppCsms(FakeHass(), port=_free_port(), **kwargs)
    await csms.async_start()
    try:
        yield csms
    finally:
        await csms.async_stop()


@contextlib.asynccontextmanager
async def connected(csms: OcppCsms, cp_id: str = "EVB1", password: str | None = None):
    """Collega una finta wallbox e completa la sequenza di boot."""
    headers = {}
    if password is not None:
        token = base64.b64encode(f"{cp_id}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.ws_connect(
            f"http://127.0.0.1:{csms.port}/{cp_id}", protocols=("ocpp1.6",)
        ) as ws:
            cp = FakeChargePoint(ws)
            boot = await cp.call(
                "BootNotification",
                {
                    "chargePointVendor": "Wallbox",
                    "chargePointModel": "Pulsar Max",
                    "firmwareVersion": "6.1.2",
                },
            )
            assert boot["status"] == "Accepted"
            # Lascia completare la sequenza post-boot del server.
            await cp.wait_for("GetConfiguration", timeout=BOOT_SETTLE_SECONDS + 4)
            await cp.wait_for("ChangeConfiguration")
            try:
                yield cp
            finally:
                await cp.close()


# --- handshake ---------------------------------------------------------

@pytest.mark.asyncio
async def test_boot_registra_la_wallbox():
    async with running_csms() as csms:
        async with connected(csms) as cp:
            assert csms.connected
            session = csms.charge_point
            assert session.cp_id == "EVB1"
            assert session.boot_info["model"] == "Pulsar Max"
            assert session.supports_smart_charging
            assert not session.supports_phase_switching
            assert cp.calls_for("GetConfiguration")


@pytest.mark.asyncio
async def test_il_server_negozia_il_subprotocollo_ocpp():
    async with running_csms() as csms:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"http://127.0.0.1:{csms.port}/EVB1", protocols=("ocpp1.6",)
            ) as ws:
                assert ws.protocol == "ocpp1.6"


@pytest.mark.asyncio
async def test_wallbox_sconosciuta_viene_rifiutata():
    """Il Charge Point ID sbagliato e' la causa piu' comune di 'non si collega'."""
    async with running_csms(cp_id="ATTESA") as csms:
        async with aiohttp.ClientSession() as session:
            with pytest.raises(aiohttp.WSServerHandshakeError) as err:
                await session.ws_connect(
                    f"http://127.0.0.1:{csms.port}/DIVERSA", protocols=("ocpp1.6",)
                )
            assert err.value.status == 404


@pytest.mark.asyncio
async def test_id_vuoto_accetta_qualsiasi_wallbox():
    async with running_csms(cp_id="") as csms:
        async with connected(csms, cp_id="QUALSIASI"):
            assert csms.charge_point.cp_id == "QUALSIASI"


@pytest.mark.asyncio
async def test_password_sbagliata_rifiutata():
    async with running_csms(password="segreto") as csms:
        async with aiohttp.ClientSession() as session:
            with pytest.raises(aiohttp.WSServerHandshakeError) as err:
                await session.ws_connect(
                    f"http://127.0.0.1:{csms.port}/EVB1", protocols=("ocpp1.6",)
                )
            assert err.value.status == 401


@pytest.mark.asyncio
async def test_password_giusta_accettata():
    async with running_csms(password="segreto") as csms:
        async with connected(csms, password="segreto"):
            assert csms.connected


# --- telemetria --------------------------------------------------------

@pytest.mark.asyncio
async def test_stato_e_meter_values():
    async with running_csms() as csms:
        async with connected(csms) as cp:
            await cp.call(
                "StatusNotification",
                {"connectorId": 1, "errorCode": "NoError", "status": "Charging"},
            )
            await cp.call(
                "MeterValues",
                {
                    "connectorId": 1,
                    "meterValue": [
                        {
                            "timestamp": "2026-09-08T10:00:00Z",
                            "sampledValue": [
                                {
                                    "value": "3450",
                                    "measurand": "Power.Active.Import",
                                    "unit": "W",
                                },
                                {
                                    "value": "15",
                                    "measurand": "Current.Import",
                                    "unit": "A",
                                    "phase": "L1",
                                },
                            ],
                        }
                    ],
                },
            )
            telemetria = csms.charge_point.telemetry()
            assert telemetria["status"] == "Charging"
            assert telemetria["charging"] is True
            assert telemetria["vehicle_connected"] is True
            assert telemetria["power_w"] == 3450.0
            assert telemetria["currents"] == {"L1": 15.0}


@pytest.mark.asyncio
async def test_energia_di_sessione():
    async with running_csms() as csms:
        async with connected(csms) as cp:
            avvio = await cp.call(
                "StartTransaction",
                {
                    "connectorId": 1,
                    "idTag": "TAG1",
                    "meterStart": 1_000_000,
                    "timestamp": "2026-09-08T10:00:00Z",
                },
            )
            assert avvio["idTagInfo"]["status"] == "Accepted"
            transazione = avvio["transactionId"]

            await cp.call(
                "MeterValues",
                {
                    "connectorId": 1,
                    "meterValue": [
                        {
                            "sampledValue": [
                                {"value": "1005500", "unit": "Wh"}   # registro
                            ]
                        }
                    ],
                },
            )
            assert csms.charge_point.session_energy_kwh == pytest.approx(5.5)

            await cp.call(
                "StopTransaction",
                {
                    "transactionId": transazione,
                    "meterStop": 1_008_000,
                    "timestamp": "2026-09-08T11:00:00Z",
                    "reason": "Local",
                },
            )
            session = csms.charge_point
            assert session.transaction_id is None
            assert session.snapshot.power_w == 0.0
            assert session.snapshot.energy_wh == 1_008_000.0


@pytest.mark.asyncio
async def test_azione_sconosciuta_risponde_callerror():
    async with running_csms() as csms:
        async with connected(csms) as cp:
            assert await cp.call("InventatoDiSanaPianta", {}) is None
            # ...e la sessione resta viva
            assert (await cp.call("Heartbeat", {}))["currentTime"]


# --- attuazione --------------------------------------------------------

@pytest.mark.asyncio
async def test_pausa_e_un_limite_di_zero_ampere():
    """Il cuore della correzione: fermare la ricarica senza switch."""
    async with running_csms() as csms:
        async with connected(csms) as cp:
            assert await csms.charge_point.async_set_limit(0)
            periodo = cp.calls_for("SetChargingProfile")[-1]["csChargingProfiles"][
                "chargingSchedule"
            ]["chargingSchedulePeriod"][0]
            assert periodo["limit"] == 0.0
            assert csms.charge_point.desired_limit_a == 0
            assert csms.charge_point.limit_confirmed


@pytest.mark.asyncio
async def test_limite_rifiutato_viene_registrato():
    async with running_csms() as csms:
        async with connected(csms) as cp:
            cp.overrides["SetChargingProfile"] = {"status": "Rejected"}
            assert not await csms.charge_point.async_set_limit(10)
            session = csms.charge_point
            assert not session.limit_confirmed
            assert "Rejected" in session.last_limit_error


@pytest.mark.asyncio
async def test_limite_non_rispettato_viene_smascherato():
    """La wallbox dice Accepted ma continua a offrire 32 A: dobbiamo accorgercene."""
    async with running_csms() as csms:
        async with connected(csms) as cp:
            await csms.charge_point.async_set_limit(10)
            await cp.call(
                "StatusNotification",
                {"connectorId": 1, "errorCode": "NoError", "status": "Charging"},
            )
            await cp.call(
                "MeterValues",
                {
                    "connectorId": 1,
                    "meterValue": [
                        {
                            "sampledValue": [
                                {
                                    "value": "32",
                                    "measurand": "Current.Offered",
                                    "unit": "A",
                                }
                            ]
                        }
                    ],
                },
            )
            session = csms.charge_point
            assert not session.limit_confirmed
            assert "32" in session.last_limit_error


@pytest.mark.asyncio
async def test_il_limite_viene_riapplicato_a_inizio_transazione():
    """Alcune wallbox azzerano i profili quando parte la sessione."""
    async with running_csms() as csms:
        async with connected(csms) as cp:
            await csms.charge_point.async_set_limit(8)
            prima = len(cp.calls_for("SetChargingProfile"))

            await cp.call(
                "StartTransaction",
                {
                    "connectorId": 1,
                    "idTag": "TAG1",
                    "meterStart": 0,
                    "timestamp": "2026-09-08T10:00:00Z",
                },
            )
            await asyncio.sleep(0.2)
            assert len(cp.calls_for("SetChargingProfile")) > prima


@pytest.mark.asyncio
async def test_la_riconnessione_conserva_il_limite():
    """Se la wallbox si riavvia non deve ripartire senza limite."""
    async with running_csms() as csms:
        async with connected(csms) as cp:
            await csms.charge_point.async_set_limit(9)
            await cp.close()
            await asyncio.sleep(0.2)

        async with connected(csms) as cp2:
            # La riapplicazione arriva in coda alla sequenza post-boot, dopo un
            # numero variabile di ChangeConfiguration: va attesa, non assunta.
            profilo = await cp2.wait_for("SetChargingProfile")
            periodo = profilo["csChargingProfiles"]["chargingSchedule"][
                "chargingSchedulePeriod"
            ][0]
            assert periodo["limit"] == 9.0


@pytest.mark.asyncio
async def test_senza_wallbox_il_csms_non_e_connesso():
    async with running_csms() as csms:
        assert not csms.connected
        assert csms.charge_point is None


# --- auto attaccata a una wallbox che non parte -------------------------

async def _status(cp, status: str) -> None:
    await cp.call(
        "StatusNotification",
        {"connectorId": 1, "errorCode": "NoError", "status": status},
    )


@pytest.mark.asyncio
async def test_auto_collegata_chiede_di_riapplicare_il_limite():
    """Molte wallbox perdono il profilo quando l'auto viene inserita: il limite
    mandato prima va riaffermato, altrimenti la ricarica non parte."""
    async with running_csms() as csms:
        async with connected(csms) as cp:
            session = csms.charge_point
            await _status(cp, "Available")
            assert not session.reassert_pending

            await _status(cp, "Preparing")
            assert session.reassert_pending

            # Passare da uno stato "collegata" a un altro non e' un'altra spina.
            assert session.consume_reassert()
            await _status(cp, "Charging")
            assert not session.reassert_pending


@pytest.mark.asyncio
async def test_sospesa_dalla_wallbox_senza_misure_non_e_confermata(monkeypatch):
    """Senza MeterValues non ci sono correnti: lo stato e' l'unico indizio."""
    monkeypatch.setattr(ocpp_server, "STATUS_GRACE_SECONDS", 0.0)
    async with running_csms() as csms:
        async with connected(csms) as cp:
            session = csms.charge_point
            await session.async_set_limit(14)
            assert session.limit_confirmed

            await _status(cp, "SuspendedEVSE")
            assert not session.limit_confirmed
            assert "SuspendedEVSE" in session.last_limit_error


@pytest.mark.asyncio
async def test_il_cambio_di_stato_ha_il_tempo_di_seguire_il_limite(monkeypatch):
    """Subito dopo un nuovo limite la wallbox e' ancora in transizione: non e'
    un'accusa, e non deve far lampeggiare l'avviso nel pannello."""
    monkeypatch.setattr(ocpp_server, "STATUS_GRACE_SECONDS", 60.0)
    async with running_csms() as csms:
        async with connected(csms) as cp:
            session = csms.charge_point
            await session.async_set_limit(14)
            await _status(cp, "SuspendedEVSE")
            assert session.limit_confirmed


@pytest.mark.asyncio
async def test_sospesa_con_limite_zero_e_quello_che_volevamo(monkeypatch):
    monkeypatch.setattr(ocpp_server, "STATUS_GRACE_SECONDS", 0.0)
    async with running_csms() as csms:
        async with connected(csms) as cp:
            session = csms.charge_point
            await session.async_set_limit(0)
            await _status(cp, "SuspendedEVSE")
            assert session.limit_confirmed


@pytest.mark.asyncio
async def test_attuatore_riaffonda_il_limite_quando_si_inserisce_l_auto():
    """Il caso reale, dall'inizio alla fine: limite gia' mandato, poi l'auto
    viene attaccata. Prima di questa correzione non partiva nulla fino a un
    riavvio di Home Assistant, perche' l'attuatore non rimanda un valore uguale."""
    actuator = importlib.import_module("evbalance_pkg.actuator")
    async with running_csms() as csms:
        async with connected(csms) as cp:
            act = actuator.OcppActuator(csms, min_current=6, voltage=230.0, phases=1)

            await act.async_apply(14, paused=False)
            mandati = len(cp.calls_for("SetChargingProfile"))
            await act.async_apply(14, paused=False)
            assert len(cp.calls_for("SetChargingProfile")) == mandati   # nulla di nuovo

            await _status(cp, "Available")
            await _status(cp, "Preparing")        # l'auto viene attaccata
            await act.async_apply(14, paused=False)
            assert len(cp.calls_for("SetChargingProfile")) == mandati + 1

            # Una volta sola per spina, non a ogni ciclo.
            await act.async_apply(14, paused=False)
            assert len(cp.calls_for("SetChargingProfile")) == mandati + 1

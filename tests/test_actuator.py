"""Test degli attuatori (`actuator.py`): come il target calcolato arriva alla wallbox.

E' il punto in cui viveva il bug originale: chiedere una corrente sotto il
minimo della wallbox non ferma la ricarica. Qui si verifica che entrambe le
strade lo gestiscano — l'una accorgendosene, l'altra rendendolo impossibile.
"""

import importlib

import pytest

pytest.importorskip("pytest_asyncio")

actuator = importlib.import_module("evbalance_pkg.actuator")
EntityActuator = actuator.EntityActuator
OcppActuator = actuator.OcppActuator
RETRY_INTERVAL = actuator.RETRY_INTERVAL

from ocpp_messages import MeterSnapshot  # noqa: E402


# --- doppioni di Home Assistant ---------------------------------------

class FakeState:
    def __init__(self, state):
        self.state = state


class FakeServices:
    def __init__(self, hass):
        self.hass = hass
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_with: Exception | None = None

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, dict(data)))
        if self.fail_with is not None:
            raise self.fail_with
        # Simula l'effetto del servizio sullo stato, come farebbe HA.
        entity = data["entity_id"]
        if domain == "number":
            self.hass.set(entity, str(data["value"]))
        else:
            self.hass.set(entity, "on" if service == "turn_on" else "off")


class FakeStates:
    def __init__(self, values):
        self.values = values

    def get(self, entity_id):
        raw = self.values.get(entity_id)
        return None if raw is None else FakeState(raw)


class FakeHass:
    def __init__(self, **values):
        self.values = dict(values)
        self.states = FakeStates(self.values)
        self.services = FakeServices(self)

    def set(self, entity_id, value):
        self.values[entity_id] = value


NUMBER = "number.wallbox_current"
SWITCH = "switch.wallbox_charging"


def make_entity_actuator(hass, switch=None, invert=False):
    return EntityActuator(
        hass, number_entity=NUMBER, switch_entity=switch, switch_invert=invert
    )


# --- EntityActuator ----------------------------------------------------

@pytest.mark.asyncio
async def test_scrive_la_corrente_sul_number():
    hass = FakeHass(**{NUMBER: "16"})
    await make_entity_actuator(hass).async_apply(10, paused=False)
    assert hass.services.calls == [
        ("number", "set_value", {"entity_id": NUMBER, "value": 10})
    ]


@pytest.mark.asyncio
async def test_non_riscrive_un_valore_gia_impostato():
    """Non si inonda la wallbox di comandi a ogni ciclo."""
    hass = FakeHass(**{NUMBER: "10"})
    act = make_entity_actuator(hass)
    await act.async_apply(10, paused=False)
    await act.async_apply(10, paused=False)
    assert hass.services.calls == []


@pytest.mark.asyncio
async def test_smaschera_la_wallbox_che_rifiuta_di_scendere():
    """Il bug originale: chiediamo 0 A, la wallbox resta a 6 e il contatore scatta.

    Senza switch di pausa non possiamo evitarlo, ma dobbiamo almeno accorgercene
    invece di dichiarare "in pausa" mentre sta ancora erogando.
    """
    hass = FakeHass(**{NUMBER: "6"})
    hass.services.fail_with = ValueError("value must be at least 6")
    act = make_entity_actuator(hass)

    await act.async_apply(0, paused=True)
    assert act.last_error is not None

    # Al giro successivo il number vale ancora 6: la discrepanza va segnalata.
    await act.async_apply(0, paused=True)
    assert act.mismatch is not None
    assert "6A" in act.mismatch
    assert act.diagnostics()["actuator_mismatch"] == act.mismatch


@pytest.mark.asyncio
async def test_nessuna_discrepanza_quando_il_valore_attecchisce():
    hass = FakeHass(**{NUMBER: "16"})
    act = make_entity_actuator(hass)
    await act.async_apply(10, paused=False)
    await act.async_apply(10, paused=False)
    assert act.mismatch is None


@pytest.mark.asyncio
async def test_pausa_con_switch_spegne_senza_toccare_la_corrente():
    hass = FakeHass(**{NUMBER: "10", SWITCH: "on"})
    act = make_entity_actuator(hass, switch=SWITCH)
    await act.async_apply(0, paused=True)
    assert hass.services.calls == [
        ("homeassistant", "turn_off", {"entity_id": SWITCH})
    ]
    assert hass.values[NUMBER] == "10"   # pronta per la ripresa


@pytest.mark.asyncio
async def test_ripresa_imposta_prima_la_corrente_poi_accende():
    """Riaccendere prima di limitare farebbe ripartire sopra il budget."""
    hass = FakeHass(**{NUMBER: "16", SWITCH: "off"})
    act = make_entity_actuator(hass, switch=SWITCH)
    await act.async_apply(8, paused=False)
    assert [(s, d["entity_id"]) for _, s, d in hass.services.calls] == [
        ("set_value", NUMBER),
        ("turn_on", SWITCH),
    ]


@pytest.mark.asyncio
async def test_switch_invertito():
    """Su alcune wallbox lo stato ON dello switch significa 'in pausa'."""
    hass = FakeHass(**{NUMBER: "10", SWITCH: "off"})
    act = make_entity_actuator(hass, switch=SWITCH, invert=True)
    await act.async_apply(0, paused=True)
    assert hass.services.calls[-1][1] == "turn_on"


@pytest.mark.asyncio
async def test_switch_gia_nello_stato_voluto_non_viene_ritoccato():
    hass = FakeHass(**{NUMBER: "10", SWITCH: "off"})
    act = make_entity_actuator(hass, switch=SWITCH)
    await act.async_apply(0, paused=True)
    assert hass.services.calls == []


# --- OcppActuator ------------------------------------------------------

class FakeSession:
    def __init__(self, accept=True):
        self.accept = accept
        self.limits: list[float] = []
        self.limit_confirmed = True
        self.last_limit_error = None
        self.snapshot = MeterSnapshot()
        self.status = "Charging"
        self.vehicle_connected = True

    async def async_set_limit(self, amps, phases=None):
        self.limits.append(amps)
        return self.accept

    def telemetry(self):
        return {"status": self.status}


class FakeCsms:
    def __init__(self, session=None):
        self.charge_point = session

    @property
    def connected(self):
        return self.charge_point is not None


def make_ocpp_actuator(session, min_current=6, voltage=230.0, phases=1):
    return OcppActuator(
        FakeCsms(session), min_current=min_current, voltage=voltage, phases=phases
    )


@pytest.mark.asyncio
async def test_la_pausa_diventa_zero_ampere():
    """La correzione di fondo: in OCPP fermarsi non richiede alcuno switch."""
    session = FakeSession()
    await make_ocpp_actuator(session).async_apply(0, paused=True)
    assert session.limits == [0.0]


@pytest.mark.asyncio
async def test_un_target_sotto_il_minimo_diventa_pausa():
    """Mai chiedere 4 A: la wallbox risalirebbe a 6 e il contatore scatterebbe."""
    session = FakeSession()
    await make_ocpp_actuator(session).async_apply(4, paused=False)
    assert session.limits == [0.0]


@pytest.mark.asyncio
async def test_target_valido_passa_invariato():
    session = FakeSession()
    await make_ocpp_actuator(session).async_apply(13, paused=False)
    assert session.limits == [13.0]


@pytest.mark.asyncio
async def test_non_rimanda_lo_stesso_limite():
    session = FakeSession()
    act = make_ocpp_actuator(session)
    await act.async_apply(10, paused=False)
    await act.async_apply(10, paused=False)
    await act.async_apply(10, paused=False)
    assert session.limits == [10.0]


@pytest.mark.asyncio
async def test_insiste_se_la_wallbox_non_rispetta_il_limite():
    """Il caso noto: la wallbox risponde Accepted ma continua a fondo scala."""
    session = FakeSession()
    act = make_ocpp_actuator(session)
    await act.async_apply(10, paused=False)

    session.limit_confirmed = False
    session.last_limit_error = "chiesti 10.0A ma la wallbox ne offre 32.0"
    act._last_attempt = 0.0   # come se fosse passato l'intervallo di ritentativo
    await act.async_apply(10, paused=False)
    assert session.limits == [10.0, 10.0]


@pytest.mark.asyncio
async def test_non_insiste_prima_dell_intervallo():
    session = FakeSession()
    act = make_ocpp_actuator(session)
    await act.async_apply(10, paused=False)
    session.limit_confirmed = False
    await act.async_apply(10, paused=False)
    assert session.limits == [10.0]


@pytest.mark.asyncio
async def test_un_rifiuto_fa_ritentare_al_giro_dopo():
    session = FakeSession(accept=False)
    act = make_ocpp_actuator(session)
    await act.async_apply(10, paused=False)
    await act.async_apply(10, paused=False)
    assert session.limits == [10.0, 10.0]


@pytest.mark.asyncio
async def test_senza_wallbox_non_si_comanda_nulla():
    act = make_ocpp_actuator(None)
    await act.async_apply(10, paused=False)   # non deve sollevare
    assert not act.available
    assert act.diagnostics() == {
        "control_mode": "ocpp",
        "ocpp_connected": False,
        "ocpp": {},
    }


def test_potenza_letta_dalla_wallbox():
    session = FakeSession()
    session.snapshot = MeterSnapshot(power_w=3400.0)
    assert make_ocpp_actuator(session).read_power_w() == 3400.0


def test_potenza_zero_se_l_auto_non_e_collegata():
    session = FakeSession()
    session.vehicle_connected = False
    session.snapshot = MeterSnapshot(power_w=1234.0)
    assert make_ocpp_actuator(session).read_power_w() == 0.0


def test_potenza_ignota_senza_wallbox():
    """None significa 'usa il sensore configurato', non 'zero watt'."""
    assert make_ocpp_actuator(None).read_power_w() is None

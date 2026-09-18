"""Unit test dei payload OCPP 1.6J (`ocpp_messages.py`).

Coprono la regola che risolve il bug del minimo di 6 A (sotto il minimo si
chiede 0 A, non un valore intermedio), la costruzione dei charging profile, la
lettura dei MeterValues e la verifica che il limite sia stato davvero applicato.
"""

import pytest

from ocpp_messages import (
    MeterSnapshot,
    build_charging_profile,
    build_clear_profile,
    check_limit_applied,
    effective_power_w,
    next_sampled_data,
    parse_configuration,
    parse_meter_values,
    sanitize_limit,
    snapshot_without_flow,
    supports_phase_switching,
    supports_smart_charging,
    SAMPLED_DATA_CANDIDATES,
)


# --- sanitize_limit ----------------------------------------------------

@pytest.mark.parametrize(
    ("richiesto", "atteso"),
    [
        (16, 16.0),     # sopra il minimo: invariato
        (6, 6.0),       # esattamente il minimo: ammesso
        (5, 0.0),       # sotto il minimo: pausa, non 5 A
        (1, 0.0),
        (0, 0.0),
        (-3, 0.0),      # valori assurdi non devono passare
    ],
)
def test_sanitize_limit(richiesto, atteso):
    assert sanitize_limit(richiesto, 6) == atteso


def test_sanitize_limit_rispetta_un_minimo_diverso():
    """Con minimo 10 A, 8 A non e' un limite valido: si mette in pausa."""
    assert sanitize_limit(8, 10) == 0.0
    assert sanitize_limit(10, 10) == 10.0


# --- build_charging_profile --------------------------------------------

def test_profilo_di_base():
    payload = build_charging_profile(10, connector=1)
    assert payload["connectorId"] == 1
    profile = payload["csChargingProfiles"]
    assert profile["chargingProfilePurpose"] == "TxDefaultProfile"
    schedule = profile["chargingSchedule"]
    assert schedule["chargingRateUnit"] == "A"
    assert schedule["chargingSchedulePeriod"] == [{"startPeriod": 0, "limit": 10.0}]


def test_profilo_di_pausa():
    """La pausa e' un profilo come gli altri, con limite 0."""
    schedule = build_charging_profile(0)["csChargingProfiles"]["chargingSchedule"]
    assert schedule["chargingSchedulePeriod"][0]["limit"] == 0.0


def test_profilo_con_numero_di_fasi():
    period = build_charging_profile(16, phases=1)["csChargingProfiles"][
        "chargingSchedule"
    ]["chargingSchedulePeriod"][0]
    assert period["numberPhases"] == 1


def test_profilo_senza_fasi_non_forza_il_campo():
    period = build_charging_profile(16)["csChargingProfiles"]["chargingSchedule"][
        "chargingSchedulePeriod"
    ][0]
    assert "numberPhases" not in period


def test_txprofile_porta_la_transazione():
    profile = build_charging_profile(
        12, purpose="TxProfile", transaction_id=7
    )["csChargingProfiles"]
    assert profile["transactionId"] == 7


def test_txdefaultprofile_non_porta_la_transazione():
    """Un TxDefaultProfile con transactionId verrebbe rifiutato da alcune wallbox."""
    profile = build_charging_profile(12, transaction_id=7)["csChargingProfiles"]
    assert "transactionId" not in profile


def test_clear_profile_vuoto_cancella_tutto():
    assert build_clear_profile() == {}
    assert build_clear_profile(profile_id=1, connector=1) == {
        "id": 1,
        "connectorId": 1,
    }


# --- parse_meter_values ------------------------------------------------

def _meter(*samples):
    return {"connectorId": 1, "meterValue": [{"sampledValue": list(samples)}]}


def test_lettura_potenza_e_correnti():
    snap = parse_meter_values(
        _meter(
            {"value": "3450", "measurand": "Power.Active.Import", "unit": "W"},
            {"value": "15.1", "measurand": "Current.Import", "unit": "A", "phase": "L1"},
            {"value": "230.2", "measurand": "Voltage", "unit": "V", "phase": "L1"},
        )
    )
    assert snap.power_w == 3450.0
    assert snap.currents == {"L1": 15.1}
    assert snap.voltages == {"L1": 230.2}


def test_potenza_in_kw_viene_riportata_in_watt():
    snap = parse_meter_values(
        _meter({"value": "3.45", "measurand": "Power.Active.Import", "unit": "kW"})
    )
    assert snap.power_w == 3450.0


def test_energia_e_il_measurand_predefinito():
    """Senza measurand l'OCPP 1.6 intende il registro di energia."""
    snap = parse_meter_values(_meter({"value": "12.5", "unit": "kWh"}))
    assert snap.energy_wh == 12500.0


def test_fase_con_suffisso_viene_normalizzata():
    """'L1-N' e 'L1' sono la stessa fase."""
    snap = parse_meter_values(
        _meter(
            {"value": "10", "measurand": "Current.Import", "phase": "L1-N"},
            {"value": "230", "measurand": "Voltage", "phase": "L1-N"},
        )
    )
    assert snap.currents == {"L1": 10.0}
    assert snap.voltages == {"L1": 230.0}


def test_corrente_senza_fase_finisce_su_l1():
    snap = parse_meter_values(_meter({"value": "9", "measurand": "Current.Import"}))
    assert snap.currents == {"L1": 9.0}


def test_soc_e_corrente_offerta():
    snap = parse_meter_values(
        _meter(
            {"value": "62", "measurand": "SoC", "unit": "Percent"},
            {"value": "8", "measurand": "Current.Offered", "unit": "A"},
        )
    )
    assert snap.soc == 62.0
    assert snap.current_offered_a == 8.0


def test_valori_non_numerici_vengono_ignorati():
    snap = parse_meter_values(
        _meter({"value": "n/a", "measurand": "Power.Active.Import", "unit": "W"})
    )
    assert snap.power_w is None


def test_merge_conserva_i_valori_non_ripetuti():
    """Un campione parziale non deve cancellare quel che sappiamo gia'."""
    vecchio = MeterSnapshot(power_w=3000.0, currents={"L1": 13.0}, soc=40.0)
    nuovo = parse_meter_values(
        _meter({"value": "14", "measurand": "Current.Import", "phase": "L1"})
    )
    unito = vecchio.merged_with(nuovo)
    assert unito.currents == {"L1": 14.0}
    assert unito.power_w == 3000.0
    assert unito.soc == 40.0


def test_somma_e_massimo_delle_correnti():
    snap = MeterSnapshot(currents={"L1": 10.0, "L2": 12.0, "L3": 11.0})
    assert snap.total_current_a == 33.0
    assert snap.max_phase_current_a == 12.0


def test_correnti_assenti_non_producono_zero_fasullo():
    assert MeterSnapshot().total_current_a is None
    assert MeterSnapshot().max_phase_current_a is None


# --- effective_power_w -------------------------------------------------

def test_potenza_misurata_ha_la_precedenza():
    snap = MeterSnapshot(power_w=1234.0, currents={"L1": 100.0})
    assert effective_power_w(snap, 230.0, 1) == 1234.0


def test_potenza_calcolata_dalle_correnti_misurate():
    snap = MeterSnapshot(currents={"L1": 10.0}, voltages={"L1": 235.0})
    assert effective_power_w(snap, 230.0, 1) == pytest.approx(2350.0)


def test_potenza_calcolata_con_la_tensione_di_configurazione():
    snap = MeterSnapshot(currents={"L1": 10.0})
    assert effective_power_w(snap, 230.0, 1) == pytest.approx(2300.0)


def test_trifase_usa_la_tensione_di_fase():
    """Con 400 V di linea, ogni fase lavora a 400/sqrt(3) ~ 231 V."""
    snap = MeterSnapshot(currents={"L1": 10.0, "L2": 10.0, "L3": 10.0})
    atteso = 3 * 10.0 * (400.0 / 3 ** 0.5)
    assert effective_power_w(snap, 400.0, 3) == pytest.approx(atteso, rel=1e-6)


def test_senza_misure_la_potenza_e_ignota():
    assert effective_power_w(MeterSnapshot(), 230.0, 1) is None


def test_snapshot_senza_flusso_azzera_solo_l_erogazione():
    snap = MeterSnapshot(power_w=3000.0, currents={"L1": 13.0}, energy_wh=5000.0)
    fermo = snapshot_without_flow(snap)
    assert fermo.power_w == 0.0
    assert fermo.currents == {}
    assert fermo.energy_wh == 5000.0   # il contatore non torna indietro


# --- check_limit_applied ----------------------------------------------

def test_limite_confermato_dalla_corrente_offerta():
    snap = MeterSnapshot(current_offered_a=10.0)
    assert check_limit_applied(snap, 10.0).matches


def test_limite_ignorato_dalla_wallbox():
    """Il caso noto: si riprende da 0 A e la wallbox torna a fondo scala."""
    snap = MeterSnapshot(current_offered_a=32.0)
    esito = check_limit_applied(snap, 10.0)
    assert not esito.matches
    assert "32" in esito.detail


def test_pausa_non_rispettata_viene_rilevata():
    """La regressione da cui e' nato tutto: pausa chiesta, wallbox a 6 A."""
    snap = MeterSnapshot(currents={"L1": 6.0})
    esito = check_limit_applied(snap, 0.0)
    assert not esito.matches
    assert "6.0A" in esito.detail


def test_pausa_confermata_senza_corrente():
    snap = MeterSnapshot(currents={"L1": 0.1})
    assert check_limit_applied(snap, 0.0).matches


def test_corrente_entro_la_tolleranza_va_bene():
    snap = MeterSnapshot(currents={"L1": 10.4})
    assert check_limit_applied(snap, 10.0).matches


def test_corrente_oltre_la_tolleranza_no():
    snap = MeterSnapshot(currents={"L1": 12.0})
    assert not check_limit_applied(snap, 10.0).matches


def test_senza_misure_non_si_accusa_la_wallbox():
    assert check_limit_applied(MeterSnapshot(), 10.0).matches


def test_ripresa_da_pausa_ignorata_viene_rilevata():
    """Cambio fascia (es. F2 -> F3): si chiede corrente > 0 ma la wallbox
    resta sospesa "di suo" invece di ripartire col nuovo limite."""
    snap = MeterSnapshot(currents={"L1": 0.0})
    esito = check_limit_applied(snap, 10.0, status="SuspendedEVSE")
    assert not esito.matches
    assert "sospesa" in esito.detail


def test_auto_che_non_assorbe_non_e_un_mismatch():
    """SuspendedEV e' una scelta dell'auto (es. batteria piena): non e' un bug."""
    snap = MeterSnapshot(currents={"L1": 0.0})
    assert check_limit_applied(snap, 10.0, status="SuspendedEV").matches


# --- configurazione ----------------------------------------------------

def test_parse_configuration():
    conf = parse_configuration(
        {
            "configurationKey": [
                {"key": "SupportedFeatureProfiles", "value": "Core,SmartCharging"},
                {"key": "MeterValueSampleInterval", "value": "60", "readonly": False},
            ],
            "unknownKey": ["ConnectorSwitch3to1PhaseSupported"],
        }
    )
    assert conf["SupportedFeatureProfiles"] == "Core,SmartCharging"
    assert conf["MeterValueSampleInterval"] == "60"
    assert conf["ConnectorSwitch3to1PhaseSupported"] == ""


def test_smart_charging_dichiarato():
    assert supports_smart_charging({"SupportedFeatureProfiles": "Core,SmartCharging"})
    assert not supports_smart_charging({"SupportedFeatureProfiles": "Core,LocalAuthList"})


def test_smart_charging_ignoto_si_assume_disponibile():
    """Diverse wallbox lo supportano senza dichiararlo: decide il comando."""
    assert supports_smart_charging({})
    assert supports_smart_charging({"SupportedFeatureProfiles": ""})


def test_commutazione_fasi():
    assert supports_phase_switching({"ConnectorSwitch3to1PhaseSupported": "true"})
    assert not supports_phase_switching({"ConnectorSwitch3to1PhaseSupported": "false"})
    assert not supports_phase_switching({})


def test_ripiego_progressivo_dei_measurand():
    primo = next_sampled_data(None)
    assert primo == SAMPLED_DATA_CANDIDATES[0]
    secondo = next_sampled_data(primo)
    assert secondo == SAMPLED_DATA_CANDIDATES[1]
    assert next_sampled_data(SAMPLED_DATA_CANDIDATES[-1]) is None


def test_ripiego_da_valore_sconosciuto_riparte_dal_primo():
    assert next_sampled_data("qualcosa di inatteso") == SAMPLED_DATA_CANDIDATES[0]


def test_remote_trigger():
    from ocpp_messages import supports_remote_trigger

    assert supports_remote_trigger({"SupportedFeatureProfiles": "Core,RemoteTrigger"})
    assert not supports_remote_trigger({"SupportedFeatureProfiles": "Core"})
    assert supports_remote_trigger({})   # ignoto: si prova comunque

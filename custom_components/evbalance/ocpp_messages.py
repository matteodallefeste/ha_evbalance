# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Costruzione e lettura dei payload OCPP 1.6J (logica pura).

Tenuta separata dal server websocket per gli stessi motivi di ``balancer.py``:
qui non si tocca ne' Home Assistant ne' la rete, quindi e' tutto testabile
direttamente.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# --- Tipi di messaggio OCPP-J ---
CALL = 2
CALLRESULT = 3
CALLERROR = 4

# --- Stati del connettore (StatusNotification) ---
STATUS_AVAILABLE = "Available"
STATUS_PREPARING = "Preparing"
STATUS_CHARGING = "Charging"
STATUS_SUSPENDED_EV = "SuspendedEV"
STATUS_SUSPENDED_EVSE = "SuspendedEVSE"
STATUS_FINISHING = "Finishing"
STATUS_FAULTED = "Faulted"
STATUS_UNAVAILABLE = "Unavailable"

# Stati in cui c'e' un'auto collegata (anche se non sta assorbendo).
CONNECTED_STATES = frozenset(
    {
        STATUS_PREPARING,
        STATUS_CHARGING,
        STATUS_SUSPENDED_EV,
        STATUS_SUSPENDED_EVSE,
        STATUS_FINISHING,
    }
)

# Measurand che ci interessano nei MeterValues. Quando il campo manca, l'OCPP
# 1.6 dice di assumere Energy.Active.Import.Register.
DEFAULT_MEASURAND = "Energy.Active.Import.Register"

# Ordine di preferenza: se la wallbox rifiuta l'elenco completo si ripiega sui
# successivi, sempre piu' corti. Alcuni firmware rifiutano in blocco l'intera
# stringa se anche un solo measurand non e' supportato.
SAMPLED_DATA_CANDIDATES = (
    "Power.Active.Import,Current.Import,Current.Offered,Voltage,"
    "Energy.Active.Import.Register,SoC",
    "Power.Active.Import,Current.Import,Current.Offered,"
    "Energy.Active.Import.Register",
    "Power.Active.Import,Current.Import,Energy.Active.Import.Register",
    "Current.Import,Energy.Active.Import.Register",
    "Energy.Active.Import.Register",
)


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _scale_power(value: float, unit: str) -> float:
    """Riporta una potenza in W."""
    return value * 1000.0 if unit.lower() in ("kw", "kva", "kvar") else value


def _scale_energy(value: float, unit: str) -> float:
    """Riporta un'energia in Wh."""
    return value * 1000.0 if unit.lower() in ("kwh", "kvarh", "kvah") else value


# --------------------------------------------------------------------------
# Profili di ricarica (SetChargingProfile)
# --------------------------------------------------------------------------

def sanitize_limit(amps: float, min_current: float) -> float:
    """Porta a 0 qualunque richiesta sotto il minimo caricabile.

    E' il cuore della correzione: in OCPP l'unico modo sensato di stare sotto il
    minimo della wallbox e' chiedere **0 A**, che sospende l'erogazione tenendo
    aperta la sessione (stato SuspendedEVSE). Un valore intermedio (1-5 A) non
    e' rappresentabile sul pilot e verrebbe risalito a 6 A dalla wallbox, che
    e' esattamente il comportamento che fa scattare il contatore.
    """
    value = max(0.0, float(amps))
    if value < float(min_current):
        return 0.0
    return value


def build_charging_profile(
    limit_a: float,
    *,
    connector: int = 1,
    phases: int | None = None,
    profile_id: int = 1,
    stack_level: int = 0,
    purpose: str = "TxDefaultProfile",
    kind: str = "Relative",
    transaction_id: int | None = None,
) -> dict:
    """Payload di SetChargingProfile per un limite costante.

    Un solo periodo che parte a 0 s e resta valido: e' il modo piu' compatibile
    di esprimere "da adesso, non superare questa corrente".
    """
    period: dict = {"startPeriod": 0, "limit": round(float(limit_a), 1)}
    if phases:
        period["numberPhases"] = int(phases)

    profile: dict = {
        "chargingProfileId": profile_id,
        "stackLevel": stack_level,
        "chargingProfilePurpose": purpose,
        "chargingProfileKind": kind,
        "chargingSchedule": {
            "chargingRateUnit": "A",
            "chargingSchedulePeriod": [period],
        },
    }
    # Un TxProfile e' legato a una transazione precisa e senza id viene rifiutato.
    if purpose == "TxProfile" and transaction_id is not None:
        profile["transactionId"] = transaction_id

    return {"connectorId": connector, "csChargingProfiles": profile}


def build_clear_profile(profile_id: int | None = None, connector: int | None = None) -> dict:
    """Payload di ClearChargingProfile (senza campi: cancella tutto)."""
    payload: dict = {}
    if profile_id is not None:
        payload["id"] = profile_id
    if connector is not None:
        payload["connectorId"] = connector
    return payload


# --------------------------------------------------------------------------
# Lettura dei MeterValues
# --------------------------------------------------------------------------

@dataclass
class MeterSnapshot:
    """Ultima fotografia elettrica nota della wallbox.

    Ogni campo e' opzionale: le wallbox mandano sottoinsiemi diversi di
    measurand, e un campione non ripete necessariamente tutto ogni volta.
    """

    power_w: float | None = None
    currents: dict[str, float] = field(default_factory=dict)   # per fase (L1/L2/L3)
    voltages: dict[str, float] = field(default_factory=dict)
    current_offered_a: float | None = None
    energy_wh: float | None = None
    soc: float | None = None
    temperature_c: float | None = None

    def merged_with(self, other: "MeterSnapshot") -> "MeterSnapshot":
        """Nuovo snapshot con i valori di `other` sovrapposti ai propri."""
        return MeterSnapshot(
            power_w=other.power_w if other.power_w is not None else self.power_w,
            currents={**self.currents, **other.currents},
            voltages={**self.voltages, **other.voltages},
            current_offered_a=(
                other.current_offered_a
                if other.current_offered_a is not None
                else self.current_offered_a
            ),
            energy_wh=other.energy_wh if other.energy_wh is not None else self.energy_wh,
            soc=other.soc if other.soc is not None else self.soc,
            temperature_c=(
                other.temperature_c
                if other.temperature_c is not None
                else self.temperature_c
            ),
        )

    @property
    def total_current_a(self) -> float | None:
        """Somma delle correnti di fase, se ne conosciamo almeno una."""
        if not self.currents:
            return None
        return sum(self.currents.values())

    @property
    def max_phase_current_a(self) -> float | None:
        """Corrente della fase piu' carica: e' quella che il limite governa."""
        if not self.currents:
            return None
        return max(self.currents.values())


def parse_meter_values(payload: dict) -> MeterSnapshot:
    """Estrae da un MeterValues i valori che ci servono."""
    snapshot = MeterSnapshot()
    for entry in payload.get("meterValue") or []:
        for sample in entry.get("sampledValue") or []:
            measurand = sample.get("measurand") or DEFAULT_MEASURAND
            value = _to_float(sample.get("value"))
            if value is None:
                continue
            unit = str(sample.get("unit") or "")
            phase = sample.get("phase")

            if measurand == "Power.Active.Import":
                snapshot.power_w = _scale_power(value, unit)
            elif measurand == "Current.Import":
                # Senza indicazione di fase assumiamo la L1 (monofase).
                snapshot.currents[_phase_key(phase)] = value
            elif measurand == "Current.Offered":
                snapshot.current_offered_a = value
            elif measurand == "Voltage":
                snapshot.voltages[_phase_key(phase)] = value
            elif measurand == DEFAULT_MEASURAND:
                snapshot.energy_wh = _scale_energy(value, unit or "Wh")
            elif measurand == "SoC":
                snapshot.soc = value
            elif measurand == "Temperature":
                snapshot.temperature_c = value
    return snapshot


def _phase_key(phase: object) -> str:
    """Normalizza il campo phase: 'L1-N' e 'L1' finiscono nello stesso posto."""
    if not phase:
        return "L1"
    text = str(phase).upper()
    for name in ("L1", "L2", "L3"):
        if text.startswith(name):
            return name
    return text


def effective_power_w(
    snapshot: MeterSnapshot, fallback_voltage: float, phases: int
) -> float | None:
    """Potenza della wallbox in W, calcolata se non e' misurata direttamente.

    Molte wallbox non mandano Power.Active.Import ma solo le correnti: in quel
    caso ricostruiamo la potenza fase per fase, usando le tensioni misurate
    quando ci sono e quella configurata altrimenti.
    """
    if snapshot.power_w is not None:
        return snapshot.power_w
    if not snapshot.currents:
        return None

    total = 0.0
    for name, current in snapshot.currents.items():
        voltage = snapshot.voltages.get(name)
        if voltage is None:
            # Su impianto trifase la tensione di linea configurata (400 V) non e'
            # quella fase-neutro: per il calcolo per fase serve V / sqrt(3).
            voltage = fallback_voltage / 1.732050808 if phases >= 3 else fallback_voltage
        total += current * voltage
    return total


# --------------------------------------------------------------------------
# Configurazione della wallbox (GetConfiguration / ChangeConfiguration)
# --------------------------------------------------------------------------

# Chiavi che decidono cosa possiamo davvero fare con questa wallbox.
CAPABILITY_KEYS = (
    "SupportedFeatureProfiles",
    "NumberOfConnectors",
    "ChargingScheduleAllowedChargingRateUnit",
    "ChargeProfileMaxStackLevel",
    "MaxChargingProfilesInstalled",
    "ChargingScheduleMaxPeriods",
    "MeterValueSampleInterval",
    "MeterValuesSampledData",
    "ConnectorSwitch3to1PhaseSupported",
)


def parse_configuration(payload: dict) -> dict[str, str]:
    """GetConfiguration.conf -> {chiave: valore} (le sconosciute valgono '')."""
    out: dict[str, str] = {}
    for item in payload.get("configurationKey") or []:
        key = item.get("key")
        if key:
            out[str(key)] = "" if item.get("value") is None else str(item["value"])
    for key in payload.get("unknownKey") or []:
        out.setdefault(str(key), "")
    return out


def supports_smart_charging(config: dict[str, str]) -> bool:
    """True se la wallbox dichiara il profilo SmartCharging.

    In assenza della chiave assumiamo di si': diverse wallbox supportano
    SetChargingProfile senza dichiararlo, e il vero test e' l'esito del comando.
    """
    raw = config.get("SupportedFeatureProfiles")
    if raw is None or raw == "":
        return True
    return "smartcharging" in raw.replace(" ", "").lower()


def supports_phase_switching(config: dict[str, str]) -> bool:
    """True se la wallbox sa passare da tre fasi a una."""
    return str(config.get("ConnectorSwitch3to1PhaseSupported", "")).lower() == "true"


def supports_remote_trigger(config: dict[str, str]) -> bool:
    """True se possiamo chiedere alla wallbox di mandare subito un messaggio.

    Come per lo smart charging, in assenza della chiave proviamo lo stesso: al
    massimo il comando viene rifiutato, senza conseguenze.
    """
    raw = config.get("SupportedFeatureProfiles")
    if raw is None or raw == "":
        return True
    return "remotetrigger" in raw.replace(" ", "").lower()


def next_sampled_data(current: str | None) -> str | None:
    """Elenco di measurand successivo da provare dopo un rifiuto."""
    if current is None:
        return SAMPLED_DATA_CANDIDATES[0]
    try:
        index = SAMPLED_DATA_CANDIDATES.index(current)
    except ValueError:
        return SAMPLED_DATA_CANDIDATES[0]
    if index + 1 >= len(SAMPLED_DATA_CANDIDATES):
        return None
    return SAMPLED_DATA_CANDIDATES[index + 1]


# --------------------------------------------------------------------------
# Verifica dell'attuazione
# --------------------------------------------------------------------------

@dataclass
class LimitCheck:
    """Esito del confronto tra limite chiesto e comportamento osservato."""

    matches: bool
    detail: str


def check_limit_applied(
    snapshot: MeterSnapshot,
    limit_a: float,
    *,
    tolerance_a: float = 1.0,
    idle_current_a: float = 0.5,
) -> LimitCheck:
    """Il limite che abbiamo chiesto e' stato davvero applicato?

    E' il controllo che oggi manca del tutto: scrivere un valore e non
    verificarlo e' il motivo per cui il contatore poteva scattare mentre il
    pannello mostrava "in pausa". Preferiamo Current.Offered (cosa la wallbox
    dichiara di offrire) e ripieghiamo sulla corrente misurata.
    """
    if snapshot.current_offered_a is not None:
        offered = snapshot.current_offered_a
        if abs(offered - limit_a) <= tolerance_a:
            return LimitCheck(True, f"offerti {offered:.1f}A come richiesto")
        return LimitCheck(
            False, f"chiesti {limit_a:.1f}A ma la wallbox ne offre {offered:.1f}"
        )

    measured = snapshot.max_phase_current_a
    if measured is None:
        return LimitCheck(True, "nessuna misura disponibile: nessun sospetto")

    if limit_a <= 0:
        if measured > idle_current_a:
            return LimitCheck(
                False, f"pausa chiesta ma la wallbox eroga ancora {measured:.1f}A"
            )
        return LimitCheck(True, "pausa confermata dalla corrente misurata")

    if measured > limit_a + tolerance_a:
        return LimitCheck(
            False, f"chiesti {limit_a:.1f}A ma ne misuriamo {measured:.1f}"
        )
    return LimitCheck(True, f"misurati {measured:.1f}A entro il limite")


def snapshot_without_flow(snapshot: MeterSnapshot) -> MeterSnapshot:
    """Copia con potenza e correnti azzerate (usata a fine transazione)."""
    return replace(snapshot, power_w=0.0, currents={}, current_offered_a=None)

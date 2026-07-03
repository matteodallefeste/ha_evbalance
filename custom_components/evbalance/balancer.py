"""Logica pura di bilanciamento energetico (indipendente da Home Assistant).

Tenuta separata da coordinator/entity per essere facilmente testabile.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

SQRT3 = math.sqrt(3)


def watts_per_amp(voltage: float, phases: int) -> float:
    """Watt corrispondenti a 1 A impostato sulla EV Charger.

    Monofase:  P = V * I            -> W/A = V
    Trifase:   P = sqrt(3) * V * I  -> W/A = sqrt(3) * V  (V = tensione di linea, 400)
    """
    if phases >= 3:
        return voltage * SQRT3
    return voltage


@dataclass
class BalancerConfig:
    """Parametri di bilanciamento."""

    max_power_w: float
    safety_margin_w: float
    watts_per_amp: float
    min_current: int
    max_current: int
    pause_current: int = 0
    hold_seconds: float = 300.0
    current_steps: list[int] = field(default_factory=list)  # vuoto = ogni intero min..max


@dataclass
class BalancerInputs:
    """Letture istantanee."""

    sources_w: float          # somma consumi NON-EV Charger
    ev_charger_w: float          # potenza attuale EV Charger


@dataclass
class BalancerState:
    """Stato mutabile mantenuto tra un ciclo e l'altro."""

    applied_current: int | None = None   # ultimo valore scritto sulla EV Charger
    last_change_ts: float = 0.0          # monotonic() dell'ultima variazione
    charging_blocked: bool = False       # True quando in pausa forzata
    reasons: list[str] = field(default_factory=list)


def allowed_steps(cfg: BalancerConfig) -> list[int]:
    """Valori di corrente ammessi, ordinati e limitati a [min_current, max_current].

    Se `current_steps` e' vuoto, i passi ammessi sono tutti gli interi da
    `min_current` a `max_current` (comportamento storico, continuo 1A).
    """
    steps = [s for s in cfg.current_steps if cfg.min_current <= s <= cfg.max_current]
    if not steps:
        steps = list(range(cfg.min_current, cfg.max_current + 1))
    return sorted(set(steps))


def quantize_current(cfg: BalancerConfig, raw: int) -> int:
    """Arrotonda `raw` per difetto al valore ammesso piu' vicino (<= raw).

    Ritorna 0 se nessun passo ammesso e' <= raw (sotto il minimo caricabile).
    Arrotondiamo sempre verso il basso per non sforare mai il budget.
    """
    candidates = [s for s in allowed_steps(cfg) if s <= raw]
    return max(candidates) if candidates else 0


def desired_current(cfg: BalancerConfig, inp: BalancerInputs) -> tuple[int, list[str]]:
    """Corrente "ideale" richiesta ora, senza isteresi.

    Ritorna (ampere, motivazioni). `ampere == pause_current` significa pausa.
    """
    reasons: list[str] = []
    budget_w = cfg.max_power_w - cfg.safety_margin_w - inp.sources_w
    reasons.append(
        f"budget={budget_w:.0f}W "
        f"(max {cfg.max_power_w:.0f} - margine {cfg.safety_margin_w:.0f} "
        f"- sorgenti {inp.sources_w:.0f})"
    )

    if budget_w <= 0:
        reasons.append("sorgenti oltre il limite: pausa EV Charger")
        return cfg.pause_current, reasons

    raw = math.floor(budget_w / cfg.watts_per_amp)
    raw = max(0, min(raw, cfg.max_current))

    stepped = quantize_current(cfg, raw)
    if stepped < cfg.min_current:
        reasons.append(
            f"corrente disponibile {raw}A < passo minimo {cfg.min_current}A: pausa"
        )
        return cfg.pause_current, reasons

    if stepped != raw:
        reasons.append(f"corrente ammessa {stepped}A (step, da {raw}A)")
    else:
        reasons.append(f"corrente ammessa {stepped}A")
    return stepped, reasons


def next_current(
    cfg: BalancerConfig,
    inp: BalancerInputs,
    state: BalancerState,
    now: float,
) -> int:
    """Applica l'isteresi e restituisce il valore da scrivere sulla EV Charger.

    Regole:
    - Riduzione (o pausa): immediata, per sicurezza. Riparte il timer di hold.
    - Aumento: consentito solo se sono passati `hold_seconds` dall'ultima
      variazione, cosi' da non modificare il valore di continuo.
    `state` viene aggiornato in place; il chiamante scrive solo se cambia.
    """
    target, reasons = desired_current(cfg, inp)
    state.reasons = reasons

    current = state.applied_current
    if current is None:
        # Primo ciclo: applica subito il valore calcolato.
        state.applied_current = target
        state.last_change_ts = now
        state.charging_blocked = target <= cfg.pause_current
        return target

    if target < current:
        # Sicurezza: si scende sempre subito.
        state.applied_current = target
        state.last_change_ts = now
        state.charging_blocked = target <= cfg.pause_current
        return target

    if target > current:
        elapsed = now - state.last_change_ts
        if elapsed >= cfg.hold_seconds:
            state.applied_current = target
            state.last_change_ts = now
            state.charging_blocked = target <= cfg.pause_current
            return target
        # Hold attivo: mantieni il valore corrente.
        state.reasons.append(
            f"hold attivo: rialzo tra {cfg.hold_seconds - elapsed:.0f}s"
        )
        return current

    # Nessuna variazione.
    return current

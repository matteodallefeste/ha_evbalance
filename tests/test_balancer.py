"""Unit test della logica pura di bilanciamento (`balancer.py`).

Coprono conversione W/A, quantizzazione ai passi ammessi (`current_steps`),
il calcolo della corrente desiderata e l'isteresi salita/discesa.
"""

import math

import pytest

from balancer import (
    SQRT3,
    BalancerConfig,
    BalancerInputs,
    BalancerState,
    allowed_steps,
    desired_current,
    next_current,
    quantize_current,
    watts_per_amp,
)


def make_cfg(**overrides) -> BalancerConfig:
    """Config di base (monofase 230V), sovrascrivibile per singolo test."""
    base = dict(
        max_power_w=10_000.0,
        safety_margin_w=200.0,
        watts_per_amp=230.0,
        min_current=6,
        max_current=16,
        pause_current=0,
        hold_seconds=300.0,
    )
    base.update(overrides)
    return BalancerConfig(**base)


def cfg_for_amps(amps: float, **overrides) -> tuple[BalancerConfig, BalancerInputs]:
    """Config + input tali che il budget valga esattamente `amps` ampere."""
    cfg = make_cfg(**overrides)
    # budget_w = max - margine - sources  =>  sources per ottenere `amps`
    sources = cfg.max_power_w - cfg.safety_margin_w - amps * cfg.watts_per_amp
    return cfg, BalancerInputs(sources_w=sources, ev_charger_w=0.0)


# --- watts_per_amp -------------------------------------------------------

def test_watts_per_amp_monofase():
    assert watts_per_amp(230, 1) == 230


def test_watts_per_amp_trifase():
    assert watts_per_amp(400, 3) == pytest.approx(400 * SQRT3)


# --- allowed_steps -------------------------------------------------------

def test_allowed_steps_default_e_ogni_intero():
    cfg = make_cfg(current_steps=[])
    assert allowed_steps(cfg) == list(range(6, 17))


def test_allowed_steps_filtra_fuori_range_ordina_e_deduplica():
    cfg = make_cfg(min_current=6, max_current=16, current_steps=[16, 8, 8, 6, 3, 20, 10])
    # 3 (<min) e 20 (>max) scartati, duplicati rimossi, ordinato
    assert allowed_steps(cfg) == [6, 8, 10, 16]


def test_allowed_steps_vuoto_dopo_filtro_torna_al_default():
    # tutti i passi fuori dai limiti -> fallback al continuo min..max
    cfg = make_cfg(min_current=6, max_current=16, current_steps=[1, 2, 30])
    assert allowed_steps(cfg) == list(range(6, 17))


# --- quantize_current ----------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        (5, 0),    # sotto il primo passo -> nessun candidato
        (6, 6),    # esatto
        (7, 6),    # arrotonda per difetto
        (9, 8),
        (12, 10),
        (13, 13),
        (15, 13),  # non sale al passo superiore
        (16, 16),
        (20, 16),  # oltre max: arrotonda al massimo passo ammesso
    ],
)
def test_quantize_current_arrotonda_per_difetto(raw, expected):
    cfg = make_cfg(current_steps=[6, 8, 10, 13, 16])
    assert quantize_current(cfg, raw) == expected


def test_quantize_current_default_e_identita_sugli_interi():
    cfg = make_cfg(current_steps=[])
    for raw in range(6, 17):
        assert quantize_current(cfg, raw) == raw


# --- desired_current -----------------------------------------------------

def test_desired_current_budget_negativo_va_in_pausa():
    cfg, inp = cfg_for_amps(-1)  # sources oltre il limite
    amps, reasons = desired_current(cfg, inp)
    assert amps == cfg.pause_current
    assert any("pausa" in r for r in reasons)


def test_desired_current_sotto_minimo_va_in_pausa():
    cfg, inp = cfg_for_amps(4)  # 4A < min 6A
    amps, _ = desired_current(cfg, inp)
    assert amps == cfg.pause_current


def test_desired_current_pausa_usa_pause_current_configurato():
    cfg, inp = cfg_for_amps(-1, pause_current=5)
    amps, _ = desired_current(cfg, inp)
    assert amps == 5


def test_desired_current_quantizza_ai_passi():
    # budget = 14A ma passi ammessi 6,8,10,13,16 -> 13A
    cfg, inp = cfg_for_amps(14, current_steps=[6, 8, 10, 13, 16])
    amps, reasons = desired_current(cfg, inp)
    assert amps == 13
    assert any("step" in r for r in reasons)


def test_desired_current_clamp_al_massimo():
    # budget enorme (100A) ma max_current 16
    cfg, inp = cfg_for_amps(100)
    amps, _ = desired_current(cfg, inp)
    assert amps == 16


def test_desired_current_default_e_floor_del_budget():
    # 14.9A di budget, passi = continuo -> floor 14A
    cfg, inp = cfg_for_amps(14.9)
    amps, _ = desired_current(cfg, inp)
    assert amps == 14


# --- next_current (isteresi) --------------------------------------------

def test_next_current_primo_ciclo_applica_subito():
    cfg, inp = cfg_for_amps(10)
    state = BalancerState()
    assert next_current(cfg, inp, state, now=1000.0) == 10
    assert state.applied_current == 10
    assert state.last_change_ts == 1000.0


def test_next_current_discesa_immediata():
    cfg, inp = cfg_for_amps(8)  # target 8A
    state = BalancerState(applied_current=16, last_change_ts=1000.0)
    # anche pochi secondi dopo, la discesa e' immediata
    assert next_current(cfg, inp, state, now=1001.0) == 8
    assert state.applied_current == 8
    assert state.last_change_ts == 1001.0


def test_next_current_salita_bloccata_durante_hold():
    cfg, inp = cfg_for_amps(16)  # vorrebbe salire a 16
    state = BalancerState(applied_current=8, last_change_ts=1000.0)
    # solo 100s < hold 300s -> resta a 8
    out = next_current(cfg, inp, state, now=1100.0)
    assert out == 8
    assert state.applied_current == 8
    assert any("hold" in r for r in state.reasons)


def test_next_current_salita_concessa_dopo_hold():
    cfg, inp = cfg_for_amps(16)
    state = BalancerState(applied_current=8, last_change_ts=1000.0)
    out = next_current(cfg, inp, state, now=1000.0 + cfg.hold_seconds)
    assert out == 16
    assert state.applied_current == 16


def test_next_current_nessuna_variazione_mantiene_stato():
    cfg, inp = cfg_for_amps(10)
    state = BalancerState(applied_current=10, last_change_ts=1000.0)
    out = next_current(cfg, inp, state, now=5000.0)
    assert out == 10
    assert state.last_change_ts == 1000.0  # non resettato


def test_next_current_pausa_imposta_charging_blocked():
    cfg, inp = cfg_for_amps(-1)  # budget negativo -> pausa
    state = BalancerState(applied_current=10, last_change_ts=1000.0)
    out = next_current(cfg, inp, state, now=1001.0)
    assert out == cfg.pause_current
    assert state.charging_blocked is True

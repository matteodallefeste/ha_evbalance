"""Test delle fasce orarie data-driven (`energy.py`).

Coprono: parsing ``HH:MM``, costruzione/validazione dello schema, ``active_band``
su confini ai minuti, stagionalità, weekend, festivi e fallback, più il
round-trip di serializzazione e la validità dei preset spediti in ``tariffs/``.

``energy`` è privo di dipendenze Home Assistant (vedi conftest), quindi gira
stand-alone con solo pytest.
"""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

import energy

TARIFFS_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "evbalance"
    / "tariffs"
)


# --- parse_hm / minutes_to_hm -------------------------------------------

@pytest.mark.parametrize(
    "raw, minutes",
    [("00:00", 0), ("07:30", 450), ("19:00", 1140), ("24:00", 1440), ("8", 480), (8, 480)],
)
def test_parse_hm(raw, minutes):
    assert energy.parse_hm(raw) == minutes


@pytest.mark.parametrize("bad", ["24:01", "25:00", "07:60", "-1:00", True])
def test_parse_hm_invalid(bad):
    with pytest.raises(ValueError):
        energy.parse_hm(bad)


def test_minutes_to_hm_roundtrip():
    for m in (0, 450, 1140, 1440):
        assert energy.parse_hm(energy.minutes_to_hm(m)) == m


# --- schema di riferimento (ARERA-like) ---------------------------------

def _arera_like() -> dict:
    return {
        "type": "tou",
        "id": "t",
        "country": "IT",
        "fallback": "F3",
        "holidays_as": "F3",
        "bands": [
            {"id": "F1", "rank": 3},
            {"id": "F2", "rank": 2},
            {"id": "F3", "rank": 1},
        ],
        "seasons": [
            {
                "months": None,
                "rules": [
                    {"band": "F1", "days": [0, 1, 2, 3, 4], "start": "08:00", "end": "19:00"},
                    {"band": "F2", "days": [0, 1, 2, 3, 4], "start": "19:00", "end": "23:00"},
                    {"band": "F2", "days": [5], "start": "07:00", "end": "23:00"},
                ],
            }
        ],
    }


def test_active_band_basic():
    s = energy.scheme_from_dict(_arera_like())
    # mercoledì 2026-07-08
    assert energy.active_band(s, datetime(2026, 7, 8, 10, 0)) == "F1"
    assert energy.active_band(s, datetime(2026, 7, 8, 20, 0)) == "F2"
    # domenica -> nessuna regola -> fallback
    assert energy.active_band(s, datetime(2026, 7, 12, 10, 0)) == "F3"


def test_active_band_minute_boundary():
    s = energy.scheme_from_dict(_arera_like())
    # 18:59 ancora F1, 19:00 esatto passa a F2 (end esclusa)
    assert energy.active_band(s, datetime(2026, 7, 8, 18, 59)) == "F1"
    assert energy.active_band(s, datetime(2026, 7, 8, 19, 0)) == "F2"


def test_active_band_holidays():
    s = energy.scheme_from_dict(_arera_like())
    d = datetime(2026, 1, 1, 10, 0)  # mercoledì ma festivo
    assert energy.active_band(s, d, holidays={date(2026, 1, 1)}) == "F3"
    # senza set festivi resta la regola feriale
    assert energy.active_band(s, d) == "F1"


def test_seasonal_rules():
    s = energy.scheme_from_dict(
        {
            "type": "tou",
            "id": "seasonal",
            "fallback": "off",
            "bands": [{"id": "peak", "rank": 2}, {"id": "off", "rank": 1}],
            "seasons": [
                {"months": [6, 7, 8], "rules": [
                    {"band": "peak", "days": [0, 1, 2, 3, 4, 5, 6], "start": "13:00", "end": "17:00"},
                ]},
                {"months": [12, 1, 2], "rules": [
                    {"band": "peak", "days": [0, 1, 2, 3, 4, 5, 6], "start": "18:00", "end": "21:00"},
                ]},
            ],
        }
    )
    # luglio: picco pomeridiano
    assert energy.active_band(s, datetime(2026, 7, 1, 14, 0)) == "peak"
    assert energy.active_band(s, datetime(2026, 7, 1, 19, 0)) == "off"
    # gennaio: picco serale
    assert energy.active_band(s, datetime(2026, 1, 15, 19, 0)) == "peak"
    assert energy.active_band(s, datetime(2026, 1, 15, 14, 0)) == "off"


# --- validazione --------------------------------------------------------

def test_compact_bands_rank_by_order():
    s = energy.scheme_from_dict(
        {
            "id": "c",
            "fallback": "b",
            "bands": ["a", "b"],
            "seasons": [{"months": None, "rules": [
                {"band": "a", "days": [0], "start": "00:00", "end": "01:00"},
            ]}],
        }
    )
    assert s.rank_of("a") == 2  # primo = più caro
    assert s.rank_of("b") == 1
    assert s.band_ids == ("a", "b")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["seasons"][0]["rules"][0].update(start="20:00", end="08:00"),  # start>=end
        lambda d: d["seasons"][0]["rules"][0].update(band="ZZ"),  # banda ignota
        lambda d: d.update(fallback="ZZ"),  # fallback ignoto
        lambda d: d.update(holidays_as="ZZ"),  # holidays_as ignoto
        lambda d: d["seasons"][0]["rules"][0].update(days=[9]),  # giorno invalido
        lambda d: d["seasons"][0]["rules"][0].update(start="26:00"),  # ora invalida
        lambda d: d.update(type="dynamic"),  # tipo non supportato
        lambda d: d.update(bands=[]),  # niente bande
    ],
)
def test_invalid_schemes_raise(mutate):
    data = _arera_like()
    mutate(data)
    with pytest.raises(ValueError):
        energy.scheme_from_dict(data)


# --- serializzazione ----------------------------------------------------

def test_scheme_to_dict_roundtrip():
    s1 = energy.scheme_from_dict(_arera_like())
    s2 = energy.scheme_from_dict(energy.scheme_to_dict(s1))
    assert s1.band_ids == s2.band_ids
    assert s1.fallback == s2.fallback
    assert s1.holidays_as == s2.holidays_as
    for dt in (
        datetime(2026, 7, 8, 10, 0),
        datetime(2026, 7, 8, 20, 0),
        datetime(2026, 7, 11, 12, 0),
    ):
        assert energy.active_band(s1, dt) == energy.active_band(s2, dt)


# --- preset spediti -----------------------------------------------------

def test_shipped_presets_valid():
    files = [p for p in TARIFFS_DIR.glob("*.json") if p.name != "schema.json"]
    assert files, "nessun preset trovato"
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        scheme = energy.scheme_from_dict(data, scheme_id=data.get("id"))
        # round-trip stabile
        again = energy.scheme_from_dict(energy.scheme_to_dict(scheme))
        assert again.band_ids == scheme.band_ids


def test_arera_regression():
    """Il preset ARERA deve dare le stesse bande della vecchia logica."""
    data = json.loads((TARIFFS_DIR / "it_arera.json").read_text(encoding="utf-8"))
    s = energy.scheme_from_dict(data, scheme_id="it_arera")
    assert energy.active_band(s, datetime(2026, 7, 8, 10, 30)) == "F1"  # mer 10:30
    assert energy.active_band(s, datetime(2026, 7, 8, 7, 30)) == "F2"   # mer 07:30
    assert energy.active_band(s, datetime(2026, 7, 11, 12, 0)) == "F2"  # sab 12:00
    assert energy.active_band(s, datetime(2026, 7, 12, 12, 0)) == "F3"  # dom 12:00

"""Fasce orarie (time-of-use) e utilità per il calcolo dell'energia.

Le fasce sono "data-driven": un preset è una lista di regole valutate in
ordine; la prima che combacia vince. Aggiungere un preset custom significa
solo aggiungere regole, senza toccare la logica.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Giorni: 0 = lunedì ... 6 = domenica (come datetime.weekday()).
WEEKDAYS = frozenset({0, 1, 2, 3, 4})
SATURDAY = frozenset({5})
SUNDAY = frozenset({6})
ALL_DAYS = frozenset(range(7))


@dataclass(frozen=True)
class BandRule:
    """Regola oraria: se `day` e l'ora [start, end) combaciano -> `band`."""

    band: str
    days: frozenset[int]
    start: int  # ora inclusa (0-24)
    end: int    # ora esclusa (0-24)

    def matches(self, dt: datetime) -> bool:
        return dt.weekday() in self.days and self.start <= dt.hour < self.end


# Preset ARERA (Italia). I giorni festivi nazionali sono trattati come F3
# se passati in `holidays`; la domenica è già F3.
ARERA_RULES: tuple[BandRule, ...] = (
    # F1: Lun-Ven 08-19
    BandRule("F1", WEEKDAYS, 8, 19),
    # F2: Lun-Ven 07-08 e 19-23
    BandRule("F2", WEEKDAYS, 7, 8),
    BandRule("F2", WEEKDAYS, 19, 23),
    # F2: Sab 07-23
    BandRule("F2", SATURDAY, 7, 23),
    # F3: tutto il resto (fallback sotto)
)

FLAT_RULES: tuple[BandRule, ...] = (BandRule("F1", ALL_DAYS, 0, 24),)

TARIFF_PRESETS: dict[str, tuple[tuple[BandRule, ...], str, tuple[str, ...]]] = {
    # nome -> (regole, banda_di_fallback, elenco_bande)
    "arera": (ARERA_RULES, "F3", ("F1", "F2", "F3")),
    "flat": (FLAT_RULES, "F1", ("F1",)),
}


def preset_bands(preset: str) -> tuple[str, ...]:
    """Elenco delle bande esposte da un preset."""
    return TARIFF_PRESETS.get(preset, TARIFF_PRESETS["arera"])[2]


def active_band(preset: str, dt: datetime, holidays: frozenset | None = None) -> str:
    """Banda attiva per il momento `dt` (datetime *locale*)."""
    rules, fallback, _ = TARIFF_PRESETS.get(preset, TARIFF_PRESETS["arera"])
    # Festivi nazionali -> come domenica (F3 per ARERA).
    if holidays and dt.date() in holidays:
        return fallback
    for rule in rules:
        if rule.matches(dt):
            return rule.band
    return fallback


def energy_increment_kwh(power_w: float, seconds: float) -> float:
    """Energia (kWh) accumulata da `power_w` costante per `seconds` secondi."""
    if power_w <= 0 or seconds <= 0:
        return 0.0
    return power_w * seconds / 3600.0 / 1000.0

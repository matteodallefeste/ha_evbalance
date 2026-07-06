# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Fasce orarie (time-of-use) e utilità per il calcolo dell'energia.

Le fasce sono "data-driven": uno schema (:class:`TariffScheme`) è una lista di
regole valutate in ordine; la prima che combacia vince. Gli schemi arrivano dai
file JSON in ``tariffs/`` (vedi :mod:`tariff_loader`) oppure da uno schema custom
salvato nelle options; questo modulo resta **senza dipendenze da Home Assistant**
così da essere testabile stand-alone.

Ogni banda porta un ``rank`` di costo relativo (1 = più economica): oggi serve
solo al reporting, domani è il segnale che un controllo price-aware confronta per
decidere quando spingere la ricarica.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

# Giorni: 0 = lunedì ... 6 = domenica (come datetime.weekday()).
WEEKDAYS = frozenset({0, 1, 2, 3, 4})
SATURDAY = frozenset({5})
SUNDAY = frozenset({6})
ALL_DAYS = frozenset(range(7))

MINUTES_IN_DAY = 24 * 60


def minutes_to_hm(total: int) -> str:
    """Minuti da mezzanotte -> ``"HH:MM"`` (1440 -> ``"24:00"``)."""
    return f"{total // 60:02d}:{total % 60:02d}"


def parse_hm(value: Any) -> int:
    """Converte ``"HH:MM"`` (o ``"HH"``/intero-ora) in minuti da mezzanotte.

    ``"24:00"`` -> 1440 (fine giornata). Solleva ``ValueError`` se fuori range.
    """
    if isinstance(value, bool):  # bool è int: escludilo esplicitamente
        raise ValueError(f"orario non valido: {value!r}")
    if isinstance(value, int):
        hours, minutes = value, 0
    else:
        text = str(value).strip()
        if ":" in text:
            h_str, m_str = text.split(":", 1)
            hours, minutes = int(h_str), int(m_str)
        else:
            hours, minutes = int(text), 0
    if not (0 <= minutes < 60):
        raise ValueError(f"minuti fuori range: {value!r}")
    total = hours * 60 + minutes
    if not (0 <= total <= MINUTES_IN_DAY):
        raise ValueError(f"orario fuori range 00:00-24:00: {value!r}")
    return total


def _days_frozenset(days: Any) -> frozenset[int]:
    out = {int(d) for d in days}
    if not out or any(d < 0 or d > 6 for d in out):
        raise ValueError(f"giorni non validi (attesi 0-6): {days!r}")
    return frozenset(out)


def _months_frozenset(months: Any) -> frozenset[int] | None:
    if months is None:
        return None
    out = {int(m) for m in months}
    if not out or any(m < 1 or m > 12 for m in out):
        raise ValueError(f"mesi non validi (attesi 1-12): {months!r}")
    return frozenset(out)


@dataclass(frozen=True)
class Band:
    """Una fascia esposta dallo schema, con costo relativo (``rank``)."""

    id: str
    rank: int
    label: str = ""
    color: str | None = None


@dataclass(frozen=True)
class BandRule:
    """Regola: se ``months``/``days``/``[start, end)`` combaciano -> ``band``.

    ``start``/``end`` sono minuti da mezzanotte (``end`` esclusa). ``months`` a
    ``None`` significa "tutto l'anno".
    """

    band: str
    days: frozenset[int]
    start: int  # minuti da mezzanotte (inclusi)
    end: int    # minuti da mezzanotte (esclusi)
    months: frozenset[int] | None = None

    def matches(self, dt: datetime) -> bool:
        if self.months is not None and dt.month not in self.months:
            return False
        if dt.weekday() not in self.days:
            return False
        minute = dt.hour * 60 + dt.minute
        return self.start <= minute < self.end


@dataclass(frozen=True)
class TariffScheme:
    """Schema tariffario risolto: bande + regole + fallback + festivi."""

    id: str
    bands: tuple[Band, ...]
    fallback: str
    rules: tuple[BandRule, ...] = ()
    holidays_as: str | None = None
    country: str | None = None
    label: str = ""

    @property
    def band_ids(self) -> tuple[str, ...]:
        return tuple(b.id for b in self.bands)

    def band(self, band_id: str) -> Band | None:
        for b in self.bands:
            if b.id == band_id:
                return b
        return None

    def rank_of(self, band_id: str) -> int | None:
        b = self.band(band_id)
        return b.rank if b is not None else None


# --- Costruzione schema da dict JSON -----------------------------------------


def _bands_from_data(data: dict) -> tuple[Band, ...]:
    raw = data.get("bands")
    if not isinstance(raw, list) or not raw:
        raise ValueError("'bands' mancante o vuoto")
    bands: list[Band] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            # forma compatta: solo id; rank derivato dall'ordine (primo = più caro)
            bands.append(Band(id=item, rank=len(raw) - i))
            continue
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError(f"banda non valida: {item!r}")
        bands.append(
            Band(
                id=str(item["id"]),
                rank=int(item.get("rank", len(raw) - i)),
                label=str(item.get("label", "")),
                color=item.get("color"),
            )
        )
    return tuple(bands)


def _rules_from_data(data: dict, band_ids: frozenset[str]) -> tuple[BandRule, ...]:
    # Accetta sia 'seasons' (con scope mesi) sia un 'rules' piatto (mesi = tutti).
    seasons = data.get("seasons")
    if seasons is None:
        seasons = [{"months": None, "rules": data.get("rules", [])}]
    if not isinstance(seasons, list) or not seasons:
        raise ValueError("'seasons'/'rules' mancante")

    rules: list[BandRule] = []
    for season in seasons:
        months = _months_frozenset(season.get("months"))
        for r in season.get("rules", []):
            band = str(r["band"])
            if band not in band_ids:
                raise ValueError(f"regola su banda sconosciuta: {band!r}")
            start = parse_hm(r["start"])
            end = parse_hm(r["end"])
            if start >= end:
                raise ValueError(f"start>=end nella regola {r!r}")
            rules.append(
                BandRule(
                    band=band,
                    days=_days_frozenset(r["days"]),
                    start=start,
                    end=end,
                    months=months,
                )
            )
    return tuple(rules)


def scheme_from_dict(data: dict, *, scheme_id: str | None = None) -> TariffScheme:
    """Costruisce (e valida) un :class:`TariffScheme` da un dict JSON.

    Solleva ``ValueError`` se lo schema non è valido, così il loader può loggare
    e saltare un file/opzione malformata senza rompere il setup.
    """
    ttype = data.get("type", "tou")
    if ttype != "tou":
        raise ValueError(f"tipo tariffa non supportato: {ttype!r}")

    bands = _bands_from_data(data)
    band_ids = frozenset(b.id for b in bands)

    fallback = str(data.get("fallback") or bands[-1].id)
    if fallback not in band_ids:
        raise ValueError(f"fallback su banda sconosciuta: {fallback!r}")

    holidays_as = data.get("holidays_as")
    if holidays_as is not None:
        holidays_as = str(holidays_as)
        if holidays_as not in band_ids:
            raise ValueError(f"holidays_as su banda sconosciuta: {holidays_as!r}")

    return TariffScheme(
        id=str(scheme_id or data.get("id") or "custom"),
        bands=bands,
        fallback=fallback,
        rules=_rules_from_data(data, band_ids),
        holidays_as=holidays_as,
        country=data.get("country"),
        label=str(data.get("label", "")),
    )


def scheme_to_dict(scheme: TariffScheme) -> dict:
    """Serializza uno schema nella forma JSON (per l'editor del pannello).

    Le regole vengono raggruppate per ``months`` in ``seasons``, inverso di
    :func:`scheme_from_dict`.
    """
    seasons: list[dict] = []
    order: list[frozenset[int] | None] = []
    grouped: dict[frozenset[int] | None, list[dict]] = {}
    for rule in scheme.rules:
        key = rule.months
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(
            {
                "band": rule.band,
                "days": sorted(rule.days),
                "start": minutes_to_hm(rule.start),
                "end": minutes_to_hm(rule.end),
            }
        )
    for key in order:
        seasons.append(
            {"months": sorted(key) if key is not None else None, "rules": grouped[key]}
        )
    return {
        "type": "tou",
        "id": scheme.id,
        "country": scheme.country,
        "label": scheme.label,
        "fallback": scheme.fallback,
        "holidays_as": scheme.holidays_as,
        "bands": [
            {"id": b.id, "rank": b.rank, "label": b.label, "color": b.color}
            for b in scheme.bands
        ],
        "seasons": seasons,
    }


# Schema minimo hardcoded: fallback di sicurezza se la cartella tariffs/ è
# illeggibile (rispecchia tariffs/default.json).
DEFAULT_SCHEME = TariffScheme(
    id="default",
    bands=(Band(id="Default", rank=1, label="Default"),),
    fallback="Default",
    rules=(BandRule("Default", ALL_DAYS, 0, MINUTES_IN_DAY),),
)


# --- Query -------------------------------------------------------------------


def preset_bands(scheme: TariffScheme) -> tuple[str, ...]:
    """Elenco degli id di banda esposti dallo schema."""
    return scheme.band_ids


def active_band(
    scheme: TariffScheme,
    dt: datetime,
    holidays: frozenset[date] | set[date] | None = None,
) -> str:
    """Id della banda attiva per il momento ``dt`` (datetime *locale*)."""
    if scheme.holidays_as and holidays and dt.date() in holidays:
        return scheme.holidays_as
    for rule in scheme.rules:
        if rule.matches(dt):
            return rule.band
    return scheme.fallback


def energy_increment_kwh(power_w: float, seconds: float) -> float:
    """Energia (kWh) accumulata da ``power_w`` costante per ``seconds`` secondi."""
    if power_w <= 0 or seconds <= 0:
        return 0.0
    return power_w * seconds / 3600.0 / 1000.0

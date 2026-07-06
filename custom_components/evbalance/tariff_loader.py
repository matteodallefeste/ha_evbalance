# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Caricamento dei preset tariffari da ``tariffs/*.json`` e utilità festivi.

Il modulo :mod:`energy` resta HA-free e si occupa solo di *costruire/valutare*
gli schemi; qui vive la glue con Home Assistant: I/O su file (in executor, mai
sul loop), cache in ``hass.data`` e calcolo dei festivi nazionali.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from functools import lru_cache
from typing import TYPE_CHECKING

from .const import DOMAIN
from .energy import DEFAULT_SCHEME, TariffScheme, scheme_from_dict

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_PRESETS_KEY = "tariff_presets"
_TARIFFS_DIR = os.path.join(os.path.dirname(__file__), "tariffs")
# File nella cartella tariffs/ che non sono preset.
_SKIP_FILES = {"schema.json"}
# Id storici -> id attuali, per non rompere le config entry già salvate.
_ALIASES = {"arera": "it_arera", "flat": "default"}


def _load_presets_sync() -> dict[str, TariffScheme]:
    """Legge e valida tutti i preset da disco (chiamare in executor)."""
    presets: dict[str, TariffScheme] = {}
    try:
        names = sorted(os.listdir(_TARIFFS_DIR))
    except OSError as err:
        _LOGGER.warning("Cartella tariffs/ non leggibile: %s", err)
        return presets

    for name in names:
        if not name.endswith(".json") or name in _SKIP_FILES:
            continue
        path = os.path.join(_TARIFFS_DIR, name)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            scheme = scheme_from_dict(data, scheme_id=data.get("id") or name[:-5])
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as err:
            _LOGGER.warning("Preset tariffa '%s' ignorato: %s", name, err)
            continue
        presets[scheme.id] = scheme

    return presets


async def async_load_presets(hass: HomeAssistant) -> dict[str, TariffScheme]:
    """Carica i preset (una sola volta) e li mette in cache in ``hass.data``."""
    data = hass.data.setdefault(DOMAIN, {})
    cached = data.get(_PRESETS_KEY)
    if cached is not None:
        return cached
    presets = await hass.async_add_executor_job(_load_presets_sync)
    if "default" not in presets:
        presets["default"] = DEFAULT_SCHEME
    data[_PRESETS_KEY] = presets
    return presets


def get_presets(hass: HomeAssistant) -> dict[str, TariffScheme]:
    """Preset già caricati (dict vuoto se il loader non è ancora girato)."""
    return hass.data.get(DOMAIN, {}).get(_PRESETS_KEY, {})


def canonical_preset(preset: str) -> str:
    """Id attuale di un preset, risolvendo gli alias storici (es. arera->it_arera)."""
    return _ALIASES.get(preset, preset)


def resolve_scheme(
    hass: HomeAssistant, preset: str, tariffs_option: dict | None
) -> TariffScheme:
    """Risolve lo schema attivo: ``custom`` dalle options, altrimenti built-in."""
    if preset == "custom" and tariffs_option:
        try:
            return scheme_from_dict(tariffs_option, scheme_id="custom")
        except (ValueError, KeyError, TypeError) as err:
            _LOGGER.warning("Schema tariffa custom non valido, uso il fallback: %s", err)
            return DEFAULT_SCHEME
    presets = get_presets(hass)
    if preset not in presets:
        preset = _ALIASES.get(preset, preset)
    return presets.get(preset) or presets.get("default") or DEFAULT_SCHEME


# --- Festivi nazionali -------------------------------------------------------


@lru_cache(maxsize=64)
def _holidays_for(country: str, year: int) -> frozenset[date]:
    """Festivi nazionali (date) per ``country``/``year`` via lib ``holidays``."""
    try:
        import holidays as holidays_lib  # dipendenza dichiarata nel manifest
    except ImportError:  # pragma: no cover - manifest garantisce la presenza
        _LOGGER.debug("Libreria 'holidays' non disponibile: festivi disabilitati")
        return frozenset()
    try:
        cal = holidays_lib.country_holidays(country.upper(), years=year)
    except (KeyError, NotImplementedError):
        _LOGGER.debug("Festivi non disponibili per il paese %r", country)
        return frozenset()
    return frozenset(cal.keys())


def holidays_for_scheme(scheme: TariffScheme, year: int) -> frozenset[date]:
    """Set di festivi rilevanti per lo schema (vuoto se non ha ``holidays_as``)."""
    if not scheme.holidays_as or not scheme.country:
        return frozenset()
    return _holidays_for(scheme.country, year)

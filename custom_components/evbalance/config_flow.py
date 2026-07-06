# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Config & options flow per EV Balance."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CURRENT_STEPS,
    CONF_HOLD_SECONDS,
    CONF_MAX_CURRENT,
    CONF_MAX_POWER_W,
    CONF_MIN_CURRENT,
    CONF_NAME,
    CONF_PAUSE_CURRENT,
    CONF_PHASES,
    CONF_SAFETY_MARGIN_W,
    CONF_SHOW_PANEL,
    CONF_SOURCES,
    CONF_SOURCES_INCLUDE_EV_CHARGER,
    CONF_TARIFF_PRESET,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE,
    CONF_EV_CHARGER_CURRENT,
    CONF_EV_CHARGER_POWER,
    DEFAULT_CURRENT_STEPS,
    DEFAULT_HOLD_SECONDS,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MIN_CURRENT,
    DEFAULT_PAUSE_CURRENT,
    DEFAULT_PHASES,
    DEFAULT_SAFETY_MARGIN_W,
    DEFAULT_SHOW_PANEL,
    DEFAULT_SOURCES_INCLUDE_EV_CHARGER,
    DEFAULT_TARIFF_PRESET,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VOLTAGE,
    DOMAIN,
)
from .tariff_loader import get_presets

POWER_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="power")
)
POWER_SENSORS = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="power", multiple=True)
)
NUMBER_ENTITY = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="number")
)
PHASES_SELECT = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value="1", label="Monofase (230V)"),
            selector.SelectOptionDict(value="3", label="Trifase (400V)"),
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)
def _tariff_selector(hass) -> selector.SelectSelector:
    """Selettore tariffa costruito dai preset caricati + voce 'custom'."""
    presets = get_presets(hass)
    options = [
        selector.SelectOptionDict(value=scheme.id, label=scheme.label or scheme.id)
        for scheme in presets.values()
    ]
    if not options:  # loader non ancora girato / cartella illeggibile
        options = [selector.SelectOptionDict(value="flat", label="Fascia unica")]
    options.append(
        selector.SelectOptionDict(value="custom", label="Personalizzata (dal pannello)")
    )
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options, mode=selector.SelectSelectorMode.DROPDOWN
        )
    )


def _country_default_preset(hass, current: str) -> str:
    """Preset da preselezionare: quello già scelto, o quello del paese HA."""
    presets = get_presets(hass)
    if current in presets or current == "custom":
        return current
    country = getattr(hass.config, "country", None)
    if country:
        for scheme in presets.values():
            if scheme.country and scheme.country.upper() == country.upper():
                return scheme.id
    return current


def _parse_steps(raw: Any) -> list[int]:
    """Da testo/lista a lista ordinata di interi unici (>=0).

    Accetta 'valori separati da virgola' oppure gia' una lista. Voci non
    numeriche vengono ignorate; stringa vuota => lista vuota (default min..max).
    """
    if isinstance(raw, (list, tuple)):
        parts = raw
    else:
        parts = str(raw or "").replace(";", ",").split(",")
    out: set[int] = set()
    for part in parts:
        try:
            val = int(float(str(part).strip()))
        except (TypeError, ValueError):
            continue
        if val > 0:
            out.add(val)
    return sorted(out)


def _format_steps(steps: Any) -> str:
    """Lista di interi -> stringa 'a, b, c' per il default del form."""
    return ", ".join(str(s) for s in _parse_steps(steps))


class EVBalanceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Flusso di configurazione iniziale."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_PHASES] = int(user_input[CONF_PHASES])
            if user_input[CONF_MIN_CURRENT] >= user_input[CONF_MAX_CURRENT]:
                errors["base"] = "min_ge_max"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="EV Balance"): str,
                vol.Required(CONF_EV_CHARGER_POWER): POWER_SENSOR,
                vol.Required(CONF_EV_CHARGER_CURRENT): NUMBER_ENTITY,
                vol.Optional(CONF_SOURCES, default=[]): POWER_SENSORS,
                vol.Required(
                    CONF_SOURCES_INCLUDE_EV_CHARGER,
                    default=DEFAULT_SOURCES_INCLUDE_EV_CHARGER,
                ): bool,
                vol.Required(CONF_MAX_POWER_W, default=3300): vol.Coerce(float),
                vol.Required(CONF_VOLTAGE, default=DEFAULT_VOLTAGE): vol.Coerce(float),
                vol.Required(CONF_PHASES, default=str(DEFAULT_PHASES)): PHASES_SELECT,
                vol.Required(CONF_MIN_CURRENT, default=DEFAULT_MIN_CURRENT): vol.Coerce(int),
                vol.Required(CONF_MAX_CURRENT, default=DEFAULT_MAX_CURRENT): vol.Coerce(int),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return EVBalanceOptionsFlow(entry)


class EVBalanceOptionsFlow(OptionsFlow):
    """Modifica a caldo di sorgenti, margini, fasce."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry

    def _current(self, key, default):
        if key in self.entry.options:
            return self.entry.options[key]
        return self.entry.data.get(key, default)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_CURRENT_STEPS] = _parse_steps(
                user_input.get(CONF_CURRENT_STEPS, "")
            )
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SOURCES, default=self._current(CONF_SOURCES, [])
                ): POWER_SENSORS,
                vol.Required(
                    CONF_SOURCES_INCLUDE_EV_CHARGER,
                    default=self._current(
                        CONF_SOURCES_INCLUDE_EV_CHARGER, DEFAULT_SOURCES_INCLUDE_EV_CHARGER
                    ),
                ): bool,
                vol.Required(
                    CONF_SAFETY_MARGIN_W,
                    default=self._current(CONF_SAFETY_MARGIN_W, DEFAULT_SAFETY_MARGIN_W),
                ): vol.Coerce(float),
                vol.Required(
                    CONF_PAUSE_CURRENT,
                    default=self._current(CONF_PAUSE_CURRENT, DEFAULT_PAUSE_CURRENT),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_CURRENT_STEPS,
                    default=_format_steps(
                        self._current(CONF_CURRENT_STEPS, DEFAULT_CURRENT_STEPS)
                    ),
                ): str,
                vol.Required(
                    CONF_HOLD_SECONDS,
                    default=self._current(CONF_HOLD_SECONDS, DEFAULT_HOLD_SECONDS),
                ): vol.Coerce(int),
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=self._current(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.Coerce(int),
                vol.Required(
                    CONF_TARIFF_PRESET,
                    default=_country_default_preset(
                        self.hass,
                        self._current(CONF_TARIFF_PRESET, DEFAULT_TARIFF_PRESET),
                    ),
                ): _tariff_selector(self.hass),
                vol.Required(
                    CONF_SHOW_PANEL,
                    default=self._current(CONF_SHOW_PANEL, DEFAULT_SHOW_PANEL),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

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
    CONF_ALLOWED_BANDS,
    CONF_CONTROL_MODE,
    CONF_CURRENT_STEPS,
    CONF_HOLD_SECONDS,
    CONF_MAX_CURRENT,
    CONF_MAX_POWER_W,
    CONF_MIN_CURRENT,
    CONF_NAME,
    CONF_OCPP_CONNECTOR,
    CONF_OCPP_CP_ID,
    CONF_OCPP_METER_INTERVAL,
    CONF_OCPP_PASSWORD,
    CONF_OCPP_PORT,
    CONF_OCPP_USE_HA_PORT,
    CONF_PAUSE_CURRENT,
    CONF_PHASES,
    CONF_SAFETY_MARGIN_W,
    CONF_SHOW_PANEL,
    CONF_SOURCES,
    CONF_SOURCES_INCLUDE_EV_CHARGER,
    CONF_TARIFF_PRESET,
    CONF_TARIFFS,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE,
    CONF_EV_CHARGER_CURRENT,
    CONF_EV_CHARGER_POWER,
    CONF_EV_CHARGER_SWITCH,
    CONF_EV_CHARGER_SWITCH_INVERT,
    DEFAULT_ALLOWED_BANDS,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CURRENT_STEPS,
    DEFAULT_EV_CHARGER_SWITCH_INVERT,
    DEFAULT_HOLD_SECONDS,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MIN_CURRENT,
    DEFAULT_OCPP_CONNECTOR,
    DEFAULT_OCPP_CP_ID,
    DEFAULT_OCPP_METER_INTERVAL,
    DEFAULT_OCPP_PASSWORD,
    DEFAULT_OCPP_PORT,
    DEFAULT_OCPP_USE_HA_PORT,
    DEFAULT_PAUSE_CURRENT,
    DEFAULT_PHASES,
    DEFAULT_SAFETY_MARGIN_W,
    DEFAULT_SHOW_PANEL,
    DEFAULT_SOURCES_INCLUDE_EV_CHARGER,
    DEFAULT_TARIFF_PRESET,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VOLTAGE,
    DOMAIN,
    MODE_ENTITIES,
    MODE_OCPP,
    OCPP_OPTION_KEYS,
)
from .tariff_loader import canonical_preset, get_presets, resolve_scheme

POWER_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="power")
)
POWER_SENSORS = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="power", multiple=True)
)
NUMBER_ENTITY = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="number")
)
CHARGE_SWITCH = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["switch", "input_boolean"])
)
PASSWORD = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
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
        options = [selector.SelectOptionDict(value="default", label="Default")]
    options.append(
        selector.SelectOptionDict(value="custom", label="Personalizzata (dal pannello)")
    )
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options, mode=selector.SelectSelectorMode.DROPDOWN
        )
    )


def _bands_selector(hass, preset: str, tariffs) -> selector.SelectSelector:
    """Multi-selezione delle fasce dello schema attivo.

    Le fasce dipendono dalla tariffa scelta, quindi l'elenco si costruisce dallo
    schema risolto invece di essere fisso.
    """
    scheme = resolve_scheme(hass, preset, tariffs)
    options = [
        selector.SelectOptionDict(value=band.id, label=band.label or band.id)
        for band in scheme.bands
    ]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _country_default_preset(hass, current: str) -> str:
    """Preset da preselezionare: la scelta esplicita, o quello del paese HA.

    Se non è stata scelta una tariffa specifica (valore ``default``, alias
    storico o id ignoto) si prova a proporre quella del paese configurato in HA.
    """
    presets = get_presets(hass)
    current = canonical_preset(current)
    if current == "custom" or (current in presets and current != "default"):
        return current
    country = getattr(hass.config, "country", None)
    if country:
        for scheme in presets.values():
            if scheme.country and scheme.country.upper() == country.upper():
                return scheme.id
    return current if current in presets else "default"


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


def _plant_schema() -> dict:
    """Campi dell'impianto, comuni alle due modalità di controllo."""
    return {
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


class EVBalanceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Flusso di configurazione iniziale."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prima scelta: come si comanda la wallbox."""
        return self.async_show_menu(
            step_id="user", menu_options=[MODE_ENTITIES, MODE_OCPP]
        )

    def _finish(self, user_input: dict[str, Any], mode: str) -> ConfigFlowResult:
        user_input[CONF_PHASES] = int(user_input[CONF_PHASES])
        user_input[CONF_CONTROL_MODE] = mode
        # Le impostazioni OCPP sono modificabili a caldo: devono nascere nelle
        # options, dove le cercano l'options flow e il pannello. Scriverle anche
        # nei dati creerebbe due copie divergenti dello stesso valore.
        options = {
            key: user_input.pop(key) for key in OCPP_OPTION_KEYS if key in user_input
        }
        return self.async_create_entry(
            title=user_input[CONF_NAME], data=user_input, options=options
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wallbox già integrata in HA: si pilota tramite le sue entità."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_MIN_CURRENT] >= user_input[CONF_MAX_CURRENT]:
                errors["base"] = "min_ge_max"
            else:
                return self._finish(user_input, MODE_ENTITIES)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="EV Balance"): str,
                vol.Required(CONF_EV_CHARGER_POWER): POWER_SENSOR,
                vol.Required(CONF_EV_CHARGER_CURRENT): NUMBER_ENTITY,
                vol.Optional(CONF_EV_CHARGER_SWITCH): CHARGE_SWITCH,
                **_plant_schema(),
            }
        )
        return self.async_show_form(
            step_id=MODE_ENTITIES, data_schema=schema, errors=errors
        )

    async def async_step_ocpp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """La wallbox si collega direttamente a EV Balance parlando OCPP 1.6J."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_MIN_CURRENT] >= user_input[CONF_MAX_CURRENT]:
                errors["base"] = "min_ge_max"
            elif not 1 <= int(user_input[CONF_OCPP_PORT]) <= 65535:
                errors["base"] = "invalid_port"
            else:
                user_input[CONF_OCPP_CP_ID] = str(
                    user_input.get(CONF_OCPP_CP_ID, "") or ""
                ).strip()
                return self._finish(user_input, MODE_OCPP)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="EV Balance"): str,
                vol.Required(CONF_OCPP_PORT, default=DEFAULT_OCPP_PORT): vol.Coerce(int),
                vol.Optional(CONF_OCPP_CP_ID, default=DEFAULT_OCPP_CP_ID): str,
                vol.Optional(CONF_OCPP_PASSWORD, default=DEFAULT_OCPP_PASSWORD): PASSWORD,
                vol.Required(
                    CONF_OCPP_USE_HA_PORT, default=DEFAULT_OCPP_USE_HA_PORT
                ): bool,
                # Riserva: serve solo se la wallbox non manda telemetria.
                vol.Optional(CONF_EV_CHARGER_POWER): POWER_SENSOR,
                **_plant_schema(),
            }
        )
        return self.async_show_form(
            step_id=MODE_OCPP, data_schema=schema, errors=errors
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
            # La OptionsFlow sostituisce l'intero dict options: preserva le chiavi
            # gestite solo dal pannello (schema fasce custom, prezzi, valuta),
            # altrimenti salvando da qui andrebbero perse.
            merged = dict(self.entry.options)
            merged.update(user_input)
            return self.async_create_entry(title="", data=merged)

        mode = self._current(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
        if mode == MODE_OCPP:
            # In OCPP la pausa è un limite di 0 A: niente switch, niente
            # corrente di pausa da inventare.
            charger_fields = {
                vol.Required(
                    CONF_OCPP_PORT,
                    default=self._current(CONF_OCPP_PORT, DEFAULT_OCPP_PORT),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_OCPP_CP_ID,
                    default=self._current(CONF_OCPP_CP_ID, DEFAULT_OCPP_CP_ID),
                ): str,
                vol.Optional(
                    CONF_OCPP_PASSWORD,
                    default=self._current(CONF_OCPP_PASSWORD, DEFAULT_OCPP_PASSWORD),
                ): PASSWORD,
                vol.Required(
                    CONF_OCPP_CONNECTOR,
                    default=self._current(CONF_OCPP_CONNECTOR, DEFAULT_OCPP_CONNECTOR),
                ): vol.Coerce(int),
                vol.Required(
                    CONF_OCPP_METER_INTERVAL,
                    default=self._current(
                        CONF_OCPP_METER_INTERVAL, DEFAULT_OCPP_METER_INTERVAL
                    ),
                ): vol.Coerce(int),
                vol.Required(
                    CONF_OCPP_USE_HA_PORT,
                    default=self._current(
                        CONF_OCPP_USE_HA_PORT, DEFAULT_OCPP_USE_HA_PORT
                    ),
                ): bool,
            }
        else:
            charger_fields = {
                vol.Optional(
                    CONF_EV_CHARGER_SWITCH,
                    description={
                        "suggested_value": self._current(CONF_EV_CHARGER_SWITCH, None)
                    },
                ): CHARGE_SWITCH,
                vol.Required(
                    CONF_EV_CHARGER_SWITCH_INVERT,
                    default=self._current(
                        CONF_EV_CHARGER_SWITCH_INVERT, DEFAULT_EV_CHARGER_SWITCH_INVERT
                    ),
                ): bool,
                vol.Required(
                    CONF_PAUSE_CURRENT,
                    default=self._current(CONF_PAUSE_CURRENT, DEFAULT_PAUSE_CURRENT),
                ): vol.Coerce(int),
            }

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
                **charger_fields,
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
                vol.Optional(
                    CONF_ALLOWED_BANDS,
                    default=list(
                        self._current(CONF_ALLOWED_BANDS, DEFAULT_ALLOWED_BANDS) or []
                    ),
                ): _bands_selector(
                    self.hass,
                    self._current(CONF_TARIFF_PRESET, DEFAULT_TARIFF_PRESET),
                    self._current(CONF_TARIFFS, None),
                ),
                vol.Required(
                    CONF_SHOW_PANEL,
                    default=self._current(CONF_SHOW_PANEL, DEFAULT_SHOW_PANEL),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

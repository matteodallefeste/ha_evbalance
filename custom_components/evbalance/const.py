# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Constants for the EV Balance integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "evbalance"
PLATFORMS = ["sensor", "switch", "binary_sensor"]

# --- Config entry data (impostazioni "strutturali", da config flow iniziale) ---
CONF_NAME = "name"
CONF_MAX_POWER_W = "max_power_w"          # limite contatore in Watt (soglia stacco)
CONF_VOLTAGE = "voltage"                  # tensione di linea (230 monofase, 400 trifase)
CONF_PHASES = "phases"                    # 1 o 3
CONF_EV_CHARGER_POWER = "ev_charger_power_entity"    # sensore potenza istantanea EV Charger (W)
CONF_EV_CHARGER_CURRENT = "ev_charger_current_entity"  # number su cui scrivo gli Ampere
CONF_EV_CHARGER_SWITCH = "ev_charger_switch_entity"  # switch/input_boolean pausa-ripresa ricarica
CONF_EV_CHARGER_SWITCH_INVERT = "ev_charger_switch_invert"  # True = lo stato ON significa "in pausa"

# --- Options (modificabili a caldo) ---
CONF_SOURCES = "sources"                  # lista di entity_id sensori potenza
CONF_SOURCES_INCLUDE_EV_CHARGER = "sources_include_ev_charger"  # True = la sorgente misura anche la EV Charger
CONF_SAFETY_MARGIN_W = "safety_margin_w"  # riserva di sicurezza in W
CONF_MIN_CURRENT = "min_current"          # A minimi di ricarica (sotto -> pausa)
CONF_MAX_CURRENT = "max_current"          # A massimi impostabili sulla EV Charger
CONF_CURRENT_STEPS = "current_steps"      # valori A ammessi (vuoto = ogni intero min..max)
CONF_PAUSE_CURRENT = "pause_current"      # A scritti per "fermare" la ricarica (default 0)
CONF_HOLD_SECONDS = "hold_seconds"        # tempo minimo prima di rialzare la corrente
CONF_UPDATE_INTERVAL = "update_interval"  # frequenza di lettura/attuazione (s)
CONF_TARIFF_PRESET = "tariff_preset"      # id preset (es. "it_arera", "default") o "custom"
CONF_TARIFFS = "tariffs"                  # definizione fasce (data-driven)
CONF_TARIFF_PRICES = "tariff_prices"      # {band_id: prezzo €/kWh} per stima costi
CONF_CURRENCY = "currency"                # simbolo valuta per la stima costi
CONF_SHOW_PANEL = "show_panel"            # mostra il pannello nella sidebar

# --- Default ---
DEFAULT_VOLTAGE = 230
DEFAULT_PHASES = 1
DEFAULT_SAFETY_MARGIN_W = 200
DEFAULT_MIN_CURRENT = 6
DEFAULT_MAX_CURRENT = 16
DEFAULT_CURRENT_STEPS: list[int] = []   # vuoto = ogni intero da min_current a max_current
DEFAULT_PAUSE_CURRENT = 0
DEFAULT_HOLD_SECONDS = 300          # 5 minuti
DEFAULT_UPDATE_INTERVAL = 3         # secondi
DEFAULT_TARIFF_PRESET = "default"   # tariffa usata finché non se ne seleziona una
DEFAULT_CURRENCY = "€"
DEFAULT_SHOW_PANEL = True
DEFAULT_SOURCES_INCLUDE_EV_CHARGER = False
DEFAULT_EV_CHARGER_SWITCH_INVERT = False

MIN_UPDATE_INTERVAL = timedelta(seconds=3)

# Fattore di conversione W -> A gestito in balancer.py in base a voltage/phases.

# --- Pannello sidebar (custom panel, servito come file JS statico) ---
PANEL_URL_PATH = "evbalance"                 # /evbalance nella sidebar
PANEL_TITLE = "EV Balance"
PANEL_ICON = "mdi:ev-station"
# La cartella www/ è servita per intero: il modulo principale importa il modulo
# fratello delle traduzioni tramite path relativo, quindi dev'essere raggiungibile.
PANEL_STATIC_URL = "/evbalance_static"       # URL base (cartella www/)
PANEL_JS_FILENAME = "evbalance-panel.js"     # modulo principale del pannello
PANEL_JS_VERSION = "14"                        # bump per invalidare la cache del browser
WS_TYPE_PANEL = "evbalance/panel"             # comando websocket usato dal pannello

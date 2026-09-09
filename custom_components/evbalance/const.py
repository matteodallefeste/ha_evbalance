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

# Come si comanda la wallbox: tramite entita' HA oppure parlandole in OCPP.
CONF_CONTROL_MODE = "control_mode"
MODE_ENTITIES = "entities"
MODE_OCPP = "ocpp"

# --- OCPP 1.6J (CSMS integrato) ---
CONF_OCPP_PORT = "ocpp_port"                  # porta del server websocket
CONF_OCPP_CP_ID = "ocpp_cp_id"                # Charge Point ID atteso ("" = qualsiasi)
CONF_OCPP_PASSWORD = "ocpp_password"          # basic auth, se impostata sulla wallbox
CONF_OCPP_CONNECTOR = "ocpp_connector"        # connettore da pilotare (di norma 1)
CONF_OCPP_METER_INTERVAL = "ocpp_meter_interval"  # ogni quanti s vogliamo i MeterValues
CONF_OCPP_PROFILE_PURPOSE = "ocpp_profile_purpose"  # TxDefaultProfile | TxProfile
CONF_OCPP_USE_HA_PORT = "ocpp_use_ha_port"    # True = endpoint sulla porta di HA

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
CONF_ALLOWED_BANDS = "allowed_bands"      # fasce in cui è consentito ricaricare
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
DEFAULT_ALLOWED_BANDS: list[str] = []   # vuoto = si ricarica in tutte le fasce
DEFAULT_TARIFF_PRESET = "default"   # tariffa usata finché non se ne seleziona una
DEFAULT_CURRENCY = "€"
DEFAULT_SHOW_PANEL = True
DEFAULT_SOURCES_INCLUDE_EV_CHARGER = False
DEFAULT_EV_CHARGER_SWITCH_INVERT = False

DEFAULT_CONTROL_MODE = MODE_ENTITIES
DEFAULT_OCPP_PORT = 9000
DEFAULT_OCPP_CP_ID = ""             # vuoto = accetta la prima wallbox che si presenta
DEFAULT_OCPP_PASSWORD = ""
DEFAULT_OCPP_CONNECTOR = 1
DEFAULT_OCPP_METER_INTERVAL = 10    # s: abbastanza fitto da verificare i limiti
DEFAULT_OCPP_PROFILE_PURPOSE = "TxDefaultProfile"
DEFAULT_OCPP_USE_HA_PORT = False

# Impostazioni OCPP modificabili a caldo: vivono nelle *options*, non nei dati
# strutturali. Elenco condiviso da config flow, options flow e pannello, così
# non finiscono per errore in due posti diversi con valori divergenti.
OCPP_OPTION_KEYS = (
    CONF_OCPP_PORT,
    CONF_OCPP_CP_ID,
    CONF_OCPP_PASSWORD,
    CONF_OCPP_CONNECTOR,
    CONF_OCPP_METER_INTERVAL,
    CONF_OCPP_PROFILE_PURPOSE,
    CONF_OCPP_USE_HA_PORT,
)

# Chiavi in hass.data per il server OCPP condiviso.
OCPP_CSMS_KEY = "ocpp_csms"
OCPP_VIEW_FLAG = "ocpp_view_registered"

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
PANEL_TRANSLATIONS_FILENAME = "evbalance-translations.js"  # modulo fratello
# Il token anti-cache viene calcolato dal contenuto dei due moduli JS (vedi
# panel.py): cambia da solo a ogni modifica, senza bump manuali. Questo valore
# resta solo come ripiego se i file non fossero leggibili.
PANEL_JS_VERSION = "16"
WS_TYPE_PANEL = "evbalance/panel"             # comando websocket usato dal pannello

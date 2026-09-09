"""Rende importabili i moduli puri dell'integrazione senza installare Home Assistant."""

import importlib.util
import sys
import types
from pathlib import Path

# balancer.py e ocpp_messages.py vivono dentro custom_components/evbalance e sono
# privi di dipendenze HA: li esponiamo direttamente sul path cosi' i test girano
# stand-alone (solo pytest).
_EV_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "evbalance"
if str(_EV_DIR) not in sys.path:
    sys.path.insert(0, str(_EV_DIR))


def _stub_homeassistant() -> None:
    """Finge le poche API di Home Assistant usate da `ocpp_server`.

    Il server OCPP tocca HA solo per il decoratore `callback`, il tipo
    `HomeAssistant` e la classe base delle view: stubbandole possiamo collaudare
    il dialogo con la wallbox senza installare tutto Home Assistant.
    """
    if "homeassistant" in sys.modules:
        return
    if importlib.util.find_spec("homeassistant") is not None:
        return   # Home Assistant e' installato davvero: niente stub

    root = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func

    components = types.ModuleType("homeassistant.components")
    http = types.ModuleType("homeassistant.components.http")

    class HomeAssistantView:  # minimo indispensabile per la sottoclasse
        url = ""
        name = ""
        requires_auth = True

    http.HomeAssistantView = HomeAssistantView

    root.core = core
    root.components = components
    components.http = http
    sys.modules.update(
        {
            "homeassistant": root,
            "homeassistant.core": core,
            "homeassistant.components": components,
            "homeassistant.components.http": http,
        }
    )


_stub_homeassistant()

# Nome del package finto sotto cui caricare i moduli che usano import relativi.
EVBALANCE_PKG = "evbalance_pkg"


def _register_package() -> None:
    """Espone la cartella dell'integrazione come package importabile.

    `ocpp_server.py` fa `from .ocpp_messages import ...`: un import relativo
    richiede un package padre. Ne creiamo uno finto che punta alla stessa
    cartella, riusando il modulo `ocpp_messages` gia' caricato in piano cosi'
    non ne esistano due copie.
    """
    if EVBALANCE_PKG in sys.modules:
        return
    package = types.ModuleType(EVBALANCE_PKG)
    package.__path__ = [str(_EV_DIR)]
    sys.modules[EVBALANCE_PKG] = package

    import ocpp_messages

    sys.modules[f"{EVBALANCE_PKG}.ocpp_messages"] = ocpp_messages


_register_package()

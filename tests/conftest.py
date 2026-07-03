"""Rende importabile il package puro `balancer` senza dipendenze da Home Assistant."""

import sys
from pathlib import Path

# balancer.py vive dentro custom_components/evbalance ed e' privo di dipendenze HA:
# lo esponiamo direttamente sul path cosi' i test girano stand-alone (solo pytest).
_EV_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "evbalance"
if str(_EV_DIR) not in sys.path:
    sys.path.insert(0, str(_EV_DIR))

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Valida i preset tariffa in ``custom_components/evbalance/tariffs``.

Due livelli:
  1. struttura -> JSON Schema (``schema.json``, se ``jsonschema`` è installato);
  2. semantica -> ``energy.scheme_from_dict`` (start<end, band note, fallback, ...).

Esce con codice 1 se un file non è valido. Usato in CI e lanciabile a mano.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARIFFS_DIR = ROOT / "custom_components" / "evbalance" / "tariffs"
SKIP = {"schema.json"}

# energy.py è HA-free: lo importiamo direttamente dal package.
sys.path.insert(0, str(ROOT / "custom_components" / "evbalance"))
import energy  # noqa: E402


def _load_schema_validator():
    try:
        import jsonschema  # type: ignore
    except ImportError:
        print("jsonschema non installato: salto la validazione strutturale")
        return None
    schema = json.loads((TARIFFS_DIR / "schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def main() -> int:
    validator = _load_schema_validator()
    errors = 0
    files = sorted(p for p in TARIFFS_DIR.glob("*.json") if p.name not in SKIP)
    if not files:
        print("Nessun preset trovato")
        return 1

    seen_ids: dict[str, str] = {}
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            print(f"[FAIL] {path.name}: JSON non valido: {err}")
            errors += 1
            continue

        if validator is not None:
            schema_errs = sorted(validator.iter_errors(data), key=lambda e: e.path)
            for e in schema_errs:
                loc = "/".join(str(p) for p in e.path) or "(root)"
                print(f"[FAIL] {path.name}: schema @ {loc}: {e.message}")
            errors += len(schema_errs)

        try:
            scheme = energy.scheme_from_dict(data, scheme_id=data.get("id"))
        except (ValueError, KeyError, TypeError) as err:
            print(f"[FAIL] {path.name}: semantica: {err}")
            errors += 1
            continue

        if scheme.id in seen_ids:
            print(f"[FAIL] {path.name}: id duplicato '{scheme.id}' (già in {seen_ids[scheme.id]})")
            errors += 1
        seen_ids[scheme.id] = path.name

        print(f"[ok]   {path.name} -> {scheme.id} {list(scheme.band_ids)}")

    if errors:
        print(f"\n{errors} errore/i di validazione")
        return 1
    print(f"\nTutti i {len(files)} preset sono validi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Controlli sulle traduzioni, prima che ci pensi hassfest.

hassfest gira solo su GitHub e fallisce a cose gia' pubblicate. Qui
replichiamo le sue regole che ci siamo trovati a violare, in modo da
accorgercene in locale:

* nessun HTML nei valori (``cv.string_with_no_html``): la regex e' la stessa
  di Home Assistant, quindi un segnaposto come ``<porta>`` viene scambiato per
  un tag e rifiutato -- usare parentesi quadre;
* stesse chiavi in tutte le lingue, cosi' nessuna resta senza traduzione.

Uso:  python tools/check_translations.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# homeassistant/helpers/config_validation.py -> string_with_no_html
RE_HTML = re.compile(r"<[a-z].*?>", re.IGNORECASE)

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "evbalance"


def iter_strings(node: object, path: str = ""):
    """Percorre il JSON restituendo (percorso, valore) per ogni stringa."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from iter_strings(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_strings(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


def translation_files() -> list[Path]:
    files = [BASE / "strings.json"]
    files.extend(sorted((BASE / "translations").glob("*.json")))
    return [f for f in files if f.exists()]


def main() -> int:
    files = translation_files()
    if not files:
        print("nessun file di traduzione trovato", file=sys.stderr)
        return 1

    errors: list[str] = []
    keys_by_file: dict[str, set[str]] = {}

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            errors.append(f"{path.name}: JSON non valido ({err})")
            continue

        keys_by_file[path.name] = {key for key, _ in iter_strings(data)}
        for key, value in iter_strings(data):
            if RE_HTML.search(value):
                errors.append(
                    f"{path.name}: '{key}' contiene qualcosa che hassfest legge "
                    f"come HTML -> {value[:70]}..."
                )

    # Parita' delle chiavi: strings.json e' il riferimento.
    reference = keys_by_file.get("strings.json", set())
    for name, keys in keys_by_file.items():
        if name == "strings.json":
            continue
        for missing in sorted(reference - keys):
            errors.append(f"{name}: manca la chiave '{missing}'")
        for extra in sorted(keys - reference):
            errors.append(f"{name}: chiave '{extra}' assente da strings.json")

    if errors:
        for line in errors:
            print(f"  {line}")
        print(f"\n{len(errors)} problemi nelle traduzioni")
        return 1

    total = sum(len(k) for k in keys_by_file.values())
    print(f"Traduzioni valide: {len(files)} file, {total} stringhe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

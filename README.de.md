<p align="center">
  <img src="brand/logo.png" alt="EV Balance" width="360">
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.it.md">Italiano</a> · <b>Deutsch</b> · <a href="README.fr.md">Français</a>
</p>

# EV Balance — Energie-Lastmanager für Home Assistant

[![Version](https://img.shields.io/github/v/tag/matteodallefeste/ha_evbalance?sort=semver&label=version)](https://github.com/matteodallefeste/ha_evbalance/tags)
[![HACS: Custom](https://img.shields.io/badge/HACS-Custom-orange)](https://github.com/custom-components/hacs)
[![Home Assistant: Integration](https://img.shields.io/badge/Home%20Assistant-Integration-blue)](https://www.home-assistant.io/)
[![hassfest](https://github.com/matteodallefeste/ha_evbalance/actions/workflows/hassfest.yml/badge.svg)](https://github.com/matteodallefeste/ha_evbalance/actions/workflows/hassfest.yml)
[![HACS validation](https://github.com/matteodallefeste/ha_evbalance/actions/workflows/validate.yml/badge.svg)](https://github.com/matteodallefeste/ha_evbalance/actions/workflows/validate.yml)
[![License](https://img.shields.io/badge/license-GPLv3-blue)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/matteodallefeste/ha_evbalance)](https://github.com/matteodallefeste/ha_evbalance/commits)
[![Issues](https://img.shields.io/github/issues/matteodallefeste/ha_evbalance)](https://github.com/matteodallefeste/ha_evbalance/issues)
[![Stars](https://img.shields.io/github/stars/matteodallefeste/ha_evbalance?style=flat)](https://github.com/matteodallefeste/ha_evbalance/stargazers)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=matteodallefeste&repository=ha_evbalance&category=integration)

Custom-Integration (installierbar über **HACS**), die eine Abschaltung des
Zählers durch Überlast verhindert, indem sie den Strom der Wallbox anhand des
Hausverbrauchs moduliert, und die Energie nach **Zeittarifen** (italienische
ARERA-Zeitfenster F1/F2/F3) mit täglichem und monatlichem Reset erfasst.

## Funktionsweise

In jedem Zyklus (Standard alle 3 s) führt die Integration Folgendes aus:

1. liest die **Momentanleistung** der Wallbox und der konfigurierten Quellen;
2. berechnet das verfügbare Budget:
   `Budget = Zählerlimit − Sicherheitsmarge − Quellenverbrauch`;
3. rechnet das Budget in Ampere um (abhängig von Spannung und Phasenzahl) und
   schreibt es in die **Number-Entität** der Wallbox;
4. übersteigt der Verbrauch ohne Wallbox das Limit, wird die Wallbox
   **pausiert**.

### Hysterese (Anti-Flattern)

- **Reduzierung / Pause → sofort** (Sicherheit).
- **Erhöhung → erst nach `hold_seconds`** (Standard 300 s = 5 min) seit der
  letzten Änderung erlaubt. So wird der Wert nicht ständig verändert.

## Installation (HACS)

1. HACS → *Integrations* → Menü ⋮ → **Custom repositories**.
2. Die URL dieses Repositorys hinzufügen, Kategorie **Integration**.
3. **EV Balance** installieren und Home Assistant neu starten.
4. *Einstellungen → Geräte & Dienste → Integration hinzufügen → EV Balance*.

> Alternativ den Ordner `custom_components/evbalance/` in den eigenen
> `config/custom_components/`-Ordner kopieren und neu starten.

## Konfiguration

**Ersteinrichtung (strukturell, bei der ersten Konfiguration festgelegt):**

| Parameter | Standard | Wofür |
|---|---|---|
| Name | EV Balance | Name der Integrationsinstanz |
| Leistungssensor Wallbox | — | `sensor.*` (device_class power) in W/kW mit der aktuellen Wallbox-Leistung |
| Strom-Number Wallbox | — | `number.*`-Entität, in die der Balancer das maximale Ampere schreibt |
| Verbrauchsquellen | (keine) | Leistungssensoren des restlichen Hauses, vom Budget abgezogen (Mehrfachauswahl) |
| Quelle enthält Wallbox | aus | EIN, wenn eine Quelle die Wallbox bereits mitmisst, damit sie nicht doppelt zählt |
| Maximales Zählerlimit | 3300 W | Leistung, ab der der Zähler abschaltet; die Obergrenze, unter der der Balancer bleibt |
| Spannung | 230 V | Netzspannung, dient zur Umrechnung Watt ↔ Ampere |
| Versorgung / Phasen | Einphasig | Einphasig (1) oder dreiphasig (3), beeinflusst die W↔A-Umrechnung |
| Min-Strom | 6 A | Darunter wird die Wallbox pausiert statt gedrosselt |
| Max-Strom | 16 A | Höchster Strom, der auf die Wallbox geschrieben werden kann |

**Optionen (zur Laufzeit änderbar, ohne Neustart):**

| Parameter | Standard | Wofür |
|---|---|---|
| Verbrauchsquellen | (keine) | Wie oben, später änderbar |
| Quelle enthält Wallbox | aus | Wie oben, später änderbar |
| Sicherheitsmarge | 200 W | Unter dem Zählerlimit frei gehaltene Reserve, fängt Spitzen ab |
| Pausenstrom | 0 A | Wert, der zum „Stoppen“ des Ladens in Pause geschrieben wird (manche Wallboxen brauchen einen Wert > 0) |
| Erlaubte Stromstufen | (leer) | Kommagetrennte Liste erlaubter Ampere (z. B. `6, 8, 10, 16`); leer = jede Ganzzahl von min bis max |
| Hold seconds | 300 s | Mindestwartezeit, bevor der Strom wieder erhöht werden darf (Anti-Flattern) |
| Aktualisierungsintervall | 3 s | Wie oft die Leistung gelesen und der Strom angewendet wird (mindestens 3 s) |
| Tarif-Preset | ARERA F1/F2/F3 | Zeitfenster-Satz für die Energieerfassung (ARERA oder Einzeltarif) |
| Panel anzeigen | ein | Blendet das EV-Balance-Panel in der Seitenleiste ein/aus |

## Erstellte Entitäten

- **Switch** *Balancing aktiv* — bei AUS wird gelesen, aber die Wallbox nicht angesteuert.
- **Binary Sensor** *Laden pausiert* — mit dem Attribut `reasons` (erklärt die Entscheidung).
- **Number** *Maximales Zählerlimit*, *Sicherheitsmarge* — Live-Tuning.
- **Sensor** Gesamt-/Quellen-/Wallbox-Leistung, *Erlaubter Strom*, *Aktiver Tarif*.
- **Energie-Sensor** für jede Quelle × Tarif × Zeitraum (täglich + monatlich),
  in kWh, `state_class: total_increasing` → kompatibel mit dem Energie-Dashboard.

## Seitenleisten-Panel

Die Integration registriert ein optionales **Seitenleisten-Panel** (Custom
Element, ohne Build-Schritt), das Live-Leistung, erlaubten Strom, Zählerlimit
und die Energie pro Tarif der letzten Monate anzeigt. Es liest alles aus
vorhandenen Entitäten und den Langzeitstatistiken des Recorders — kein
zusätzlicher Speicher. Ein-/ausschaltbar über die Optionen (*Panel anzeigen*).

## ARERA-Zeitfenster

| Tarif | Wann |
|---|---|
| **F1** | Mo–Fr 08:00–19:00 |
| **F2** | Mo–Fr 07:00–08:00 und 19:00–23:00; Sa 07:00–23:00 |
| **F3** | Mo–Fr 23:00–07:00; Sa 23:00–07:00; Sonn- und Feiertage |

Die Zeitfenster sind datengetrieben
([`energy.py`](custom_components/evbalance/energy.py)): ein eigenes Preset
hinzuzufügen bedeutet, Regeln zu ergänzen, ohne die Logik zu ändern.

## Entwicklung

Die Balancing-Logik ist isoliert und testbar in
[`balancer.py`](custom_components/evbalance/balancer.py) (keine Abhängigkeit von
Home Assistant).

## ⚠️ Sicherheit

Diese Software moduliert den Strom, **ersetzt aber nicht die elektrischen
Schutzeinrichtungen** der Anlage. Setze immer eine angemessene Sicherheitsmarge
und prüfe, wie sich deine Wallbox verhält, wenn sie 0 A erhält.

### ⚠️ Haftungsausschluss

Die Nutzung der Anwendung und das Einstellen ihrer Parameter **sollten
ausschließlich durch autorisierte und fachkundige Personen erfolgen**. Der Autor
übernimmt keinerlei Haftung für mögliche Schäden an Sachen und Personen, die
direkt oder indirekt aus der Nutzung dieser Software entstehen.

## Lizenz

Lizenziert unter der **GNU General Public License v3.0** — siehe [`LICENSE`](LICENSE).

Kurz gesagt:

- **Frei** zu nutzen, zu studieren, zu teilen und zu ändern.
- Jede verbreitete Kopie oder abgeleitetes Werk muss **Open Source unter der
  GPL-3.0** bleiben und den vollständigen zugehörigen Quellcode enthalten.
- Bereitgestellt **ohne jegliche Gewährleistung**.

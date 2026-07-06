<p align="center">
  <img src="brand/logo.png" alt="EV Balance" width="360">
</p>

<p align="center">
  <b>English</b> · <a href="README.it.md">Italiano</a> · <a href="README.de.md">Deutsch</a> · <a href="README.fr.md">Français</a>
</p>

# EV Balance — Energy load balancer for Home Assistant

[![Version](https://img.shields.io/github/v/tag/matteodallefeste/ha_evbalance?sort=semver&label=version)](https://github.com/matteodallefeste/ha_evbalance/tags)
[![HACS: Custom](https://img.shields.io/badge/HACS-Custom-orange)](https://github.com/custom-components/hacs)
[![Home Assistant: Integration](https://img.shields.io/badge/Home%20Assistant-Integration-blue)](https://www.home-assistant.io/)
[![hassfest](https://github.com/matteodallefeste/ha_evbalance/actions/workflows/hassfest.yml/badge.svg)](https://github.com/matteodallefeste/ha_evbalance/actions/workflows/hassfest.yml)
[![HACS validation](https://github.com/matteodallefeste/ha_evbalance/actions/workflows/validate.yml/badge.svg)](https://github.com/matteodallefeste/ha_evbalance/actions/workflows/validate.yml)
[![License](https://img.shields.io/badge/license-Proprietary-red)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/matteodallefeste/ha_evbalance)](https://github.com/matteodallefeste/ha_evbalance/commits)
[![Issues](https://img.shields.io/github/issues/matteodallefeste/ha_evbalance)](https://github.com/matteodallefeste/ha_evbalance/issues)
[![Stars](https://img.shields.io/github/stars/matteodallefeste/ha_evbalance?style=flat)](https://github.com/matteodallefeste/ha_evbalance/stargazers)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=matteodallefeste&repository=ha_evbalance&category=integration)

Custom integration (installable via **HACS**) that prevents the meter from
tripping on overload by modulating the EV Charger current based on household
consumption, and that tracks energy by **time bands** (Italian ARERA F1/F2/F3)
with daily and monthly resets.

## How it works

On every cycle (default every 3 s) the integration:

1. reads the **instantaneous power** of the EV Charger and of the configured sources;
2. computes the available budget:
   `budget = meter_limit − safety_margin − source_consumption`;
3. converts the budget into Amperes (based on voltage and number of phases) and
   writes it to the EV Charger's **number entity**;
4. if non-EV-Charger consumption exceeds the limit, it **pauses** the EV Charger.

### Hysteresis (anti-flapping)

- **Reduction / pause → immediate** (safety).
- **Increase → allowed only after `hold_seconds`** (default 300 s = 5 min)
  since the last change. This keeps the value from being changed constantly.

## Installation (HACS)

1. HACS → *Integrations* → menu ⋮ → **Custom repositories**.
2. Add the URL of this repository, category **Integration**.
3. Install **EV Balance** and restart Home Assistant.
4. *Settings → Devices & Services → Add Integration → EV Balance*.

> Alternatively, copy the `custom_components/evbalance/` folder into your
> `config/custom_components/` folder and restart.

## Configuration

**Initial setup (structural, set at first configuration):**

| Parameter | Default | What it's for |
|---|---|---|
| Name | EV Balance | Name of the integration instance |
| EV Charger power sensor | — | `sensor.*` (device_class power) in W/kW with the current EV Charger power |
| EV Charger current number | — | `number.*` entity the balancer writes the max Amperes to |
| Consumption sources | (none) | Power sensors of the rest of the house, subtracted from the budget (multi-select) |
| Source includes EV Charger | off | ON if a selected source already measures the EV Charger too, so it isn't counted twice |
| Meter maximum limit | 3300 W | Power above which the meter trips; the ceiling the balancer stays under |
| Voltage | 230 V | Line voltage, used to convert Watts ↔ Amperes |
| Supply / Phases | Single-phase | Single-phase (1) or three-phase (3), affects the W↔A conversion |
| Min current | 6 A | Below this the EV Charger is paused instead of throttled |
| Max current | 16 A | Highest current that can be written to the EV Charger |

**Options (hot-editable, no restart):**

| Parameter | Default | What it's for |
|---|---|---|
| Consumption sources | (none) | Same as above, editable later |
| Source includes EV Charger | off | Same as above, editable later |
| Safety margin | 200 W | Reserve kept free under the meter limit, absorbs spikes |
| Pause current | 0 A | Value written to "stop" charging when paused (some chargers need a value > 0) |
| Allowed current steps | (empty) | Comma-separated list of allowed Amperes (e.g. `6, 8, 10, 16`); empty = every integer from min to max |
| Hold seconds | 300 s | Minimum wait before the current can be raised again (anti-flapping) |
| Update interval | 3 s | How often power is read and current applied (minimum 3 s) |
| Tariff preset | ARERA F1/F2/F3 | Time-band set for energy tracking (ARERA or single flat band) |
| Show panel | on | Show/hide the EV Balance panel in the sidebar |

## Created entities

- **Switch** *Balancing active* — when OFF it reads but does not touch the EV Charger.
- **Binary sensor** *Charging paused* — with the `reasons` attribute (explains the decision).
- **Number** *Meter maximum limit*, *Safety margin* — live tuning.
- **Sensor** total/sources/EV-Charger power, *Allowed current*, *Active band*.
- **Energy sensor** for each source × band × period (daily + monthly), in kWh,
  `state_class: total_increasing` → compatible with the Energy dashboard.

## Sidebar panel

The integration registers an optional **sidebar panel** (custom element, no
build step) showing live power, allowed current, meter limit and the per-band
energy of the last months. It reads everything from existing entities and the
Recorder long-term statistics — no extra storage. Toggle it from the options
(*Show panel*).

## ARERA time bands

| Band | When |
|---|---|
| **F1** | Mon–Fri 08:00–19:00 |
| **F2** | Mon–Fri 07:00–08:00 and 19:00–23:00; Sat 07:00–23:00 |
| **F3** | Mon–Fri 23:00–07:00; Sat 23:00–07:00; Sundays and holidays |

The bands are data-driven ([`energy.py`](custom_components/evbalance/energy.py)):
adding a custom preset means adding rules, without touching the logic.

## Development

The balancing logic is isolated and testable in
[`balancer.py`](custom_components/evbalance/balancer.py) (no dependency on
Home Assistant).

## ⚠️ Safety

This software modulates the current but **does not replace the electrical
protections** of your installation. Always set an adequate safety margin and
verify how your EV Charger behaves when it receives 0 A.

### ⚠️ Disclaimer

Using the application and setting its parameters **should be done exclusively
by authorized and expert people**. The author declines any responsibility for
possible damage to property and people, directly or indirectly, arising from
the use of this software.

## License

Source-available under the **PolyForm Noncommercial License 1.0.0** with
additional terms — see [`LICENSE`](LICENSE).

In short:

- **Free** for any non-commercial / non-professional use.
- Copies and derivative works may be redistributed **only within an open source
  project** (OSI-approved license, full public source).
- All rights remain the exclusive property of Matteo Dalle Feste, who may
  relicense future versions or close the software at any time.
- **Commercial or professional use requires a separate written agreement** —
  contact matteo@dallefeste.com.

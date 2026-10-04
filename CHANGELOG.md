# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses calendar versioning (`YY.M.patch`). The version in
`custom_components/evbalance/manifest.json` must always match the Git tag and
the GitHub Release — HACS shows the GitHub Release notes as the changelog to
users.

## [Unreleased]

## [26.9.5] - 2026-10-04

### Fixed
- OCPP mode: charging could fail to start when the car was plugged in, and only
  began after restarting Home Assistant. The integration sent the current limit
  and then only repeated it when the value changed, but many chargers drop their
  charging profile at the moment the car is plugged in — so the new session had
  no limit and nothing re-sent it until a restart reset the "already sent"
  bookkeeping. The limit is now re-asserted once whenever a car is plugged in.
- Completes the 26.9.3 fix for a charger that stays suspended after being asked
  to resume: that check only ran when the charger sent current measurements, and
  a charger that is not delivering sends none, so it never triggered in exactly
  the case it was meant for. It now also works from the connector status alone,
  is re-evaluated on every status change and every update cycle instead of only
  when `MeterValues` arrive, and waits a few seconds after a new limit before
  judging, so a normal pause-to-charge transition does not raise a false warning.
- Connector status changes are now logged at info level (`Preparing -> Charging`
  and so on), which makes this kind of problem diagnosable from the log.

## [26.9.4] - 2026-09-26

### Added
- **Charge now** (OCPP control mode only): a new switch that charges outside
  the chosen time-of-use bands, for when the car has to be ready before the
  cheap hours come round. It overrides the bands only — the balancer still
  caps the current to what the house budget allows, so the meter never trips —
  and it switches itself back off at the end of the session, after which
  charging follows the schedule again. The charger reports the end of a
  session (cable unplugged, transaction closed, or the car itself no longer
  drawing power), which is why the switch exists only in OCPP mode: in entity
  mode there is no reliable "charging finished" signal to expire it. The
  charger card in the panel gets a matching button, next to stop/resume; it
  is disabled while charging is stopped by hand, since the manual stop wins.
- **Lovelace card** `custom:evbalance-card`: the charger card, for your own
  dashboards instead of the sidebar panel — connector state, requested versus
  offered current, session energy, state of charge, current per phase, and the
  stop/resume and *Charge now* buttons. The integration registers the frontend
  resource itself, so the card appears in the card picker with nothing to add
  by hand, and it works with the sidebar panel hidden. Its content and logic
  come from a module shared with the panel (`evbalance-wallbox.js`), so the two
  cannot drift apart, while the colours follow the Home Assistant theme. OCPP
  control mode only, like the switch.

## [26.9.3] - 2026-09-18

### Fixed
- OCPP mode: charging could stay stuck after switching from a disallowed to
  an allowed time-of-use band (e.g. ARERA's F2 → F3). `check_limit_applied`
  only flagged a mismatch when the charger drew *more* current than
  requested, never when it stayed at 0 A after being asked to resume — so a
  charger stuck in `SuspendedEVSE` looked "fine" and the retry
  (`SetChargingProfile` every 30s) never fired. The check now also looks at
  the connector status: `SuspendedEVSE` with a positive requested limit is
  treated as unconfirmed and retried, while `SuspendedEV` (the vehicle's own
  choice not to draw power) is still treated as normal.

## [26.9.2] - 2026-09-09

### Fixed
- `hassfest` rejected the OCPP setup step: its `<address>:<port>` placeholders
  matched Home Assistant's "no HTML in translations" rule. They now use square
  brackets. Added `tools/check_translations.py`, which applies the same regex
  Home Assistant uses plus a key-parity check across languages, and runs in CI
  so this is caught locally instead of after a release.
- Completed the German, French and Spanish translations of the charging
  pause/resume switch, missing since 26.7.14 — found by the new key-parity
  check.

## [26.9.1] - 2026-09-09

### Added
- **OCPP 1.6J control mode**: EV Balance can now run its own **CSMS** and talk
  to the charger directly over a websocket, with no vendor integration and no
  cloud service in between. The mode is chosen when adding the integration
  (*Directly over OCPP 1.6*) and is served either on a dedicated port (default
  9000) or on Home Assistant's own port under `/api/evbalance/ocpp/<id>`, which
  avoids publishing an extra port when HA runs in a container.
- OCPP telemetry replaces the external power sensor when available: active
  power, per-phase current and voltage, energy register and state of charge,
  taken straight from the charger. The power sensor remains as a fallback.
- New entities in OCPP mode: **Charger status (OCPP)** — the real connector
  state (`Charging`, `SuspendedEV`, `SuspendedEVSE`, `Faulted`, …) with vendor,
  model, firmware and the requested/offered current as attributes — **Session
  energy**, from the charger's own meter registers, and **Charger connected
  (OCPP)**.
- **Charger card in the panel's Live tab** (OCPP mode): link state, real
  connector state, requested versus offered current, session energy, state of
  charge, current per phase, and the charger's vendor/model/firmware. It warns
  when the charger is not applying the requested limit and, while no charger is
  connected, shows the exact websocket address to configure — built from the
  host the panel is being viewed on. The Settings tab is mode-aware too: it
  shows the OCPP fields (port, Charge Point ID, password, connector, telemetry
  interval, Home Assistant port) instead of the charger entities and the pause
  current.
- The **control mode is switchable from the panel's Settings tab**: a selector
  between *Home Assistant entities* and *Direct OCPP 1.6* swaps the charger
  fields in place and reloads the integration on save, so an existing setup can
  move to OCPP without being removed and re-added (which would lose its energy
  history). The mode is validated server-side.
- A **step-by-step guide** in the OCPP settings covering what to enter on any
  OCPP 1.6J charger: protocol version, CSMS URL (including the `ws://` prefix
  ambiguity between firmwares), Charge Point ID matching, password, and the
  single-backend caveat.
- **Manual charging stop**: a new *Charging allowed* switch pauses the charger
  regardless of the available budget, with a button in the panel's charger card
  to stop and resume. In OCPP mode it is the usual 0 A profile, so the session
  stays open and there is no need to unplug. The choice survives a restart, and
  the state badge says *Stopped manually* rather than a generic "paused".
- **Charge only in chosen tariff bands**: pick the bands charging is allowed in
  (e.g. F1 and F3) from the panel's Settings or the integration options; outside
  them the charger is paused. Selecting none keeps the previous behaviour of
  charging in every band. The band list follows the active tariff scheme, and
  the state badge shows *Out of band* so the reason for a pause is never
  ambiguous.
- The websocket address shown to configure the charger is now built from **Home
  Assistant's own LAN address**, supplied by the backend, instead of the host
  the panel happens to be opened with. Viewing the panel from outside the home
  used to suggest a public hostname the charger cannot reach. The address also
  updates live as the port, Charge Point ID or Home Assistant-port flag are
  edited, so it always matches what is about to be saved.
- `tools/check_panel.mjs`: browser-free checks for the panel — the OCPP card,
  the per-mode settings fields, the form reader and translation-key parity
  across all five languages. Run with `node tools/check_panel.mjs`.
- `tools/ocpp_probe.py`: a standalone, dependency-free OCPP probe to work out
  why a charger does not connect. `sniff` prints the raw HTTP upgrade request
  (path, offered subprotocols, basic auth); `serve` runs a full OCPP dialogue
  with an interactive console for `SetChargingProfile`, `TriggerMessage` and
  the rest.

### Fixed
- The panel's cache-busting token is now derived from the **contents** of the
  panel's JS modules instead of a hand-maintained version number, so a browser
  can no longer keep serving a stale panel after an update.
- The panel no longer crashes with *"the name evbalance-panel has already been
  used with this registry"* when the module is loaded twice in the same page —
  which the content-based token made possible, since a reload changes the module
  URL. The custom element is now only defined if it is not registered already.
- **The charger could keep drawing its minimum when asked to pause.** In OCPP
  mode this is fixed at the root: any target below the configured minimum is
  sent as a **0 A charging profile**, which suspends delivery (`SuspendedEVSE`)
  while keeping the session open — no pause switch is needed, and a below-minimum
  current is never requested. The applied limit is then verified against what
  the charger reports offering, and the profile is re-sent when the two
  disagree; the profile is also re-applied when a transaction starts and after a
  reconnection, since some chargers drop their limits in both cases.
- With entity control, a current the charger refuses to accept (typically a
  value below its 6 A minimum) is now **detected and logged** instead of being
  silently reported as applied: the balancer no longer claims to be paused while
  the charger keeps charging.

### Changed
- Actuation moved out of the coordinator into a dedicated `actuator` layer, so
  the balancing logic no longer knows how the charger is driven. Behaviour of
  the existing entity-based mode is unchanged.
- OCPP settings now live only in the config entry **options**, the single place
  the options flow and the panel already edited. They used to be written to the
  entry data as well, leaving two copies of the same value that could drift
  apart. Existing entries are normalised on startup, keeping the options value.
- CI gained a `pyflakes` job that fails on undefined or unused names — the check
  that would have caught the missing imports above.

## [26.7.15] - 2026-07-13

### Added
- The **charging pause/resume switch** and its **inverted** flag can now be set
  directly from the panel's **Settings** tab, next to the EV charger entities —
  previously they were only available in the integration options dialog.

### Changed
- Clearer label for the EV charger current field: it is now "EV Charger charging
  current" (was "EV Charger current number") in the panel and its translations.

## [26.7.14] - 2026-07-13

### Added
- Optional **charging pause/resume switch** for the EV charger (config option
  `ev_charger_switch_entity`, a `switch` or `input_boolean`). When the available
  current drops below the configured minimum, charging is now **paused** through
  this switch instead of writing a below-minimum current to the number entity;
  it resumes automatically once the available current is back at or above the
  minimum. An **inverted** flag (`ev_charger_switch_invert`) supports wallboxes
  whose switch is ON = paused. The option is available both in the initial setup
  and in the integration options, so existing installations can add it without
  reconfiguring. When no switch is set, the previous pause-current behavior is
  kept.

### Fixed
- The balancer could trip the main meter/breaker: lowering the current below the
  wallbox minimum (e.g. 6 A) does not actually stop charging, so the charger
  kept drawing its minimum power on top of the other loads and pushed the total
  over the contracted limit. Pausing via the new charging switch prevents this.

## [26.7.13] - 2026-07-10

### Added
- The panel is now organized into three tabs — **Live**, **Statistics** and
  **Settings** — instead of a single scrolling page.
- Statistics are drawn with Apache ECharts, bundled locally in the integration
  (`www/echarts.esm.min.js`, Apache-2.0) so there is **no external/CDN
  dependency** at runtime. ECharts is imported lazily the first time the
  Statistics tab is opened, and the charts follow the Home Assistant theme
  colors. If the module fails to load, the panel falls back to CSS bars.
- New Statistics metrics:
  - **KPI tiles**: total energy for the period, share in the cheapest tariff
    band, and (when EV statistics exist) EV energy and EV share of the total.
  - **Stacked-by-band chart** over time — hourly for *Today*, daily for the
    selected *Month*.
  - **EV vs rest-of-home split** — a 100% bar showing how much of the period's
    energy went to charging. The websocket `evbalance/panel` command now also
    returns `band_stats_ev` (the per-band EV Charger statistic ids).
  - **12-month trend** — stacked-by-band monthly totals, aggregated from the
    daily statistics.
- **Per-band €/kWh prices** for bill and EV-charging cost estimates. Set a price
  per tariff band (and the currency symbol) in **Settings**; the Statistics tab
  then shows **estimated cost** for the period and **EV charging cost** as KPI
  tiles. Prices are stored in the config entry options (new `tariff_prices` map
  and `currency`), apply to both presets and custom schemes, and are returned by
  the `evbalance/panel` command as `band_prices`/`currency`. Cost tiles only
  appear when at least one band has a price set.
- **Average-price calculator** in the prices section: enter the bill amount and
  the kWh to get the average €/kWh, then apply it as a flat price to every band.
  The kWh can be typed from the bill or read from the tracked consumption for a
  chosen month (note: the tracked total may cover only the configured sources,
  not the whole-house meter, so the manual figure is more accurate).
- Added the `statistics`, `statTotal`, `statCheapest`, `statEv`, `statEvShare`,
  `statHouse`, `statTrend`, `statCost`, `statEvCost`, `pPrices`, `pCurrency`,
  `cTitle`, `cAmount`, `cPeriod`, `cRead`, `cAvg` and `cApply` translations for
  all five languages.

### Fixed
- Panel statistics: switching between months (e.g. June → July) showed the same
  kWh for every band. The month view now aggregates the native daily statistics
  (`change` per day) over the selected month and filters returned rows to the
  requested `[start, end)` window, instead of relying on the monthly `change`
  aggregation which could return out-of-window buckets (making every month look
  identical). The current month stops at "now".

## [26.7.12] - 2026-07-10

### Fixed
- The sidebar panel now shows a hamburger menu button that reopens the Home
  Assistant navigation. On narrow/mobile view (where the sidebar is hidden)
  there was previously no way back to the main menu. The button fires the core
  `hass-toggle-menu` event and appears only when needed (narrow view or a
  hidden sidebar). Added the `menu` translation for all five languages.

## [26.7.9] - 2026-07-03

### Changed
- Reworked the brand artwork (icon and logo redrawn) and refreshed the bundled
  images in `custom_components/evbalance/brand/`. The logo is now 512×152
  (`@2x` 1024×304); the icon stays 256×256 (`@2x` 512×512). Source assets moved
  to the top-level `brand/` folder.

## [26.7.8] - 2026-07-03

### Added
- **Bundled brand images** in `custom_components/evbalance/brand/` (`icon.png`,
  `icon@2x.png`, `logo.png`, `logo@2x.png`). As of Home Assistant 2026.3 a custom
  integration can ship its own brand images locally and they take priority over
  the brands CDN, so the icon now shows in the Home Assistant UI without waiting
  for a `home-assistant/brands` submission. See the
  [brands proxy API announcement](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/).

## [26.7.7] - 2026-07-03

First fully working release.

### Fixed
- Corrected panel and integration translation strings across all supported
  languages (German, English, Spanish, French, Italian) in both the backend
  (`strings.json`, `translations/*.json`) and the frontend panel
  (`evbalance-translations.js`), so labels and options render correctly in every
  locale.

## [26.7.3] - 2026-07-02

### Added
- **Optional sidebar panel** (`/evbalance`). It shows, in real time, the house
  consumption, the EV Charger consumption, the total, the granted max charge
  current, the charge state (charging / paused / idle) and the power limit.
- **Energy-by-tariff-band chart** in the panel, with a *Today* view (hourly
  granularity) and a *Month* view navigable backwards month by month. Data
  comes from Home Assistant's native long-term statistics
  (`recorder/statistics_during_period`) — no custom storage, kept indefinitely.
- New option **"Show panel in the sidebar"** (on by default) in the integration
  options.

### Fixed
- Integration failed to load with `ModuleNotFoundError: No module named
  'homeassistant.helpers.device_info'`. `DeviceInfo` is now imported from
  `homeassistant.helpers.entity`.

### Changed
- Entity icons are now declared in `icons.json` (single source of truth) instead
  of being hard-coded on each entity.
- Brand assets moved to `brands/custom_integrations/evbalance/` to match the
  structure required by the `home-assistant/brands` repository.

## [26.7.2] - 2026-07-02

### Added
- Initial release: energy load balancer for a EV Charger (HACS custom
  integration). Reads the meter/house power and the EV Charger power, and modulates
  the EV Charger current to stay under the meter limit, with time-of-use (ARERA)
  tariff bands and per-band energy sensors.

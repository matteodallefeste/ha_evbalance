# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses calendar versioning (`YY.M.patch`). The version in
`custom_components/evbalance/manifest.json` must always match the Git tag and
the GitHub Release — HACS shows the GitHub Release notes as the changelog to
users.

## [Unreleased]

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

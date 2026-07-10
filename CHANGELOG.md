# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses calendar versioning (`YY.M.patch`). The version in
`custom_components/evbalance/manifest.json` must always match the Git tag and
the GitHub Release — HACS shows the GitHub Release notes as the changelog to
users.

## [Unreleased]

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

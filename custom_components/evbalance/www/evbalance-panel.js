/**
 * EV Balance sidebar panel.
 *
 * Custom element dependency-free (nessuno step di build). Combina:
 *   - i valori live letti direttamente da hass.states (consumo casa/EV Charger,
 *     corrente concessa, stato carica, limite potenza), aggiornati a ogni
 *     assegnazione della property `hass`;
 *   - l'energia per fascia oraria letta dalle long-term statistics native via
 *     il comando core `recorder/statistics_during_period` (oggi a granularità
 *     oraria, i mesi passati a granularità mensile), navigabile all'indietro.
 *
 * I metadati (quali entità e quali statistic_id usare) arrivano dal comando
 * websocket `evbalance/panel`, così il frontend non deve indovinare gli id.
 */

// Le stringhe di traduzione vivono in un modulo separato, caricato in modo
// asincrono all'avvio del pannello (vedi evbalance-translations.js).
const TRANSLATIONS_MODULE = "./evbalance-translations.js";

// Colori per fascia (ARERA): F1 picco, F2 intermedia, F3 fuori picco.
const BAND_COLORS = {
  F1: "#ef4444",
  F2: "#f59e0b",
  F3: "#22c78b",
};
const BAND_FALLBACK = "#29c7b0";

const CHARGING_THRESHOLD_W = 100;

class EVBalancePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._meta = null;
    this._config = null; // snapshot configurazione per il form Impostazioni
    this._presets = []; // preset tariffa disponibili (da config/get)
    this._customScheme = null; // schema custom in editing (editor fasce)
    this._tr = {}; // stringhe di traduzione, caricate in _init()
    this._canEdit = false;
    this._initStarted = false;
    this._mode = "day"; // "day" | "month"
    this._monthOffset = 0; // 0 = mese corrente, 1 = precedente, ...
    this._statsLoading = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initStarted) {
      this._initStarted = true;
      this._init();
    } else if (this._meta) {
      this._updateLive();
    }
  }

  get _t() {
    const lang = (this._hass && this._hass.language) || "en";
    return this._tr[lang] || this._tr.en || {};
  }

  async _loadTranslations() {
    // Risolve il modulo relativo a questo file e propaga la stessa versione di
    // cache-busting (?v=) con cui il pannello è stato caricato, così bump di
    // PANEL_JS_VERSION invalidano anche le traduzioni senza doppia manutenzione.
    const url = new URL(TRANSLATIONS_MODULE, import.meta.url);
    const v = new URL(import.meta.url).searchParams.get("v");
    if (v) url.searchParams.set("v", v);
    const mod = await import(url.href);
    this._tr = mod.TR || {};
  }

  async _init() {
    try {
      await this._loadTranslations();
    } catch (err) {
      this._tr = {};
    }
    this._renderShell(this._t.loading || "…");
    try {
      this._meta = await this._hass.callWS({ type: "evbalance/panel" });
    } catch (err) {
      this._renderShell(this._t.error);
      return;
    }
    await this._loadConfig();
    this._render();
    this._updateLive();
    this._loadStats();
  }

  async _loadConfig() {
    try {
      const res = await this._hass.callWS({ type: "evbalance/config/get" });
      this._config = res.config;
      this._canEdit = !!res.can_edit;
      this._presets = res.presets || [];
      this._customScheme = null;
    } catch (err) {
      this._config = null;
    }
  }

  // --- Helpers stato ----------------------------------------------------

  _stateOf(key) {
    const id = this._meta && this._meta.entities && this._meta.entities[key];
    if (!id || !this._hass.states[id]) return null;
    return this._hass.states[id];
  }

  _numState(key) {
    const st = this._stateOf(key);
    if (!st) return null;
    const n = Number(st.state);
    return Number.isFinite(n) ? n : null;
  }

  _fmtPower(w) {
    if (w == null) return "—";
    const t = this._locale;
    if (Math.abs(w) >= 1000) {
      return (
        new Intl.NumberFormat(t, { maximumFractionDigits: 2 }).format(w / 1000) +
        " kW"
      );
    }
    return new Intl.NumberFormat(t, { maximumFractionDigits: 0 }).format(w) + " W";
  }

  _fmtEnergy(kwh) {
    return (
      new Intl.NumberFormat(this._locale, {
        maximumFractionDigits: 2,
      }).format(kwh || 0) + " kWh"
    );
  }

  get _locale() {
    return (this._hass && this._hass.language) || "en";
  }

  // --- Live tiles -------------------------------------------------------

  _updateLive() {
    const root = this.shadowRoot;
    if (!root) return;
    const set = (id, txt) => {
      const el = root.getElementById(id);
      if (el) el.textContent = txt;
    };

    set("v-house", this._fmtPower(this._numState("sources_power")));
    set("v-evCharger", this._fmtPower(this._numState("ev_charger_power")));
    set("v-total", this._fmtPower(this._numState("total_power")));

    const cur = this._numState("target_current");
    set("v-current", cur == null ? "—" : `${cur} A`);

    // Limite potenza: valore corrente dalla config (esposto nei metadati live).
    set("v-limit", this._fmtPower(this._meta.max_power_w));

    // Fascia attiva
    const bandSt = this._stateOf("active_band");
    set("v-band", bandSt ? bandSt.state : "—");

    // Stato carica: derivato da potenza EV Charger e blocco.
    const wb = this._numState("ev_charger_power");
    const blockedSt = this._stateOf("charging_blocked");
    const blocked = blockedSt && blockedSt.state === "on";
    const badge = root.getElementById("v-charge");
    if (badge) {
      let label = this._t.idle;
      let cls = "badge idle";
      if (blocked) {
        label = this._t.paused;
        cls = "badge paused";
      } else if (wb != null && wb > CHARGING_THRESHOLD_W) {
        label = this._t.charging;
        cls = "badge charging";
      }
      badge.textContent = label;
      badge.className = cls;
    }

    // Bilanciamento attivo/disattivo (info + sync del toggle in Impostazioni).
    const balSt = this._stateOf("balancing");
    const balOn = balSt ? balSt.state === "on" : null;
    const balBadge = root.getElementById("v-balancing");
    if (balBadge) {
      if (balOn == null) {
        balBadge.textContent = "—";
        balBadge.className = "badge idle";
      } else {
        balBadge.textContent = balOn ? this._t.statusActive : this._t.statusOff;
        balBadge.className = "badge " + (balOn ? "charging" : "idle");
      }
    }
    const balCtl = root.getElementById("ctl-balancing");
    if (balCtl && balOn != null && document.activeElement !== balCtl) {
      balCtl.checked = balOn;
    }
  }

  // --- Statistiche energia per fascia ----------------------------------

  _monthRange(offset) {
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth() - offset, 1, 0, 0, 0);
    const end = new Date(now.getFullYear(), now.getMonth() - offset + 1, 1, 0, 0, 0);
    return { start, end };
  }

  async _loadStats() {
    if (!this._meta || this._statsLoading) return;
    const stats = this._meta.band_stats || {};
    const ids = Object.values(stats).filter(Boolean);
    const chart = this.shadowRoot.getElementById("chart");
    if (!chart) return;

    if (ids.length === 0) {
      chart.innerHTML = `<div class="empty">${this._t.noData}</div>`;
      this._updateNav();
      return;
    }

    this._statsLoading = true;
    let start;
    let end;
    let period;
    if (this._mode === "day") {
      const now = new Date();
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0);
      end = null; // fino a ora
      period = "hour";
    } else {
      const r = this._monthRange(this._monthOffset);
      start = r.start;
      end = r.end;
      period = "month";
    }

    let result = {};
    try {
      const msg = {
        type: "recorder/statistics_during_period",
        statistic_ids: ids,
        start_time: start.toISOString(),
        period,
        types: ["change", "sum"],
      };
      if (end) msg.end_time = end.toISOString();
      result = await this._hass.callWS(msg);
    } catch (err) {
      result = {};
    }
    this._statsLoading = false;

    // Somma il "change" (delta della somma nel periodo) per ogni fascia.
    const totals = {};
    for (const band of this._meta.bands) {
      const sid = stats[band];
      const rows = (sid && result[sid]) || [];
      let sum = 0;
      let hasChange = false;
      for (const row of rows) {
        if (row.change != null) {
          sum += Number(row.change) || 0;
          hasChange = true;
        }
      }
      if (!hasChange && rows.length >= 2) {
        // Fallback: delta della somma cumulata agli estremi.
        const first = Number(rows[0].sum);
        const last = Number(rows[rows.length - 1].sum);
        if (Number.isFinite(first) && Number.isFinite(last)) sum = last - first;
      }
      totals[band] = Math.max(0, sum);
    }

    this._renderChart(totals);
    this._updateNav();
  }

  _renderChart(totals) {
    const chart = this.shadowRoot.getElementById("chart");
    if (!chart) return;
    const bands = this._meta.bands;
    const max = Math.max(1e-6, ...bands.map((b) => totals[b] || 0));
    const anyData = bands.some((b) => (totals[b] || 0) > 0);

    if (!anyData) {
      chart.innerHTML = `<div class="empty">${this._t.noData}</div>`;
      return;
    }

    const rows = bands
      .map((b) => {
        const val = totals[b] || 0;
        const pct = Math.round((val / max) * 100);
        const color = this._bandColor(b);
        return `
          <div class="bar-row">
            <span class="bar-label" style="color:${color}">${b}</span>
            <div class="bar-track">
              <div class="bar-fill" style="width:${pct}%;background:${color}"></div>
            </div>
            <span class="bar-val">${this._fmtEnergy(val)}</span>
          </div>`;
      })
      .join("");
    chart.innerHTML = rows;
  }

  _updateNav() {
    const root = this.shadowRoot;
    const label = root.getElementById("period-label");
    const prev = root.getElementById("nav-prev");
    const next = root.getElementById("nav-next");
    if (!label) return;

    if (this._mode === "day") {
      label.textContent = new Date().toLocaleDateString(this._locale, {
        weekday: "long",
        day: "numeric",
        month: "long",
      });
      if (prev) prev.style.visibility = "hidden";
      if (next) next.style.visibility = "hidden";
    } else {
      const { start } = this._monthRange(this._monthOffset);
      label.textContent = start.toLocaleDateString(this._locale, {
        month: "long",
        year: "numeric",
      });
      if (prev) prev.style.visibility = "visible";
      // Non si va oltre il mese corrente.
      if (next) next.style.visibility = this._monthOffset > 0 ? "visible" : "hidden";
    }
  }

  // --- Rendering DOM ----------------------------------------------------

  _renderShell(message) {
    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <div class="wrap"><div class="msg">${message}</div></div>`;
  }

  _render() {
    const t = this._t;
    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <div class="wrap">
        <h1>${this._meta.title || t.title}</h1>

        <section class="card">
          <h2>${t.live}</h2>
          <div class="tiles">
            <div class="tile"><span class="k">${t.house}</span><span class="val" id="v-house">—</span></div>
            <div class="tile"><span class="k">${t.evCharger}</span><span class="val" id="v-evCharger">—</span></div>
            <div class="tile"><span class="k">${t.total}</span><span class="val" id="v-total">—</span></div>
            <div class="tile"><span class="k">${t.maxCurrent}</span><span class="val" id="v-current">—</span></div>
            <div class="tile"><span class="k">${t.powerLimit}</span><span class="val" id="v-limit">—</span></div>
            <div class="tile">
              <span class="k">${t.chargeState}</span>
              <span class="badge idle" id="v-charge">—</span>
              <span class="chip" id="v-band">—</span>
            </div>
            <div class="tile">
              <span class="k">${t.balancing}</span>
              <span class="badge idle" id="v-balancing">—</span>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="chart-head">
            <h2>${t.energyByBand}</h2>
            <div class="modes">
              <button class="mode" data-mode="day">${t.today}</button>
              <button class="mode" data-mode="month">${t.month}</button>
            </div>
          </div>
          <div class="nav">
            <button id="nav-prev" class="navbtn">◀</button>
            <span id="period-label"></span>
            <button id="nav-next" class="navbtn">▶</button>
          </div>
          <div id="chart" class="chart"><div class="empty">${t.loading}</div></div>
        </section>

        ${this._settingsSection()}
      </div>`;

    // Handler modalità e navigazione.
    this.shadowRoot.querySelectorAll(".mode").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._mode = btn.dataset.mode;
        this._monthOffset = 0;
        this._syncModeButtons();
        this._loadStats();
      });
    });
    this.shadowRoot.getElementById("nav-prev").addEventListener("click", () => {
      this._monthOffset += 1;
      this._loadStats();
    });
    this.shadowRoot.getElementById("nav-next").addEventListener("click", () => {
      if (this._monthOffset > 0) {
        this._monthOffset -= 1;
        this._loadStats();
      }
    });
    this._syncModeButtons();
    this._updateNav();
    this._wireSettings();
  }

  // --- Sezione Impostazioni --------------------------------------------

  _powerSensors() {
    const st = this._hass.states;
    const own = this._ownEntityIds();
    return Object.keys(st)
      .filter(
        (id) =>
          id.startsWith("sensor.") &&
          st[id].attributes.device_class === "power" &&
          !own.has(id)
      )
      .sort();
  }

  // entity_id delle entità prodotte da EV Balance stessa: vanno escluse dalle
  // sorgenti di consumo, altrimenti si selezionerebbe l'output come input.
  _ownEntityIds() {
    const ent = (this._meta && this._meta.entities) || {};
    return new Set(Object.values(ent).filter(Boolean));
  }

  _numberEntities() {
    const st = this._hass.states;
    return Object.keys(st)
      .filter((id) => id.startsWith("number."))
      .sort();
  }

  _friendly(id) {
    const st = this._hass.states[id];
    return (st && st.attributes && st.attributes.friendly_name) || id;
  }

  _esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[c]);
  }

  _optionTags(list, selected) {
    return list
      .map(
        (id) =>
          `<option value="${id}" ${id === selected ? "selected" : ""}>${this._esc(
            this._friendly(id)
          )}</option>`
      )
      .join("");
  }

  _fieldNum(key, label, step) {
    return `<label class="field"><span>${label}</span>
      <input id="cfg-${key}" type="number" step="${step || 1}" value="${this._config[key]}"></label>`;
  }

  _fieldEntity(key, label, list) {
    const sel = this._config[key] || "";
    const ids = Array.from(new Set([...list, sel].filter(Boolean)));
    const empty = `<option value="" ${sel ? "" : "selected"}>—</option>`;
    return `<label class="field"><span>${label}</span>
      <select id="cfg-${key}">${empty}${this._optionTags(ids, sel)}</select></label>`;
  }

  _fieldSources(label) {
    const list = this._powerSensors();
    const selected = this._config.sources || [];
    const ids = Array.from(new Set([...list, ...selected])).sort();
    if (ids.length === 0) {
      return `<div class="field wide"><span>${label}</span>
        <div class="hint">${this._t.noSources}</div></div>`;
    }
    const sel = new Set(selected);
    const items = ids
      .map(
        (id) =>
          `<label class="cb-row"><input type="checkbox" class="src-cb" value="${id}" ${
            sel.has(id) ? "checked" : ""
          }><span>${this._esc(this._friendly(id))}</span></label>`
      )
      .join("");
    return `<div class="field wide"><span>${label}</span>
      <div class="cb-list">${items}</div></div>`;
  }

  _balancingControl() {
    const id = this._meta && this._meta.entities && this._meta.entities.balancing;
    if (!id) return "";
    const st = this._hass.states[id];
    const on = st ? st.state === "on" : false;
    return `<label class="ctl-row">
      <input type="checkbox" id="ctl-balancing" ${on ? "checked" : ""}>
      <span>${this._t.fBalancing}</span></label>`;
  }

  _settingsSection() {
    const t = this._t;
    if (!this._config) return "";
    const bal = this._balancingControl();
    if (!this._canEdit) {
      return `<details class="card settings"><summary>${t.settings}</summary>
        ${bal}<div class="hint">${t.noAdmin}</div></details>`;
    }

    const c = this._config;
    const phases = Number(c.phases) === 3 ? 3 : 1;

    const body = `
      <div class="form-grid">
        <label class="field wide"><span>${t.fName}</span>
          <input id="cfg-name" type="text" value="${this._esc(c.name || "")}"></label>

        ${this._fieldEntity("ev_charger_power_entity", t.fEvChargerPower, this._powerSensors())}
        ${this._fieldEntity("ev_charger_current_entity", t.fEvChargerCurrent, this._numberEntities())}

        ${this._fieldSources(t.fSources)}
        <label class="cb-row single wide">
          <input type="checkbox" id="cfg-sources_include_ev_charger" ${
            c.sources_include_ev_charger ? "checked" : ""
          }><span>${t.fSourcesInclude}</span></label>

        ${this._fieldNum("max_power_w", t.fMaxPower, 100)}
        ${this._fieldNum("voltage", t.fVoltage, 1)}
        <label class="field"><span>${t.fPhases}</span>
          <select id="cfg-phases">
            <option value="1" ${phases === 1 ? "selected" : ""}>${t.fPhase1}</option>
            <option value="3" ${phases === 3 ? "selected" : ""}>${t.fPhase3}</option>
          </select></label>

        ${this._fieldNum("min_current", t.fMinCurrent, 1)}
        ${this._fieldNum("max_current", t.fMaxCurrent, 1)}
        ${this._fieldNum("safety_margin_w", t.fSafetyMargin, 50)}
        ${this._fieldNum("pause_current", t.fPauseCurrent, 1)}
        ${this._fieldNum("hold_seconds", t.fHoldSeconds, 5)}
        ${this._fieldNum("update_interval", t.fUpdateInterval, 1)}

        ${this._tariffSection(c)}

        <label class="cb-row single wide">
          <input type="checkbox" id="cfg-show_panel" ${
            c.show_panel ? "checked" : ""
          }><span>${t.fShowPanel}</span></label>
      </div>
      <div class="save-row">
        <button id="save-btn" class="save-btn">${t.save}</button>
        <span id="save-status" class="save-status"></span>
      </div>`;

    return `<details class="card settings"><summary>${t.settings}</summary>${bal}${body}</details>`;
  }

  _wireSettings() {
    const btn = this.shadowRoot.getElementById("save-btn");
    if (btn) btn.addEventListener("click", () => this._save());
    const bal = this.shadowRoot.getElementById("ctl-balancing");
    if (bal) {
      bal.addEventListener("change", (e) => this._toggleBalancing(e.target.checked));
    }
    this._wireTariff();
  }

  async _toggleBalancing(on) {
    const id = this._meta && this._meta.entities && this._meta.entities.balancing;
    if (!id) return;
    try {
      await this._hass.callService("switch", on ? "turn_on" : "turn_off", {
        entity_id: id,
      });
    } catch (err) {
      // In caso di errore riallinea il toggle allo stato reale.
      this._updateLive();
    }
  }

  _readForm() {
    const root = this.shadowRoot;
    const g = (id) => root.getElementById(id);
    const tariffPreset = g("cfg-tariff_preset").value;
    return {
      name: g("cfg-name").value.trim(),
      ev_charger_power_entity: g("cfg-ev_charger_power_entity").value,
      ev_charger_current_entity: g("cfg-ev_charger_current_entity").value,
      max_power_w: Number(g("cfg-max_power_w").value),
      voltage: Number(g("cfg-voltage").value),
      phases: Number(g("cfg-phases").value),
      min_current: Number(g("cfg-min_current").value),
      max_current: Number(g("cfg-max_current").value),
      sources: Array.from(root.querySelectorAll(".src-cb")).filter((cb) => cb.checked).map((cb) => cb.value),
      sources_include_ev_charger: g("cfg-sources_include_ev_charger").checked,
      safety_margin_w: Number(g("cfg-safety_margin_w").value),
      pause_current: Number(g("cfg-pause_current").value),
      hold_seconds: Number(g("cfg-hold_seconds").value),
      update_interval: Number(g("cfg-update_interval").value),
      tariff_preset: tariffPreset,
      tariffs: tariffPreset === "custom" ? this._buildTariffPayload() : null,
      show_panel: g("cfg-show_panel").checked,
    };
  }

  async _save() {
    const t = this._t;
    const status = this.shadowRoot.getElementById("save-status");
    const btn = this.shadowRoot.getElementById("save-btn");
    const cfg = this._readForm();

    if (cfg.min_current >= cfg.max_current) {
      status.textContent = t.minGeMax;
      status.className = "save-status err";
      return;
    }

    btn.disabled = true;
    status.textContent = t.saving;
    status.className = "save-status";
    try {
      await this._hass.callWS({ type: "evbalance/config/set", config: cfg });
      this._config = cfg;
      status.textContent = t.saved;
      status.className = "save-status ok";
      // Il reload dell'integrazione può cambiare fasce/limiti: rinfresca i metadati.
      try {
        this._meta = await this._hass.callWS({ type: "evbalance/panel" });
        this._updateLive();
        this._loadStats();
      } catch (err) {
        /* non bloccante */
      }
    } catch (err) {
      status.textContent = (err && err.message) || t.saveError;
      status.className = "save-status err";
    } finally {
      btn.disabled = false;
    }
  }

  _syncModeButtons() {
    this.shadowRoot.querySelectorAll(".mode").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.mode === this._mode);
    });
  }

  // --- Editor fasce orarie (tariffa custom) ----------------------------

  _bandColor(id) {
    const meta = this._meta && this._meta.band_meta && this._meta.band_meta[id];
    if (meta && meta.color) return meta.color;
    if (BAND_COLORS[id]) return BAND_COLORS[id];
    const palette = ["#22c78b", "#f59e0b", "#ef4444", "#8b5cf6"];
    const rank = meta ? meta.rank : null;
    if (rank != null) return palette[Math.min(rank - 1, palette.length - 1)] || BAND_FALLBACK;
    return BAND_FALLBACK;
  }

  _presetById(id) {
    return (this._presets || []).find((p) => p.id === id) || null;
  }

  _blankScheme() {
    return {
      bands: [{ id: "F1", rank: 1, label: "", color: "#29c7b0" }],
      fallback: "F1",
      holidays_as: "",
      country: null,
      months: null,
      rules: [{ band: "F1", days: [0, 1, 2, 3, 4, 5, 6], start: "00:00", end: "24:00" }],
    };
  }

  // Appiattisce un preset (forma scheme_to_dict) nel modello a stagione singola
  // dell'editor. I nostri preset built-in sono non-stagionali (months=null).
  _seedFromPreset(p) {
    if (!p || !Array.isArray(p.bands)) return this._blankScheme();
    const seasons = Array.isArray(p.seasons) ? p.seasons : [];
    const rules = [];
    seasons.forEach((s) =>
      (s.rules || []).forEach((r) =>
        rules.push({
          band: r.band,
          days: (r.days || []).slice(),
          start: r.start,
          end: r.end,
        })
      )
    );
    const months = seasons.length && seasons[0].months ? seasons[0].months.slice() : null;
    return {
      bands: p.bands.map((b) => ({
        id: b.id,
        rank: b.rank,
        label: b.label || "",
        color: b.color || "",
      })),
      fallback: p.fallback || (p.bands[0] && p.bands[0].id) || "F1",
      holidays_as: p.holidays_as || "",
      country: p.country || null,
      months,
      rules: rules.length ? rules : this._blankScheme().rules,
    };
  }

  _ensureCustomScheme() {
    if (this._customScheme) return this._customScheme;
    const cfgT = this._config && this._config.tariffs;
    if (cfgT && Array.isArray(cfgT.bands)) {
      this._customScheme = this._seedFromPreset(cfgT);
    } else {
      const src = this._presetById(this._config && this._config.tariff_preset);
      this._customScheme = src ? this._seedFromPreset(src) : this._blankScheme();
    }
    return this._customScheme;
  }

  _bandOptions(selected) {
    return this._ensureCustomScheme()
      .bands.map(
        (b) =>
          `<option value="${this._esc(b.id)}" ${b.id === selected ? "selected" : ""}>${this._esc(
            b.id
          )}</option>`
      )
      .join("");
  }

  _tariffSection(c) {
    const t = this._t;
    const preset = c.tariff_preset || "default";
    const opts = (this._presets || [])
      .map(
        (p) =>
          `<option value="${this._esc(p.id)}" ${p.id === preset ? "selected" : ""}>${this._esc(
            p.label || p.id
          )}</option>`
      )
      .join("");
    const customOpt = `<option value="custom" ${
      preset === "custom" ? "selected" : ""
    }>${t.fTariffCustom}</option>`;
    return `
      <label class="field wide"><span>${t.fTariff}</span>
        <select id="cfg-tariff_preset">${opts}${customOpt}</select></label>
      <div id="tariff-editor" class="field wide">${
        preset === "custom" ? this._tariffEditorHtml() : ""
      }</div>`;
  }

  _tariffEditorHtml() {
    const t = this._t;
    const s = this._ensureCustomScheme();
    const dupOpts = (this._presets || [])
      .map((p) => `<option value="${this._esc(p.id)}">${this._esc(p.label || p.id)}</option>`)
      .join("");

    const bandRows = s.bands
      .map(
        (b, i) => `
      <div class="trow" data-band="${i}">
        <input class="b-id" value="${this._esc(b.id)}" placeholder="id">
        <input class="b-label" value="${this._esc(b.label || "")}" placeholder="${t.tColLabel}">
        <input class="b-rank" type="number" min="1" value="${b.rank || 1}" title="${t.tColRank}">
        <input class="b-color" type="color" value="${b.color || "#29c7b0"}">
        <button class="tdel" data-act="del-band" data-i="${i}" title="${t.tDel}">✕</button>
      </div>`
      )
      .join("");

    const ruleRows = s.rules
      .map((r, i) => {
        const days = this._dayCheckboxes(r.days || [], i);
        return `
      <div class="trow rule" data-rule="${i}">
        <select class="r-band">${this._bandOptions(r.band)}</select>
        <div class="days">${days}</div>
        <input class="r-start" value="${this._esc(r.start || "")}" placeholder="00:00">
        <input class="r-end" value="${this._esc(r.end || "")}" placeholder="24:00">
        <button class="tdel" data-act="del-rule" data-i="${i}" title="${t.tDel}">✕</button>
      </div>`;
      })
      .join("");

    return `
      <div class="tariff-box">
        <div class="dup-row">
          <span class="hint">${t.tDupHint}</span>
          <select id="tariff-dup">${dupOpts}</select>
          <button class="mini" data-act="dup">${t.tDup}</button>
        </div>

        <div class="sub">${t.tBandsTitle}</div>
        <div id="tariff-bands">${bandRows}</div>
        <button class="mini" data-act="add-band">+ ${t.tAddBand}</button>

        <div class="sub">${t.tRulesTitle}</div>
        <div id="tariff-rules">${ruleRows}</div>
        <button class="mini" data-act="add-rule">+ ${t.tAddRule}</button>

        <div class="tariff-foot">
          <label class="field"><span>${t.tFallback}</span>
            <select id="tariff-fallback">${this._bandOptions(s.fallback)}</select></label>
          <label class="field"><span>${t.tHolidays}</span>
            <select id="tariff-holidays"><option value="">${t.tNone}</option>${this._bandOptions(
      s.holidays_as
    )}</select></label>
          <label class="field"><span>${t.tMonths}</span>
            <input id="tariff-months" value="${
              s.months ? s.months.join(",") : ""
            }" placeholder="${t.tMonthsHint}"></label>
        </div>
      </div>`;
  }

  _dayCheckboxes(selected, ruleIdx) {
    const names = this._t.tDays || ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
    const sel = new Set(selected);
    return names
      .map(
        (n, d) =>
          `<label class="day ${sel.has(d) ? "on" : ""}"><input type="checkbox" class="r-day" data-day="${d}" ${
            sel.has(d) ? "checked" : ""
          }>${n}</label>`
      )
      .join("");
  }

  // Legge lo stato corrente dell'editor dal DOM nel modello (prima di
  // ri-renderizzare o salvare), così le modifiche in corso non si perdono.
  _readTariffEditor() {
    const root = this.shadowRoot;
    const box = root.getElementById("tariff-editor");
    if (!box || !box.querySelector(".tariff-box")) return this._customScheme;
    const bands = Array.from(box.querySelectorAll("#tariff-bands .trow")).map((row) => ({
      id: row.querySelector(".b-id").value.trim(),
      label: row.querySelector(".b-label").value.trim(),
      rank: Number(row.querySelector(".b-rank").value) || 1,
      color: row.querySelector(".b-color").value,
    })).filter((b) => b.id);
    const rules = Array.from(box.querySelectorAll("#tariff-rules .rule")).map((row) => ({
      band: row.querySelector(".r-band").value,
      days: Array.from(row.querySelectorAll(".r-day"))
        .filter((cb) => cb.checked)
        .map((cb) => Number(cb.dataset.day)),
      start: row.querySelector(".r-start").value.trim(),
      end: row.querySelector(".r-end").value.trim(),
    }));
    const monthsRaw = (root.getElementById("tariff-months").value || "").trim();
    const months = monthsRaw
      ? monthsRaw.split(",").map((m) => Number(m.trim())).filter((m) => m >= 1 && m <= 12)
      : null;
    this._customScheme = {
      bands: bands.length ? bands : this._blankScheme().bands,
      fallback: root.getElementById("tariff-fallback").value,
      holidays_as: root.getElementById("tariff-holidays").value,
      country: (this._customScheme && this._customScheme.country) || null,
      months: months && months.length ? months : null,
      rules,
    };
    return this._customScheme;
  }

  _renderTariffEditor() {
    const box = this.shadowRoot.getElementById("tariff-editor");
    if (box) box.innerHTML = this._tariffEditorHtml();
  }

  // Costruisce il payload `tariffs` (forma JSON schema) dal modello editor.
  _buildTariffPayload() {
    const s = this._readTariffEditor();
    const payload = {
      type: "tou",
      id: "custom",
      bands: s.bands,
      fallback: s.fallback,
      seasons: [{ months: s.months, rules: s.rules }],
    };
    if (s.holidays_as) payload.holidays_as = s.holidays_as;
    if (s.country) payload.country = s.country;
    return payload;
  }

  _wireTariff() {
    const sel = this.shadowRoot.getElementById("cfg-tariff_preset");
    if (sel) {
      sel.addEventListener("change", () => {
        if (sel.value === "custom") this._ensureCustomScheme();
        this._renderTariffEditor();
      });
    }
    const box = this.shadowRoot.getElementById("tariff-editor");
    if (!box) return;
    box.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      e.preventDefault();
      const act = btn.dataset.act;
      const s = this._readTariffEditor();
      if (act === "add-band") {
        s.bands.push({ id: "F" + (s.bands.length + 1), rank: s.bands.length + 1, label: "", color: "#29c7b0" });
      } else if (act === "del-band") {
        s.bands.splice(Number(btn.dataset.i), 1);
      } else if (act === "add-rule") {
        const first = s.bands[0] ? s.bands[0].id : "F1";
        s.rules.push({ band: first, days: [0, 1, 2, 3, 4], start: "00:00", end: "24:00" });
      } else if (act === "del-rule") {
        s.rules.splice(Number(btn.dataset.i), 1);
      } else if (act === "dup") {
        const id = this.shadowRoot.getElementById("tariff-dup").value;
        const p = this._presetById(id);
        if (p) this._customScheme = this._seedFromPreset(p);
      }
      this._renderTariffEditor();
    });
    box.addEventListener("change", (e) => {
      if (e.target.classList.contains("r-day")) {
        e.target.closest(".day").classList.toggle("on", e.target.checked);
      }
    });
  }

  _styles() {
    return `
      <style>
        :host { display:block; }
        .wrap {
          max-width: 880px; margin: 0 auto; padding: 16px;
          font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
          color: var(--primary-text-color, #212121);
        }
        h1 { font-size: 22px; font-weight: 600; margin: 8px 0 16px; }
        h2 { font-size: 15px; font-weight: 600; margin: 0 0 12px; opacity:.85; }
        .card {
          background: var(--card-background-color, #fff);
          border-radius: 14px; padding: 16px; margin-bottom: 16px;
          box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,.08));
        }
        .tiles { display:grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
        .tile { display:flex; flex-direction:column; gap:6px; padding:12px;
          border-radius:10px; background: var(--secondary-background-color, #f4f5f7); }
        .tile .k { font-size:12px; opacity:.7; }
        .tile .val { font-size:20px; font-weight:600; }
        .badge { align-self:flex-start; font-size:13px; font-weight:600; padding:3px 10px;
          border-radius:999px; color:#fff; }
        .badge.charging { background:#22c78b; }
        .badge.paused { background:#f59e0b; }
        .badge.idle { background:#9aa0a6; }
        .chip { align-self:flex-start; font-size:12px; padding:2px 8px; border-radius:999px;
          background: var(--divider-color, #e0e0e0); }
        .chart-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }
        .chart-head h2 { margin:0; }
        .modes { display:flex; gap:6px; }
        .mode { border:none; cursor:pointer; padding:6px 12px; border-radius:999px; font-size:13px;
          background: var(--secondary-background-color,#f0f0f0); color: inherit; }
        .mode.active { background:#29c7b0; color:#fff; }
        .nav { display:flex; align-items:center; justify-content:center; gap:16px; margin:4px 0 14px; }
        .navbtn { border:none; background:transparent; cursor:pointer; font-size:16px; color:inherit; opacity:.7; }
        .navbtn:hover { opacity:1; }
        #period-label { font-size:14px; font-weight:600; min-width:160px; text-align:center; text-transform:capitalize; }
        .chart { display:flex; flex-direction:column; gap:12px; min-height:60px; }
        .bar-row { display:grid; grid-template-columns: 34px 1fr auto; align-items:center; gap:10px; }
        .bar-label { font-weight:700; font-size:13px; }
        .bar-track { height:14px; border-radius:999px; background: var(--secondary-background-color,#eee); overflow:hidden; }
        .bar-fill { height:100%; border-radius:999px; transition: width .35s ease; }
        .bar-val { font-size:13px; font-weight:600; min-width:78px; text-align:right; }
        .empty { opacity:.6; font-size:14px; text-align:center; padding:16px 0; }
        .msg { padding:32px; text-align:center; opacity:.7; }

        details.settings { padding:0; }
        details.settings > summary {
          list-style:none; cursor:pointer; padding:16px; font-size:15px; font-weight:600;
          opacity:.85; display:flex; align-items:center; gap:8px;
        }
        details.settings > summary::-webkit-details-marker { display:none; }
        details.settings > summary::before { content:"⚙"; font-size:16px; opacity:.8; }
        details.settings[open] > summary { border-bottom:1px solid var(--divider-color,#e0e0e0); margin-bottom:12px; }
        details.settings > *:not(summary) { padding:0 16px; }
        details.settings > .hint { padding-bottom:16px; }
        .form-grid { display:grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap:14px; }
        .field { display:flex; flex-direction:column; gap:6px; }
        .field.wide { grid-column:1 / -1; }
        .field > span { font-size:12px; opacity:.7; }
        .field input, .field select {
          padding:8px 10px; border-radius:8px; font-size:14px; color:inherit;
          border:1px solid var(--divider-color,#d0d0d0);
          background: var(--secondary-background-color,#f7f8fa);
        }
        .hint { font-size:13px; opacity:.6; }
        .cb-list {
          display:flex; flex-direction:column; gap:4px; max-height:180px; overflow:auto;
          border:1px solid var(--divider-color,#e0e0e0); border-radius:8px; padding:8px;
          background: var(--secondary-background-color,#f7f8fa);
        }
        .cb-row { display:flex; align-items:center; gap:8px; font-size:14px; cursor:pointer; }
        .cb-row.single { padding-top:4px; }
        .cb-row.single.wide { grid-column:1 / -1; }
        .cb-row input { width:16px; height:16px; }
        .ctl-row {
          display:flex; align-items:center; gap:10px; font-size:14px; font-weight:600;
          cursor:pointer; margin:6px 0 14px;
          padding-bottom:14px; border-bottom:1px solid var(--divider-color,#e0e0e0);
        }
        .ctl-row input { width:18px; height:18px; }
        .save-row { display:flex; align-items:center; gap:14px; padding:16px; }
        .save-btn {
          border:none; cursor:pointer; padding:9px 20px; border-radius:999px;
          font-size:14px; font-weight:600; background:#29c7b0; color:#fff;
        }
        .save-btn:disabled { opacity:.6; cursor:default; }
        .save-status { font-size:13px; }
        .save-status.ok { color:#22c78b; }
        .save-status.err { color:#ef4444; }

        .tariff-box {
          grid-column:1 / -1; margin-top:6px; padding:12px; border-radius:10px;
          border:1px solid var(--divider-color,#e0e0e0);
          background: var(--secondary-background-color,#f7f8fa);
          display:flex; flex-direction:column; gap:8px;
        }
        .tariff-box .sub { font-size:12px; font-weight:600; opacity:.7; margin-top:6px; }
        .tariff-box .dup-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
        .tariff-box .dup-row .hint { flex:1 1 auto; min-width:120px; }
        .tariff-box select, .tariff-box input {
          padding:6px 8px; border-radius:7px; font-size:13px; color:inherit;
          border:1px solid var(--divider-color,#d0d0d0);
          background: var(--card-background-color,#fff);
        }
        .trow { display:flex; align-items:center; gap:6px; flex-wrap:wrap; padding:4px 0; }
        .trow .b-id { width:70px; }
        .trow .b-label { flex:1 1 90px; min-width:80px; }
        .trow .b-rank { width:56px; }
        .trow .b-color { width:38px; padding:2px; height:30px; }
        .trow.rule .r-band { width:96px; }
        .trow.rule .r-start, .trow.rule .r-end { width:64px; }
        .days { display:flex; gap:3px; flex-wrap:wrap; }
        .day {
          font-size:11px; padding:3px 6px; border-radius:6px; cursor:pointer; user-select:none;
          border:1px solid var(--divider-color,#d0d0d0); opacity:.6;
        }
        .day.on { opacity:1; background:#29c7b0; color:#fff; border-color:#29c7b0; }
        .day input { display:none; }
        .tdel { border:none; background:transparent; cursor:pointer; color:#ef4444; font-size:14px; padding:2px 6px; }
        .mini {
          align-self:flex-start; border:none; cursor:pointer; padding:5px 12px; border-radius:999px;
          font-size:12px; font-weight:600; background: var(--divider-color,#e0e0e0); color:inherit;
        }
        .tariff-foot { display:flex; gap:12px; flex-wrap:wrap; margin-top:8px; }
        .tariff-foot .field { flex:1 1 120px; }
      </style>`;
  }
}

customElements.define("evbalance-panel", EVBalancePanel);

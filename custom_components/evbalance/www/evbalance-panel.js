/**
 * EV Balance sidebar panel.
 *
 * Custom element dependency-free (nessuno step di build). Combina:
 *   - i valori live letti direttamente da hass.states (consumo casa/EV Charger,
 *     corrente concessa, stato carica, limite potenza), aggiornati a ogni
 *     assegnazione della property `hass`;
 *   - l'energia per fascia oraria letta dalle long-term statistics native via
 *     il comando core `recorder/statistics_during_period` (granularità oraria
 *     per il giorno corrente, giornaliera sommata per i mesi), navigabile
 *     all'indietro.
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
    this._narrow = false; // vista stretta (mobile): sidebar nascosta
    this._tab = "live"; // "live" | "stats" | "settings"
    this._echarts = null; // modulo ECharts (import lazy alla prima apertura stats)
    this._echartsLoading = false;
    this._charts = {}; // istanze ECharts per elId (main, ev, trend)
    this._trendLoaded = false; // il trend 12 mesi si carica una volta sola
  }

  connectedCallback() {
    if (!this._onResize) this._onResize = () => this._resizeCharts();
    window.addEventListener("resize", this._onResize);
  }

  disconnectedCallback() {
    if (this._onResize) window.removeEventListener("resize", this._onResize);
    Object.values(this._charts).forEach((c) => c && c.dispose());
    this._charts = {};
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

  // Home Assistant assegna `narrow` quando la sidebar è collassata (mobile).
  // Serve a decidere se mostrare il pulsante-menu che riapre la sidebar.
  set narrow(value) {
    this._narrow = value;
    this._syncMenuButton();
  }

  get narrow() {
    return this._narrow;
  }

  // Il pulsante-menu ha senso quando non c'è una sidebar sempre visibile:
  // vista stretta oppure sidebar impostata su "always_hidden".
  _showMenuButton() {
    if (this._narrow) return true;
    return this._hass && this._hass.dockedSidebar === "always_hidden";
  }

  _syncMenuButton() {
    const btn = this.shadowRoot && this.shadowRoot.querySelector(".menu-btn");
    if (btn) btn.style.display = this._showMenuButton() ? "" : "none";
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
    // Le statistiche (e il modulo ECharts) si caricano alla prima apertura del
    // relativo tab: vedi _openStats().
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

  _fmtMoney(amount) {
    const cur = (this._meta && this._meta.currency) || "€";
    return (
      new Intl.NumberFormat(this._locale, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(amount || 0) +
      " " +
      cur
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

  // Richiesta statistiche core: `change` (delta) + `sum` (cumulata) per periodo.
  async _fetchStats(ids, start, end, period) {
    if (!ids.length) return {};
    try {
      const msg = {
        type: "recorder/statistics_during_period",
        statistic_ids: ids,
        start_time: start.toISOString(),
        period,
        types: ["change", "sum"],
      };
      if (end) msg.end_time = end.toISOString();
      return await this._hass.callWS(msg);
    } catch (err) {
      return {};
    }
  }

  // `start` di un bucket: timestamp ms (numero) o stringa ISO -> ms.
  _rowMs(row) {
    return typeof row.start === "number" ? row.start : Date.parse(row.start);
  }

  // Filtro che tiene solo i bucket dentro la finestra [start, end): HA può
  // restituire bucket appena fuori dai limiti (e talvolta ignorare end_time).
  _windowFilter(start, end) {
    const startMs = start.getTime();
    const endMs = end ? end.getTime() : Infinity;
    return (row) => {
      const s = this._rowMs(row);
      return !Number.isFinite(s) || (s >= startMs && s < endMs);
    };
  }

  async _loadStats() {
    if (!this._meta || this._statsLoading) return;
    const totalStats = this._meta.band_stats || {};
    const evStats = this._meta.band_stats_ev || {};
    const bands = this._meta.bands;
    const chart = this.shadowRoot.getElementById("chart");
    if (!chart) return;

    const totalIds = Object.values(totalStats).filter(Boolean);
    if (totalIds.length === 0) {
      this._renderKpis(null);
      chart.innerHTML = `<div class="empty">${this._t.noData}</div>`;
      this._updateNav();
      return;
    }
    const evIds = Object.values(evStats).filter(Boolean);
    const hasEv = evIds.length > 0;
    const ids = Array.from(new Set([...totalIds, ...evIds]));

    // Finestra: oggi a granularità oraria, mese a granularità giornaliera
    // (aggregando i "change" giorno per giorno). Il mese corrente si ferma a ora.
    this._statsLoading = true;
    const now = new Date();
    let start;
    let end;
    let period;
    if (this._mode === "day") {
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0);
      end = null;
      period = "hour";
    } else {
      const r = this._monthRange(this._monthOffset);
      start = r.start;
      end = r.end > now ? now : r.end;
      period = "day";
    }

    const result = await this._fetchStats(ids, start, end, period);
    this._statsLoading = false;

    const inWindow = this._windowFilter(start, end);

    // Per fascia: totale casa, totale EV e serie temporale impilata (casa).
    const totalByBand = {};
    const evByBand = {};
    const perBandBucket = {}; // band -> Map(bucketMs -> kwh)
    const bucketSet = new Set();
    for (const band of bands) {
      const m = new Map();
      let sum = 0;
      const tRows = ((totalStats[band] && result[totalStats[band]]) || []).filter(inWindow);
      for (const row of tRows) {
        const v = Number(row.change) || 0;
        const key = this._rowMs(row);
        m.set(key, (m.get(key) || 0) + v);
        bucketSet.add(key);
        sum += v;
      }
      perBandBucket[band] = m;
      totalByBand[band] = Math.max(0, sum);

      let esum = 0;
      const eRows = ((evStats[band] && result[evStats[band]]) || []).filter(inWindow);
      for (const row of eRows) esum += Number(row.change) || 0;
      evByBand[band] = Math.max(0, esum);
    }

    const buckets = Array.from(bucketSet).sort((a, b) => a - b);
    this._renderKpis(totalByBand, evByBand, hasEv);
    this._renderMainChart(bands, buckets, perBandBucket, period);
    this._renderEvSplit(totalByBand, evByBand, hasEv);
    this._updateNav();
  }

  // Passa al tab richiesto: attiva il bottone e mostra il pannello corrispondente.
  _syncTabs() {
    const root = this.shadowRoot;
    root.querySelectorAll(".tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === this._tab);
    });
    root.querySelectorAll(".tabpanel").forEach((p) => {
      p.style.display = p.dataset.panel === this._tab ? "" : "none";
    });
    // ECharts calcola le dimensioni solo su un contenitore visibile: al ritorno
    // sul tab statistiche forza un resize di tutti i grafici.
    if (this._tab === "stats") this._resizeCharts();
  }

  // Apertura del tab statistiche: carica ECharts (lazy), i dati della finestra
  // corrente e — una volta sola — il trend 12 mesi.
  async _openStats() {
    await this._ensureECharts();
    this._loadStats();
    if (!this._trendLoaded) {
      this._trendLoaded = true;
      this._loadTrend();
    }
  }

  // Import lazy del modulo ECharts vendorizzato in www/. Propaga la stessa
  // versione di cache-busting (?v=) del pannello. In caso di errore il grafico
  // ricade sulle barre CSS.
  async _ensureECharts() {
    if (this._echarts || this._echartsLoading) return;
    this._echartsLoading = true;
    try {
      const url = new URL("./echarts.esm.min.js", import.meta.url);
      const v = new URL(import.meta.url).searchParams.get("v");
      if (v) url.searchParams.set("v", v);
      this._echarts = await import(url.href);
    } catch (err) {
      this._echarts = null;
    }
    this._echartsLoading = false;
  }

  // --- Gestione istanze ECharts (main, ev, trend) ----------------------

  _getChart(elId) {
    const el = this.shadowRoot.getElementById(elId);
    if (!el || !this._echarts) return null;
    let inst = this._echarts.getInstanceByDom(el);
    if (!inst) {
      el.innerHTML = "";
      inst = this._echarts.init(el);
    }
    this._charts[elId] = inst;
    return inst;
  }

  _disposeChart(elId) {
    const inst = this._charts[elId];
    if (inst) {
      inst.dispose();
      delete this._charts[elId];
    }
  }

  _resizeCharts() {
    Object.values(this._charts).forEach((c) => c && c.resize());
  }

  // Colori di testo/assi/griglia dal tema Home Assistant.
  _axisColors() {
    const cs = getComputedStyle(this);
    const v = (name, fb) => (cs.getPropertyValue(name) || "").trim() || fb;
    const text = v("--primary-text-color", "#212121");
    return {
      text,
      axis: v("--secondary-text-color", text),
      grid: v("--divider-color", "rgba(127,127,127,.25)"),
    };
  }

  // Opzioni comuni per un grafico a barre impilate per fascia (main + trend).
  _stackedOption(xLabels, series, bands) {
    const c = this._axisColors();
    const nf = new Intl.NumberFormat(this._locale, { maximumFractionDigits: 2 });
    return {
      animationDuration: 400,
      color: bands.map((b) => this._bandColor(b)),
      legend: {
        data: bands,
        top: 0,
        itemHeight: 10,
        itemWidth: 14,
        textStyle: { color: c.text },
      },
      grid: { left: 6, right: 12, top: 34, bottom: 4, containLabel: true },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        valueFormatter: (v) => `${nf.format(v)} kWh`,
      },
      xAxis: {
        type: "category",
        data: xLabels,
        axisLabel: { color: c.axis },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: c.grid } },
      },
      yAxis: {
        type: "value",
        name: "kWh",
        nameTextStyle: { color: c.axis, align: "left" },
        axisLabel: { color: c.axis },
        splitLine: { lineStyle: { color: c.grid } },
      },
      series,
    };
  }

  _bucketLabel(ms, period) {
    const d = new Date(ms);
    return period === "hour"
      ? String(d.getHours()).padStart(2, "0")
      : String(d.getDate());
  }

  // Grafico principale: barre impilate per fascia lungo il periodo (ore oggi /
  // giorni nel mese). Senza ECharts ricade sulle barre CSS aggregate.
  _renderMainChart(bands, buckets, perBandBucket, period) {
    const el = this.shadowRoot.getElementById("chart");
    if (!el) return;
    const anyData =
      buckets.length > 0 &&
      bands.some((b) => Array.from(perBandBucket[b].values()).some((v) => v > 0));

    if (!this._echarts) {
      const totals = {};
      bands.forEach((b) => {
        totals[b] = Array.from(perBandBucket[b].values()).reduce((a, c) => a + c, 0);
      });
      this._renderBars(totals, anyData);
      return;
    }

    if (!anyData) {
      this._disposeChart("chart");
      el.innerHTML = `<div class="empty">${this._t.noData}</div>`;
      return;
    }

    const inst = this._getChart("chart");
    if (!inst) return;
    const xLabels = buckets.map((ms) => this._bucketLabel(ms, period));
    const series = bands.map((band) => ({
      name: band,
      type: "bar",
      stack: "e",
      itemStyle: { color: this._bandColor(band) },
      emphasis: { focus: "series" },
      data: buckets.map((ms) => Number((perBandBucket[band].get(ms) || 0).toFixed(3))),
    }));
    inst.setOption(this._stackedOption(xLabels, series, bands), true);
    inst.resize();
  }

  // Tile KPI: totale periodo, quota nella fascia più economica, e (se ci sono
  // statistiche EV) energia EV e quota EV sul totale.
  _renderKpis(totalByBand, evByBand, hasEv) {
    const el = this.shadowRoot.getElementById("stat-kpis");
    if (!el) return;
    const t = this._t;
    if (!totalByBand) {
      el.innerHTML = "";
      return;
    }
    const bands = this._meta.bands;
    const meta = this._meta.band_meta || {};
    const total = bands.reduce((a, b) => a + (totalByBand[b] || 0), 0);

    // Fascia più economica = rank minimo (1 = più economica).
    let cheapest = null;
    let minRank = Infinity;
    for (const b of bands) {
      const rank = meta[b] && meta[b].rank != null ? meta[b].rank : null;
      if (rank != null && rank < minRank) {
        minRank = rank;
        cheapest = b;
      }
    }
    const cheapKwh = cheapest ? totalByBand[cheapest] || 0 : 0;
    const cheapPct = total > 0 ? Math.round((cheapKwh / total) * 100) : 0;
    const evTotal = hasEv ? bands.reduce((a, b) => a + (evByBand[b] || 0), 0) : 0;
    const evPct = total > 0 ? Math.round((evTotal / total) * 100) : 0;

    const tiles = [
      { k: t.statTotal, v: this._fmtEnergy(total) },
      {
        k: `${t.statCheapest}${cheapest ? ` (${cheapest})` : ""}`,
        v: `${cheapPct}%`,
      },
    ];
    if (hasEv) {
      tiles.push({ k: t.statEv, v: this._fmtEnergy(evTotal) });
      tiles.push({ k: t.statEvShare, v: `${evPct}%` });
    }

    // Stima costi: solo se almeno una fascia ha un prezzo €/kWh configurato.
    const prices = this._meta.band_prices || {};
    const hasPrice = bands.some((b) => Number(prices[b]) > 0);
    if (hasPrice) {
      const cost = bands.reduce(
        (a, b) => a + (totalByBand[b] || 0) * (Number(prices[b]) || 0),
        0
      );
      tiles.push({ k: t.statCost, v: this._fmtMoney(cost) });
      if (hasEv) {
        const evCost = bands.reduce(
          (a, b) => a + (evByBand[b] || 0) * (Number(prices[b]) || 0),
          0
        );
        tiles.push({ k: t.statEvCost, v: this._fmtMoney(evCost) });
      }
    }

    el.innerHTML = tiles
      .map(
        (x) =>
          `<div class="kpi"><span class="kpi-k">${x.k}</span><span class="kpi-v">${x.v}</span></div>`
      )
      .join("");
  }

  // Barra orizzontale 100%: quota EV vs resto casa sul periodo. Nascosta se non
  // ci sono statistiche EV o non c'è consumo.
  _renderEvSplit(totalByBand, evByBand, hasEv) {
    const wrap = this.shadowRoot.getElementById("ev-wrap");
    const el = this.shadowRoot.getElementById("chart-ev");
    if (!wrap || !el) return;
    const bands = this._meta.bands;
    const evTotal = bands.reduce((a, b) => a + (evByBand[b] || 0), 0);
    const total = bands.reduce((a, b) => a + (totalByBand[b] || 0), 0);
    const house = Math.max(0, total - evTotal);

    if (!hasEv || total <= 0) {
      wrap.style.display = "none";
      this._disposeChart("chart-ev");
      return;
    }
    wrap.style.display = "";
    if (!this._echarts) return;

    const inst = this._getChart("chart-ev");
    if (!inst) return;
    const t = this._t;
    const c = this._axisColors();
    const nf = new Intl.NumberFormat(this._locale, { maximumFractionDigits: 2 });
    const pct = (x) => Math.round((x / total) * 100);
    inst.setOption(
      {
        animationDuration: 400,
        grid: { left: 2, right: 2, top: 2, bottom: 2 },
        tooltip: { trigger: "item", valueFormatter: (v) => `${nf.format(v)} kWh` },
        xAxis: { type: "value", show: false, max: total },
        yAxis: { type: "category", show: false, data: [""] },
        series: [
          {
            name: t.statEv,
            type: "bar",
            stack: "s",
            data: [Number(evTotal.toFixed(3))],
            itemStyle: { color: "#29c7b0", borderRadius: [6, 0, 0, 6] },
            label: {
              show: evTotal > 0,
              formatter: `${t.statEv} ${pct(evTotal)}%`,
              color: "#fff",
              fontWeight: 700,
            },
          },
          {
            name: t.statHouse,
            type: "bar",
            stack: "s",
            data: [Number(house.toFixed(3))],
            itemStyle: { color: "rgba(127,127,127,.35)", borderRadius: [0, 6, 6, 0] },
            label: {
              show: house > 0,
              formatter: `${t.statHouse} ${pct(house)}%`,
              color: c.text,
              fontWeight: 700,
            },
          },
        ],
      },
      true
    );
    inst.resize();
  }

  // Trend 12 mesi: barre impilate per fascia, aggregando i "change" giornalieri
  // per mese (robusto: non dipende dal change mensile nativo). Indipendente dal
  // toggle giorno/mese.
  async _loadTrend() {
    if (!this._meta) return;
    const totalStats = this._meta.band_stats || {};
    const bands = this._meta.bands;
    const el = this.shadowRoot.getElementById("chart-trend");
    if (!el) return;
    const ids = Object.values(totalStats).filter(Boolean);
    if (ids.length === 0) {
      el.innerHTML = `<div class="empty">${this._t.noData}</div>`;
      return;
    }

    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth() - 11, 1, 0, 0, 0);
    const result = await this._fetchStats(ids, start, null, "day");

    const months = [];
    const idx = new Map();
    for (let i = 0; i < 12; i++) {
      const d = new Date(now.getFullYear(), now.getMonth() - 11 + i, 1);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
      months.push(d);
      idx.set(key, i);
    }
    const perBand = {};
    bands.forEach((b) => (perBand[b] = new Array(12).fill(0)));
    const inWindow = this._windowFilter(start, null);
    for (const band of bands) {
      const rows = ((totalStats[band] && result[totalStats[band]]) || []).filter(inWindow);
      for (const row of rows) {
        const d = new Date(this._rowMs(row));
        const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
        const i = idx.get(key);
        if (i != null) perBand[band][i] += Number(row.change) || 0;
      }
    }

    const anyData = bands.some((b) => perBand[b].some((v) => v > 0));
    if (!this._echarts || !anyData) {
      this._disposeChart("chart-trend");
      el.innerHTML = anyData ? "" : `<div class="empty">${this._t.noData}</div>`;
      return;
    }
    const inst = this._getChart("chart-trend");
    if (!inst) return;
    const xLabels = months.map((d) =>
      d.toLocaleDateString(this._locale, { month: "short" })
    );
    const series = bands.map((band) => ({
      name: band,
      type: "bar",
      stack: "e",
      itemStyle: { color: this._bandColor(band) },
      data: perBand[band].map((v) => Number(Math.max(0, v).toFixed(3))),
    }));
    inst.setOption(this._stackedOption(xLabels, series, bands), true);
    inst.resize();
  }

  // Fallback dependency-free: barre CSS orizzontali (usato se ECharts non carica).
  _renderBars(totals, anyData) {
    const chart = this.shadowRoot.getElementById("chart");
    if (!chart) return;
    const bands = this._meta.bands;
    if (!anyData) {
      chart.innerHTML = `<div class="empty">${this._t.noData}</div>`;
      return;
    }
    const max = Math.max(1e-6, ...bands.map((b) => totals[b] || 0));
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
    chart.innerHTML = `<div class="bars">${rows}</div>`;
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
        <div class="topbar">
          <button class="menu-btn" title="${t.menu || "Menu"}" aria-label="${t.menu || "Menu"}">
            <svg viewBox="0 0 24 24"><path d="M3 6h18v2H3V6m0 5h18v2H3v-2m0 5h18v2H3v-2Z"/></svg>
          </button>
          <h1>${this._meta.title || t.title}</h1>
        </div>

        <div class="tabs" role="tablist">
          <button class="tab" data-tab="live" role="tab">${t.live}</button>
          <button class="tab" data-tab="stats" role="tab">${t.statistics || t.energyByBand}</button>
          <button class="tab" data-tab="settings" role="tab">${t.settings}</button>
        </div>

        <section class="tabpanel" data-panel="live">
          <section class="card">
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
        </section>

        <section class="tabpanel" data-panel="stats" style="display:none">
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
            <div id="stat-kpis" class="kpis"></div>
            <div id="chart" class="chart"><div class="empty">${t.loading}</div></div>
            <div id="ev-wrap" class="ev-wrap" style="display:none">
              <div class="sub-h">${t.statEv} · ${t.statHouse}</div>
              <div id="chart-ev" class="chart-ev"></div>
            </div>
          </section>

          <section class="card">
            <div class="chart-head"><h2>${t.statTrend}</h2></div>
            <div id="chart-trend" class="chart-trend"><div class="empty">${t.loading}</div></div>
          </section>
        </section>

        <section class="tabpanel" data-panel="settings" style="display:none">
          ${this._settingsSection()}
        </section>
      </div>`;

    // Handler dei tab.
    this.shadowRoot.querySelectorAll(".tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._tab = btn.dataset.tab;
        this._syncTabs();
        if (this._tab === "stats") this._openStats();
      });
    });

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
    this._syncTabs();
    this._updateNav();
    this._wireSettings();

    // Pulsante-menu: riapre la sidebar di Home Assistant. L'evento
    // `hass-toggle-menu` è lo stesso emesso da <ha-menu-button> del frontend
    // core; risale (bubbles/composed) fino a <home-assistant-main> che
    // apre/chiude la sidebar.
    const menuBtn = this.shadowRoot.querySelector(".menu-btn");
    if (menuBtn) {
      menuBtn.addEventListener("click", () => {
        this.dispatchEvent(
          new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true })
        );
      });
    }
    this._syncMenuButton();
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
      return `<section class="card settings"><h2>${t.settings}</h2>
        ${bal}<div class="hint">${t.noAdmin}</div></section>`;
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

        ${this._pricesSection(c)}

        <label class="cb-row single wide">
          <input type="checkbox" id="cfg-show_panel" ${
            c.show_panel ? "checked" : ""
          }><span>${t.fShowPanel}</span></label>
      </div>
      <div class="save-row">
        <button id="save-btn" class="save-btn">${t.save}</button>
        <span id="save-status" class="save-status"></span>
      </div>`;

    return `<section class="card settings"><h2>${t.settings}</h2>${bal}${body}</section>`;
  }

  // Prezzi €/kWh per fascia (per la stima costi nel tab Statistiche). Le fasce
  // sono quelle dello schema attivo; i prezzi valgono sia per i preset sia per
  // lo schema custom e sono indipendenti dalla tariffa selezionata.
  _pricesSection(c) {
    const t = this._t;
    const bands = (this._meta && this._meta.bands) || [];
    if (!bands.length) return "";
    const prices = (c && c.tariff_prices) || {};
    const meta = (this._meta && this._meta.band_meta) || {};
    const rows = bands
      .map((b) => {
        const color = this._bandColor(b);
        const val = prices[b] != null ? prices[b] : "";
        const label = (meta[b] && meta[b].label) || b;
        const tag = label && label !== b ? `${b} · ${this._esc(label)}` : b;
        return `<label class="field">
          <span style="color:${color}">${tag}</span>
          <input class="price-in" data-band="${this._esc(b)}" type="number"
            step="0.001" min="0" value="${val}" placeholder="0.000"></label>`;
      })
      .join("");
    const cur = (c && c.currency) || "€";
    return `<div class="prices-box wide">
      <div class="sub">${t.pPrices}</div>
      <label class="field cur"><span>${t.pCurrency}</span>
        <input id="cfg-currency" type="text" maxlength="4"
          value="${this._esc(cur)}"></label>
      <div class="form-grid">${rows}</div>
      ${this._calcBlock(t, cur)}
    </div>`;
  }

  // Calcolatore prezzo medio: importo bolletta / kWh -> €/kWh, con opzione di
  // leggere i kWh dai consumi tracciati per un mese; "Applica" compila i prezzi.
  _calcBlock(t, cur) {
    const now = new Date();
    const opts = [];
    for (let i = 0; i < 13; i++) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      const label = d.toLocaleDateString(this._locale, {
        month: "long",
        year: "numeric",
      });
      opts.push(`<option value="${i}">${label}</option>`);
    }
    return `<div class="calc">
      <div class="sub">${t.cTitle}</div>
      <div class="calc-row">
        <label class="field"><span>${t.cAmount} (${this._esc(cur)})</span>
          <input id="calc-amount" type="number" step="0.01" min="0" placeholder="0.00"></label>
        <label class="field"><span>kWh</span>
          <input id="calc-kwh" type="number" step="0.1" min="0" placeholder="0"></label>
        <label class="field"><span>${t.cPeriod}</span>
          <select id="calc-period">${opts.join("")}</select></label>
        <button type="button" id="calc-read" class="mini">${t.cRead}</button>
      </div>
      <div class="calc-out">
        <span>${t.cAvg}: <b id="calc-result">—</b></span>
        <button type="button" id="calc-apply" class="mini" disabled>${t.cApply}</button>
      </div>
    </div>`;
  }

  _wireSettings() {
    const btn = this.shadowRoot.getElementById("save-btn");
    if (btn) btn.addEventListener("click", () => this._save());
    const bal = this.shadowRoot.getElementById("ctl-balancing");
    if (bal) {
      bal.addEventListener("change", (e) => this._toggleBalancing(e.target.checked));
    }
    this._wireTariff();
    this._wireCalc();
  }

  _wireCalc() {
    const root = this.shadowRoot;
    const amount = root.getElementById("calc-amount");
    const kwh = root.getElementById("calc-kwh");
    const read = root.getElementById("calc-read");
    const apply = root.getElementById("calc-apply");
    if (!amount || !kwh) return;
    this._calcAvg = null;
    amount.addEventListener("input", () => this._updateCalc());
    kwh.addEventListener("input", () => this._updateCalc());
    if (read) read.addEventListener("click", () => this._calcReadPeriod());
    if (apply) apply.addEventListener("click", () => this._calcApply());
  }

  _updateCalc() {
    const root = this.shadowRoot;
    const amt = Number(root.getElementById("calc-amount").value);
    const kwh = Number(root.getElementById("calc-kwh").value);
    const res = root.getElementById("calc-result");
    const apply = root.getElementById("calc-apply");
    const curEl = root.getElementById("cfg-currency");
    const cur =
      (curEl && curEl.value.trim()) || (this._config && this._config.currency) || "€";
    if (amt > 0 && kwh > 0) {
      this._calcAvg = amt / kwh;
      res.textContent = `${new Intl.NumberFormat(this._locale, {
        maximumFractionDigits: 4,
      }).format(this._calcAvg)} ${cur}/kWh`;
      if (apply) apply.disabled = false;
    } else {
      this._calcAvg = null;
      res.textContent = "—";
      if (apply) apply.disabled = true;
    }
  }

  // Legge dai consumi tracciati (sensori "totale casa" per fascia) i kWh del mese
  // selezionato e li mette nel campo kWh del calcolatore.
  async _calcReadPeriod() {
    if (!this._meta) return;
    const root = this.shadowRoot;
    const read = root.getElementById("calc-read");
    const offset = Number(root.getElementById("calc-period").value) || 0;
    const ids = Object.values(this._meta.band_stats || {}).filter(Boolean);
    if (!ids.length) return;
    const r = this._monthRange(offset);
    const now = new Date();
    const end = r.end > now ? now : r.end;
    if (read) read.disabled = true;
    const result = await this._fetchStats(ids, r.start, end, "day");
    const inWindow = this._windowFilter(r.start, end);
    let kwh = 0;
    for (const band of this._meta.bands) {
      const sid = this._meta.band_stats[band];
      const rows = ((sid && result[sid]) || []).filter(inWindow);
      for (const row of rows) kwh += Number(row.change) || 0;
    }
    root.getElementById("calc-kwh").value = Math.round(Math.max(0, kwh) * 10) / 10;
    if (read) read.disabled = false;
    this._updateCalc();
  }

  // Compila tutti i campi prezzo con il prezzo medio calcolato (prezzo piatto).
  _calcApply() {
    if (this._calcAvg == null) return;
    const v = Number(this._calcAvg.toFixed(4));
    this.shadowRoot.querySelectorAll(".price-in").forEach((inp) => {
      inp.value = v;
    });
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
      tariff_prices: this._readPrices(),
      currency: (g("cfg-currency").value || "€").trim() || "€",
      show_panel: g("cfg-show_panel").checked,
    };
  }

  _readPrices() {
    const prices = {};
    this.shadowRoot.querySelectorAll(".price-in").forEach((inp) => {
      const v = inp.value.trim();
      if (v !== "") {
        const n = Number(v);
        if (Number.isFinite(n) && n >= 0) prices[inp.dataset.band] = n;
      }
    });
    return prices;
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
        this._loadTrend();
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
        .topbar { display:flex; align-items:center; gap:8px; margin: 8px 0 16px; }
        .topbar h1 { margin: 0; }
        .menu-btn {
          flex: 0 0 auto; display:inline-flex; align-items:center; justify-content:center;
          width:40px; height:40px; padding:0; border:none; border-radius:50%;
          background:transparent; color: var(--primary-text-color, #212121); cursor:pointer;
        }
        .menu-btn:hover { background: var(--secondary-background-color, rgba(0,0,0,.06)); }
        .menu-btn svg { width:24px; height:24px; fill: currentColor; }
        h1 { font-size: 22px; font-weight: 600; margin: 8px 0 16px; }
        h2 { font-size: 15px; font-weight: 600; margin: 0 0 12px; opacity:.85; }
        .tabs {
          display:flex; gap:4px; margin-bottom:16px;
          border-bottom:1px solid var(--divider-color, #e0e0e0);
        }
        .tab {
          border:none; background:transparent; cursor:pointer; padding:10px 16px;
          font-size:14px; font-weight:600; color:inherit; opacity:.6;
          border-bottom:2px solid transparent; margin-bottom:-1px;
        }
        .tab:hover { opacity:.9; }
        .tab.active { opacity:1; color:#29c7b0; border-bottom-color:#29c7b0; }
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
        .kpis { display:grid; grid-template-columns: repeat(auto-fit,minmax(120px,1fr)); gap:10px; margin-bottom:14px; }
        .kpi { display:flex; flex-direction:column; gap:4px; padding:10px 12px;
          border-radius:10px; background: var(--secondary-background-color, #f4f5f7); }
        .kpi-k { font-size:11px; opacity:.7; }
        .kpi-v { font-size:18px; font-weight:700; }
        .chart { height:320px; }
        .ev-wrap { margin-top:14px; }
        .sub-h { font-size:12px; font-weight:600; opacity:.7; margin-bottom:6px; }
        .chart-ev { height:34px; }
        .chart-trend { height:280px; }
        .bars { display:flex; flex-direction:column; gap:12px; padding-top:8px; }
        .bar-row { display:grid; grid-template-columns: 34px 1fr auto; align-items:center; gap:10px; }
        .bar-label { font-weight:700; font-size:13px; }
        .bar-track { height:14px; border-radius:999px; background: var(--secondary-background-color,#eee); overflow:hidden; }
        .bar-fill { height:100%; border-radius:999px; transition: width .35s ease; }
        .bar-val { font-size:13px; font-weight:600; min-width:78px; text-align:right; }
        .empty { opacity:.6; font-size:14px; text-align:center; padding:16px 0; }
        .msg { padding:32px; text-align:center; opacity:.7; }

        .card.settings h2::before { content:"⚙ "; opacity:.8; }
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
        .save-row { display:flex; align-items:center; gap:14px; padding:8px 0 0; }
        .save-btn {
          border:none; cursor:pointer; padding:9px 20px; border-radius:999px;
          font-size:14px; font-weight:600; background:#29c7b0; color:#fff;
        }
        .save-btn:disabled { opacity:.6; cursor:default; }
        .save-status { font-size:13px; }
        .save-status.ok { color:#22c78b; }
        .save-status.err { color:#ef4444; }

        .tariff-box, .prices-box {
          grid-column:1 / -1; margin-top:6px; padding:12px; border-radius:10px;
          border:1px solid var(--divider-color,#e0e0e0);
          background: var(--secondary-background-color,#f7f8fa);
          display:flex; flex-direction:column; gap:8px;
        }
        .prices-box .sub { font-size:12px; font-weight:600; opacity:.7; }
        .prices-box .field.cur { max-width:120px; }
        .prices-box .price-in {
          padding:8px 10px; border-radius:8px; font-size:14px; color:inherit;
          border:1px solid var(--divider-color,#d0d0d0);
          background: var(--card-background-color,#fff);
        }
        .calc {
          margin-top:10px; padding-top:10px; display:flex; flex-direction:column; gap:8px;
          border-top:1px solid var(--divider-color,#e0e0e0);
        }
        .calc-row { display:flex; align-items:flex-end; gap:10px; flex-wrap:wrap; }
        .calc-row .field { flex:1 1 120px; gap:6px; }
        .calc-row input, .calc-row select {
          padding:8px 10px; border-radius:8px; font-size:14px; color:inherit;
          border:1px solid var(--divider-color,#d0d0d0);
          background: var(--card-background-color,#fff);
        }
        .calc-row .mini { flex:0 0 auto; }
        .calc-out { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; font-size:14px; }
        .calc-out b { font-size:15px; }
        .mini:disabled { opacity:.5; cursor:default; }
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

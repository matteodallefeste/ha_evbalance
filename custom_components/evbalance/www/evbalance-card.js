/**
 * Card Lovelace di EV Balance: la wallbox in una dashboard.
 *
 * Mostra la stessa card della wallbox del pannello -- stato del connettore,
 * corrente chiesta e offerta, energia di sessione, stato di carica, correnti
 * per fase -- con i bottoni ferma/riprendi e "ricarica ora". Il contenuto e la
 * logica arrivano dal modulo condiviso (evbalance-wallbox.js), così card e
 * pannello non possono divergere; qui c'e' solo l'involucro Lovelace.
 *
 * L'utente la aggiunge con `type: custom:evbalance-card`. La risorsa la
 * registra l'integrazione (vedi panel.py), quindi non serve aggiungerla a mano.
 *
 * Dipendenze: nessuna, nessuno step di build.
 */

const CARD_NAME = "evbalance-card";
const TRANSLATIONS_MODULE = "./evbalance-translations.js";
const WALLBOX_MODULE = "./evbalance-wallbox.js";

/**
 * URL di un modulo fratello con lo stesso token anti-cache di questo file: la
 * cartella www/ e' servita con cache lunga.
 */
function siblingUrl(name) {
  const url = new URL(name, import.meta.url);
  const v = new URL(import.meta.url).searchParams.get("v");
  if (v) url.searchParams.set("v", v);
  return url.href;
}

export class EVBalanceCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._meta = null;
    this._config = null; // configurazione dell'integrazione (non della card)
    this._tr = {};
    this._wb = null;
    this._initStarted = false;
    this._rendered = false;
  }

  // --- Interfaccia Lovelace ---------------------------------------------

  /** Config della card in dashboard: non ha opzioni, la wallbox e' una sola. */
  setConfig(config) {
    this._cardConfig = config || {};
  }

  /** Altezza in unità da 50px, usata da Lovelace per impaginare le colonne. */
  getCardSize() {
    return 5;
  }

  static getStubConfig() {
    return { type: `custom:${CARD_NAME}` };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initStarted) {
      this._initStarted = true;
      this._init();
    } else if (this._rendered) {
      this._update();
    }
  }

  // --- Avvio ------------------------------------------------------------

  async _init() {
    try {
      const [tr, wb] = await Promise.all([
        import(siblingUrl(TRANSLATIONS_MODULE)),
        import(siblingUrl(WALLBOX_MODULE)),
      ]);
      this._tr = tr.TR || {};
      this._wb = wb;
    } catch (err) {
      this._fail("EV Balance: moduli del frontend non caricati");
      return;
    }

    try {
      this._meta = await this._hass.callWS({ type: "evbalance/panel" });
    } catch (err) {
      this._fail(this._t.error || "EV Balance: integrazione non configurata");
      return;
    }
    // Serve solo all'avviso con l'indirizzo da configurare nella wallbox: se
    // non arriva si tira avanti, `_isOcpp` ricade sui metadati.
    try {
      const res = await this._hass.callWS({ type: "evbalance/config/get" });
      this._config = res.config;
    } catch (err) {
      this._config = null;
    }

    if (!this._isOcpp) {
      this._fail(this._t.cardOnlyOcpp);
      return;
    }
    this._render();
    this._update();
  }

  get _t() {
    const lang = (this._hass && this._hass.language) || "en";
    return this._tr[lang] || this._tr.en || {};
  }

  get _locale() {
    return (this._hass && this._hass.language) || "en";
  }

  get _isOcpp() {
    if (this._config && this._config.control_mode) {
      return this._config.control_mode === "ocpp";
    }
    return !!this._meta && this._meta.control_mode === "ocpp";
  }

  // --- Contratto richiesto dal modulo condiviso -------------------------

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

  _fmtEnergy(kwh) {
    return (
      new Intl.NumberFormat(this._locale, {
        maximumFractionDigits: 2,
      }).format(kwh || 0) + " kWh"
    );
  }

  _ocppUrl() {
    return this._wb.ocppUrl(this._config, this._meta && this._meta.local_ip);
  }

  // --- Render -----------------------------------------------------------

  _fail(message) {
    this.shadowRoot.innerHTML = `
      <style>${this._wb ? this._wb.WALLBOX_CSS : ""}</style>
      <ha-card><div class="wb"><div class="hint">${message || "EV Balance"}</div>
      </div></ha-card>`;
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>${this._wb.WALLBOX_CSS}</style>
      <ha-card>
        <div class="wb" id="ocpp-card">${this._wb.wallboxMarkup(this._t)}</div>
      </ha-card>`;

    const charge = this.shadowRoot.getElementById("ocpp-charge-btn");
    if (charge) charge.addEventListener("click", () => this._toggle("charging_allowed"));
    const now = this.shadowRoot.getElementById("ocpp-now-btn");
    if (now) now.addEventListener("click", () => this._toggle("charge_now"));
    this._rendered = true;
  }

  _update() {
    const root = this.shadowRoot;
    const set = (id, txt) => {
      const el = root.getElementById(id);
      if (el) el.textContent = txt;
    };
    this._wb.updateWallbox(this, root, set);
  }

  /** Inverte uno dei due switch. Lo stato torna dal coordinator, non lo finge. */
  async _toggle(key) {
    const id = this._meta && this._meta.entities && this._meta.entities[key];
    if (!id) return;
    const st = this._hass.states[id];
    const on = st ? st.state === "on" : false;
    await this._hass.callService("switch", on ? "turn_off" : "turn_on", {
      entity_id: id,
    });
  }
}

// Un secondo caricamento del modulo (due dashboard, o un reload a caldo) non
// deve far esplodere il registro dei custom element.
if (!customElements.get(CARD_NAME)) {
  customElements.define(CARD_NAME, EVBalanceCard);
}

// Fa comparire la card nel selettore visuale delle dashboard.
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD_NAME)) {
  window.customCards.push({
    type: CARD_NAME,
    name: "EV Balance",
    description:
      "Wallbox OCPP: stato, corrente, sessione, con stop/ripresa e ricarica ora.",
    preview: false,
    documentationURL: "https://github.com/matteodallefeste/ha_evbalance",
  });
}

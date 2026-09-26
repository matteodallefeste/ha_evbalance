/**
 * Controlli automatici del pannello EV Balance, senza browser.
 *
 * Monta un DOM minimale e verifica la card OCPP del tab Live, i campi che il
 * tab Impostazioni mostra in ciascuna modalita' di controllo, la lettura del
 * form e la parita' delle chiavi di traduzione tra le lingue.
 *
 * Uso:  node tools/check_panel.mjs
 */
import assert from "node:assert/strict";

// --- DOM minimale ------------------------------------------------------
class FakeEl {
  constructor(id) {
    this.id = id;
    this.textContent = "";
    this.className = "";
    this.style = {};
    this.value = "";
    this.checked = false;
  }
  addEventListener() {}
}
globalThis.HTMLElement = class {};

// Registro fedele al browser: un secondo define dello stesso nome solleva.
const registry = new Map();
globalThis.customElements = {
  define(name, ctor) {
    if (registry.has(name)) {
      throw new Error(
        `Failed to execute 'define' on 'CustomElementRegistry': the name "${name}" has already been used with this registry`
      );
    }
    registry.set(name, ctor);
  },
  get: (name) => registry.get(name),
};
globalThis.document = { activeElement: null };
globalThis.location = { hostname: "192.168.1.50", port: "8123" };
// La card Lovelace si annuncia in window.customCards.
globalThis.window = globalThis;

const { EVBalancePanel, ready } = await import("../custom_components/evbalance/www/evbalance-panel.js");
const { TR } = await import("../custom_components/evbalance/www/evbalance-translations.js");
// La card della wallbox vive in un modulo importato a runtime: il pannello la
// carica in _init(), che qui non passiamo. `ready()` fa la stessa cosa.
await ready();

function makePanel({ mode = "ocpp", config = {}, states = {}, entities = {}, meta = {} } = {}) {
  const p = Object.create(EVBalancePanel.prototype);
  p._tr = TR;   // _t e' un getter: si pilota dalla lingua di hass
  p._meta = { control_mode: mode, entities, max_power_w: 3300, currency: "€", ...meta };
  p._config = { control_mode: mode, name: "EV Balance", ...config };
  p._hass = { language: "it", states };
  p._canEdit = true;
  return p;
}

// --- URL da configurare nella wallbox ---------------------------------
{
  const p = makePanel({ config: { ocpp_port: 9000, ocpp_cp_id: "PULSAR" } });
  assert.equal(p._ocppUrl(), "ws://192.168.1.50:9000/PULSAR");

  const vuoto = makePanel({ config: { ocpp_port: 9000, ocpp_cp_id: "" } });
  assert.equal(vuoto._ocppUrl(), "ws://192.168.1.50:9000/EVBALANCE");

  const haPort = makePanel({ config: { ocpp_use_ha_port: true, ocpp_cp_id: "X" } });
  assert.equal(haPort._ocppUrl(), "ws://192.168.1.50:8123/api/evbalance/ocpp/X");
  console.log("ok  URL OCPP costruito dall'host in uso");
}

// --- La card compare solo in modalità OCPP ----------------------------
{
  assert.equal(makePanel({ mode: "entities" })._ocppCard(), "");
  const html = makePanel()._ocppCard();
  assert.match(html, /id="ocpp-card"/);
  assert.match(html, /v-ocpp-status/);
  assert.match(html, /Wallbox \(OCPP\)/);
  console.log("ok  card OCPP presente solo in modalità OCPP");
}

// --- Aggiornamento live della card ------------------------------------
{
  const ids = {
    ocpp_connected: "binary_sensor.ocpp_connected",
    ocpp_status: "sensor.ocpp_status",
    ocpp_session_energy: "sensor.ocpp_session",
  };
  const states = {
    [ids.ocpp_connected]: { state: "on", attributes: {} },
    [ids.ocpp_status]: {
      state: "SuspendedEVSE",
      attributes: {
        charge_point_id: "PULSAR",
        vendor: "Wallbox",
        model: "Pulsar Max",
        firmware: "6.1.2",
        requested_limit_a: 0,
        current_offered_a: 0,
        limit_confirmed: true,
        currents_a: { L1: 0.1 },
        soc: 62,
        supports_smart_charging: true,
      },
    },
    [ids.ocpp_session_energy]: { state: "5.5", attributes: {} },
  };
  const p = makePanel({ states, entities: ids });

  const els = {};
  const root = {
    getElementById: (id) => (els[id] = els[id] || new FakeEl(id)),
  };
  els["ocpp-card"] = new FakeEl("ocpp-card");
  const set = (id, txt) => {
    root.getElementById(id).textContent = txt;
  };

  p._updateOcpp(root, set);

  assert.equal(els["v-ocpp-link"].textContent, "Connessa");
  assert.equal(els["v-ocpp-status"].textContent, "In pausa (limite 0 A)");
  assert.equal(els["v-ocpp-status"].className, "badge paused");
  assert.equal(els["v-ocpp-req"].textContent, "0 A");
  assert.equal(els["v-ocpp-soc"].textContent, "62 %");
  assert.equal(els["v-ocpp-phases"].textContent, "L1 0.1 A");
  assert.match(els["v-ocpp-device"].textContent, /Wallbox Pulsar Max · fw 6\.1\.2 · PULSAR/);
  assert.equal(els["v-ocpp-warn"].style.display, "none");
  console.log("ok  stato SuspendedEVSE reso come 'in pausa', nessun avviso");

  // Limite non rispettato -> avviso visibile con la spiegazione.
  states[ids.ocpp_status].attributes.limit_confirmed = false;
  states[ids.ocpp_status].attributes.limit_error = "la wallbox ne offre 32.0";
  p._updateOcpp(root, set);
  assert.notEqual(els["v-ocpp-warn"].style.display, "none");
  assert.match(els["v-ocpp-warn"].textContent, /non sta applicando.*32\.0/);
  console.log("ok  avviso quando la wallbox ignora il limite");

  // Wallbox scollegata -> l'avviso diventa l'indirizzo da configurare.
  states[ids.ocpp_connected].state = "off";
  p._updateOcpp(root, set);
  assert.match(els["v-ocpp-warn"].textContent, /ws:\/\/192\.168\.1\.50:9000\//);
  assert.equal(els["v-ocpp-link"].textContent, "In attesa della wallbox");
  console.log("ok  wallbox assente: il pannello mostra l'URL da configurare");
}

// --- Campi impostazioni per modalità ----------------------------------
{
  const t = TR.it;
  const ocpp = makePanel({ config: { ocpp_port: 9000 } });
  const html = ocpp._chargerFields(ocpp._config, t);
  assert.match(html, /cfg-ocpp_port/);
  assert.match(html, /cfg-ocpp_cp_id/);
  assert.match(html, /cfg-ocpp_use_ha_port/);
  assert.doesNotMatch(html, /cfg-ev_charger_current_entity/);
  assert.doesNotMatch(html, /cfg-ev_charger_switch_entity/);
  assert.doesNotMatch(html, /cfg-pause_current/, "la corrente di pausa non ha senso in OCPP");

  const ent = makePanel({ mode: "entities" });
  ent._powerSensors = () => ["sensor.p"];
  ent._numberEntities = () => ["number.n"];
  ent._switchEntities = () => ["switch.s"];
  ent._friendly = (id) => id;
  const html2 = ent._chargerFields(ent._config, t);
  assert.match(html2, /cfg-ev_charger_current_entity/);
  assert.match(html2, /cfg-pause_current/);
  assert.doesNotMatch(html2, /cfg-ocpp_port/);
  console.log("ok  ogni modalità mostra solo i propri campi");
}

// --- Selettore di modalità e guida ------------------------------------
{
  const t = TR.it;
  const ocpp = makePanel();
  const sel = ocpp._modeField(t);
  assert.match(sel, /id="cfg-control_mode"/);
  assert.match(sel, /value="ocpp" selected/);
  assert.match(sel, /Cambiando modalità/);

  const ent = makePanel({ mode: "entities" });
  assert.match(ent._modeField(t), /value="entities" selected/);
  assert.doesNotMatch(ent._modeField(t), /value="ocpp" selected/);
  console.log("ok  selettore di modalità con la voce giusta preselezionata");

  // La guida compare solo in OCPP, con tutti i passi della lingua attiva.
  const guida = ocpp._chargerFields(ocpp._config, t);
  assert.match(guida, /<details class="ocpp-guide/);
  for (const step of t.ocppGuide) {
    assert.ok(guida.includes(ocpp._esc(step)), `passo mancante: ${step.slice(0, 30)}`);
  }
  assert.ok(guida.includes(t.ocppGuideNote));
  assert.doesNotMatch(ent._chargerFields(ent._config, t), /ocpp-guide/);
  console.log(`ok  guida OCPP con ${t.ocppGuide.length} passi, solo in modalità OCPP`);
}

// --- Lettura del form --------------------------------------------------
{
  const p = makePanel();
  const values = {
    "cfg-name": "EV Balance",
    "cfg-control_mode": "ocpp",
    "cfg-max_power_w": "3300",
    "cfg-voltage": "230",
    "cfg-phases": "1",
    "cfg-min_current": "6",
    "cfg-max_current": "16",
    "cfg-safety_margin_w": "200",
    "cfg-hold_seconds": "300",
    "cfg-update_interval": "3",
    "cfg-tariff_preset": "it_arera",
    "cfg-currency": "€",
    "cfg-ocpp_port": "9000",
    "cfg-ocpp_cp_id": "  PULSAR  ",
    "cfg-ocpp_password": "segreto",
    "cfg-ocpp_connector": "1",
    "cfg-ocpp_meter_interval": "10",
    "cfg-ev_charger_power_entity": "",
  };
  const boxes = { "cfg-ocpp_use_ha_port": true, "cfg-show_panel": true };
  p.shadowRoot = {
    getElementById: (id) => {
      if (id in values) {
        const el = new FakeEl(id);
        el.value = values[id];
        return el;
      }
      if (id in boxes) {
        const el = new FakeEl(id);
        el.checked = boxes[id];
        return el;
      }
      return null; // campi dell'altra modalità: assenti dal DOM
    },
    querySelectorAll: () => [],
  };
  p._readPrices = () => ({});

  const cfg = p._readForm();
  assert.equal(cfg.control_mode, "ocpp");
  assert.equal(cfg.ocpp_port, 9000);
  assert.equal(cfg.ocpp_cp_id, "PULSAR", "il Charge Point ID va ripulito dagli spazi");
  assert.equal(cfg.ocpp_use_ha_port, true);
  assert.equal(cfg.show_panel, true);
  // Niente chiavi dell'altra modalità: non devono sovrascrivere la config.
  assert.ok(!("ev_charger_current_entity" in cfg));
  assert.ok(!("pause_current" in cfg));
  assert.equal(cfg.min_current, 6);
  console.log("ok  il form legge solo i campi della modalità attiva");
}

// --- Parità delle chiavi di traduzione --------------------------------
{
  const base = Object.keys(TR.en).sort();
  for (const lang of Object.keys(TR)) {
    const keys = Object.keys(TR[lang]).sort();
    assert.deepEqual(keys, base, `chiavi diverse per ${lang}`);
    for (const s of Object.keys(TR.en.ocppStates)) {
      assert.ok(TR[lang].ocppStates[s], `stato ${s} mancante in ${lang}`);
    }
  }
  console.log(`ok  ${base.length} chiavi presenti in tutte le lingue: ${Object.keys(TR).join(", ")}`);
}

// --- Doppio caricamento del modulo ------------------------------------
{
  // Il token anti-cache cambia a ogni aggiornamento: nella stessa pagina il
  // browser puo' ritrovarsi due istanze del modulo, e senza guardia il secondo
  // define fa esplodere il registro dei custom element.
  assert.ok(customElements.get("evbalance-panel"), "il pannello non si e' registrato");
  await import("../custom_components/evbalance/www/evbalance-panel.js?v=secondo");
  assert.equal(registry.size, 1, "il pannello si e' registrato due volte");
  console.log("ok  un secondo caricamento del modulo non rompe il registro");
}

// --- Anteprima dell'indirizzo nelle Impostazioni ----------------------
{
  // Deve riflettere quello che stai per salvare, non lo stato di quando la
  // pagina e' stata disegnata: altrimenti Impostazioni e tab Live mostrano
  // due indirizzi diversi e non si capisce quale valga.
  const p = makePanel({ config: { ocpp_port: 9000, ocpp_cp_id: "" } });
  const campi = {
    "cfg-ocpp_port": { value: "9100" },
    "cfg-ocpp_cp_id": { value: "wallbox" },
    "cfg-ocpp_use_ha_port": { checked: false },
    "ocpp-url-preview": new FakeEl("ocpp-url-preview"),
  };
  p.shadowRoot = { getElementById: (id) => campi[id] || null };

  p._refreshOcppUrl();
  assert.equal(
    campi["ocpp-url-preview"].textContent,
    "ws://192.168.1.50:9100/wallbox",
    "l'anteprima non segue i campi del form"
  );

  // Con l'ID svuotato torna al segnaposto, senza dover ridisegnare la pagina.
  campi["cfg-ocpp_cp_id"].value = "";
  p._refreshOcppUrl();
  assert.equal(campi["ocpp-url-preview"].textContent, "ws://192.168.1.50:9100/EVBALANCE");
  console.log("ok  l'indirizzo nelle Impostazioni segue i campi in tempo reale");
}

// --- L'indirizzo proposto deve essere raggiungibile dalla wallbox -----
{
  // Guardando il pannello da fuori casa, location.hostname e' il nome
  // pubblico: la wallbox, sulla LAN, non ci arriva. Deve vincere l'IP locale
  // di Home Assistant, che il backend conosce.
  const fuori = makePanel({
    config: { ocpp_port: 9000, ocpp_cp_id: "wallbox" },
    meta: { local_ip: "192.168.123.230" },
  });
  assert.equal(fuori._ocppUrl(), "ws://192.168.123.230:9000/wallbox");

  // Senza IP locale (backend più vecchio) si ripiega sull'host del browser.
  const ripiego = makePanel({ config: { ocpp_port: 9000, ocpp_cp_id: "wallbox" } });
  assert.equal(ripiego._ocppUrl(), "ws://192.168.1.50:9000/wallbox");
  console.log("ok  l'indirizzo proposto usa l'IP LAN di Home Assistant");
}

// --- Bottone ferma/riprendi ricarica ----------------------------------
{
  const ids = {
    ocpp_connected: "binary_sensor.link",
    ocpp_status: "sensor.stato",
    charging_allowed: "switch.ricarica",
  };
  const states = {
    [ids.ocpp_connected]: { state: "on", attributes: {} },
    [ids.ocpp_status]: { state: "Charging", attributes: { limit_confirmed: true } },
    [ids.charging_allowed]: { state: "on", attributes: {} },
  };
  const p = makePanel({ states, entities: ids });
  const els = { "ocpp-card": new FakeEl("ocpp-card") };
  const root = { getElementById: (id) => (els[id] = els[id] || new FakeEl(id)) };
  const set = (id, txt) => { root.getElementById(id).textContent = txt; };

  p._updateOcpp(root, set);
  assert.equal(els["ocpp-charge-btn"].hidden, false);
  assert.equal(els["ocpp-charge-btn"].textContent, "Ferma la ricarica");
  assert.equal(els["v-ocpp-status"].textContent, "In carica");

  // Fermata a mano: il bottone si inverte e lo stato lo dice.
  states[ids.charging_allowed].state = "off";
  p._updateOcpp(root, set);
  assert.equal(els["ocpp-charge-btn"].textContent, "Riprendi la ricarica");
  assert.match(els["ocpp-charge-btn"].className, /resume/);
  assert.equal(els["v-ocpp-status"].textContent, "Fermata a mano");
  assert.equal(els["v-ocpp-status"].className, "badge paused");

  // Senza l'entità (backend più vecchio) il bottone resta nascosto.
  const senza = makePanel({ states, entities: { ocpp_status: ids.ocpp_status } });
  const els2 = { "ocpp-card": new FakeEl("ocpp-card") };
  const root2 = { getElementById: (id) => (els2[id] = els2[id] || new FakeEl(id)) };
  senza._updateOcpp(root2, (id, txt) => { root2.getElementById(id).textContent = txt; });
  assert.equal(els2["ocpp-charge-btn"].hidden, true);
  console.log("ok  bottone ferma/riprendi coerente con lo switch");
}

// --- Bottone "ricarica ora" -------------------------------------------
{
  const ids = {
    ocpp_connected: "binary_sensor.link",
    ocpp_status: "sensor.stato",
    charging_allowed: "switch.ricarica",
    charge_now: "switch.ora",
  };
  const states = {
    [ids.ocpp_connected]: { state: "on", attributes: {} },
    [ids.ocpp_status]: { state: "SuspendedEVSE", attributes: { limit_confirmed: true } },
    [ids.charging_allowed]: { state: "on", attributes: {} },
    [ids.charge_now]: { state: "off", attributes: {} },
  };
  const p = makePanel({ states, entities: ids });
  const els = { "ocpp-card": new FakeEl("ocpp-card") };
  const root = { getElementById: (id) => (els[id] = els[id] || new FakeEl(id)) };
  const set = (id, txt) => { root.getElementById(id).textContent = txt; };

  p._updateOcpp(root, set);
  assert.equal(els["ocpp-now-btn"].hidden, false);
  assert.equal(els["ocpp-now-btn"].textContent, "Ricarica ora");
  assert.match(els["ocpp-now-btn"].className, /now/);
  assert.equal(els["ocpp-now-btn"].disabled, false);

  // Attiva: il bottone passa ad annullarla e perde il colore da "avvia".
  states[ids.charge_now].state = "on";
  p._updateOcpp(root, set);
  assert.equal(els["ocpp-now-btn"].textContent, "Annulla ricarica ora");
  assert.doesNotMatch(els["ocpp-now-btn"].className, /now/);
  assert.equal(els["ocpp-now-btn"].disabled, false);

  // Ferma a mano: accenderla non avvierebbe nulla, quindi è disabilitata.
  // Annullarla invece resta possibile.
  states[ids.charging_allowed].state = "off";
  states[ids.charge_now].state = "off";
  p._updateOcpp(root, set);
  assert.equal(els["ocpp-now-btn"].disabled, true);
  assert.equal(els["ocpp-now-btn"].title, "Fermata a mano");
  states[ids.charge_now].state = "on";
  p._updateOcpp(root, set);
  assert.equal(els["ocpp-now-btn"].disabled, false);

  // Controllo per entità: l'entità non esiste e il bottone resta nascosto.
  const senza = makePanel({ states, entities: { ocpp_status: ids.ocpp_status } });
  const els2 = { "ocpp-card": new FakeEl("ocpp-card") };
  const root2 = { getElementById: (id) => (els2[id] = els2[id] || new FakeEl(id)) };
  senza._updateOcpp(root2, (id, txt) => { root2.getElementById(id).textContent = txt; });
  assert.equal(els2["ocpp-now-btn"].hidden, true);
  console.log("ok  bottone \"ricarica ora\" coerente con lo switch");
}

// --- Fasce ammesse alla ricarica --------------------------------------
{
  const t = TR.it;
  const p = makePanel({
    config: { allowed_bands: ["F3"] },
    meta: { bands: ["F1", "F2", "F3"], band_meta: { F3: { label: "Fuori picco" } } },
  });
  const html = p._fieldAllowedBands(t);
  assert.match(html, /class="band-cb" value="F1"/);
  assert.match(html, /value="F3" checked/, "la fascia scelta deve risultare spuntata");
  assert.doesNotMatch(html, /value="F1" checked/);
  assert.ok(html.includes("F3 · Fuori picco"), "manca l'etichetta della fascia");

  // Senza fasce note (metadati non ancora caricati) non si disegna nulla.
  assert.equal(makePanel()._fieldAllowedBands(t), "");
  console.log("ok  selezione delle fasce costruita dallo schema attivo");
}

// --- Il badge distingue il motivo della fermata ------------------------
{
  const ids = {
    active_band: "sensor.fascia",
    ev_charger_power: "sensor.potenza",
    charging_blocked: "binary_sensor.bloccata",
    charging_allowed: "switch.ricarica",
  };
  const states = {
    [ids.active_band]: { state: "F1", attributes: { allowed: false } },
    [ids.ev_charger_power]: { state: "0", attributes: {} },
    [ids.charging_blocked]: { state: "off", attributes: {} },
    [ids.charging_allowed]: { state: "on", attributes: {} },
  };
  const p = makePanel({ mode: "entities", states, entities: ids });
  const els = {};
  p.shadowRoot = { getElementById: (id) => (els[id] = els[id] || new FakeEl(id)) };
  p._meta.max_power_w = 3300;

  p._updateLive();
  assert.equal(els["v-charge"].textContent, "Fuori fascia");

  // Lo stop manuale ha la precedenza: e' il motivo piu' specifico.
  states[ids.charging_allowed].state = "off";
  p._updateLive();
  assert.equal(els["v-charge"].textContent, "Fermata a mano");

  // Rientrati in fascia e riattivata: torna la lettura normale.
  states[ids.charging_allowed].state = "on";
  states[ids.active_band].attributes.allowed = true;
  p._updateLive();
  assert.equal(els["v-charge"].textContent, "Ferma");
  console.log("ok  il badge dice perché la ricarica è ferma");
}

// --- Card Lovelace ----------------------------------------------------
const { EVBalanceCard } = await import("../custom_components/evbalance/www/evbalance-card.js");
const WB = await import("../custom_components/evbalance/www/evbalance-wallbox.js");

function makeCard({ mode = "ocpp", states = {}, entities = {}, config = {} } = {}) {
  const c = Object.create(EVBalanceCard.prototype);
  c._tr = TR;
  c._wb = WB;
  c._meta = { control_mode: mode, entities, local_ip: "192.168.1.50" };
  c._config = { control_mode: mode, ocpp_port: 9000, ocpp_cp_id: "PULSAR", ...config };
  c._hass = { language: "it", states };
  const els = {};
  c.shadowRoot = {
    innerHTML: "",
    getElementById: (id) => (els[id] = els[id] || new FakeEl(id)),
  };
  return { c, els };
}

{
  // Si registra col nome giusto e si annuncia nel selettore delle card.
  assert.ok(customElements.get("evbalance-card"), "custom element non registrato");
  const entry = (window.customCards || []).find((x) => x.type === "evbalance-card");
  assert.ok(entry, "card assente da window.customCards");
  assert.equal(entry.name, "EV Balance");
  console.log("ok  card registrata come custom:evbalance-card");
}

{
  // Un secondo caricamento del modulo non duplica né la define né l'annuncio.
  const before = window.customCards.length;
  await import("../custom_components/evbalance/www/evbalance-card.js?again=1");
  assert.equal(
    window.customCards.filter((x) => x.type === "evbalance-card").length,
    1
  );
  assert.equal(window.customCards.length, before);
  console.log("ok  ricaricare il modulo della card non duplica nulla");
}

{
  const { c } = makeCard();
  c.setConfig({ type: "custom:evbalance-card" });
  assert.equal(typeof c.getCardSize(), "number");
  c._render();
  // Involucro nativo di Lovelace, e dentro lo stesso markup del pannello.
  assert.match(c.shadowRoot.innerHTML, /<ha-card>/);
  assert.match(c.shadowRoot.innerHTML, /id="ocpp-card"/);
  assert.match(c.shadowRoot.innerHTML, /v-ocpp-status/);
  assert.match(c.shadowRoot.innerHTML, /ocpp-now-btn/);
  assert.match(c.shadowRoot.innerHTML, /Wallbox \(OCPP\)/);
  console.log("ok  card resa dentro ha-card con il markup condiviso");
}

{
  // Gli stessi stati del pannello devono dare la stessa lettura.
  const ids = {
    ocpp_connected: "binary_sensor.link",
    ocpp_status: "sensor.stato",
    ocpp_session_energy: "sensor.sessione",
    charging_allowed: "switch.ricarica",
    charge_now: "switch.ora",
  };
  const states = {
    [ids.ocpp_connected]: { state: "on", attributes: {} },
    [ids.ocpp_status]: {
      state: "Charging",
      attributes: { requested_limit_a: 10, current_offered_a: 10, soc: 62,
        currents_a: { L1: 9.8 }, limit_confirmed: true },
    },
    [ids.ocpp_session_energy]: { state: "5.5", attributes: {} },
    [ids.charging_allowed]: { state: "on", attributes: {} },
    [ids.charge_now]: { state: "off", attributes: {} },
  };
  const { c, els } = makeCard({ states, entities: ids });
  c._render();
  c._update();

  assert.equal(els["v-ocpp-link"].textContent, "Connessa");
  assert.equal(els["v-ocpp-status"].textContent, "In carica");
  assert.equal(els["v-ocpp-req"].textContent, "10 A");
  assert.equal(els["v-ocpp-session"].textContent, "5,5 kWh");
  assert.equal(els["v-ocpp-soc"].textContent, "62 %");
  assert.equal(els["v-ocpp-phases"].textContent, "L1 9.8 A");
  assert.equal(els["ocpp-charge-btn"].textContent, "Ferma la ricarica");
  assert.equal(els["ocpp-now-btn"].textContent, "Ricarica ora");

  // Wallbox scollegata: anche in dashboard si vede l'indirizzo da configurare.
  states[ids.ocpp_connected].state = "off";
  c._update();
  assert.match(els["v-ocpp-warn"].textContent, /ws:\/\/192\.168\.1\.50:9000\/PULSAR/);
  console.log("ok  card aggiornata come la card del pannello");
}

{
  // Controllo per entità: la card lo dice invece di restare vuota.
  const { c } = makeCard({ mode: "entities" });
  assert.equal(c._isOcpp, false);
  c._fail(c._t.cardOnlyOcpp);
  assert.match(c.shadowRoot.innerHTML, /richiede il controllo OCPP/);
  console.log("ok  card fuori modalità OCPP spiega perché non funziona");
}

console.log("\nTutti i controlli del pannello sono passati.");

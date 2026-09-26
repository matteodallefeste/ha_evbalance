/**
 * Card della wallbox (OCPP), condivisa tra il pannello e la card Lovelace.
 *
 * Qui vive tutta la logica: il markup e la traduzione stato -> UI. I due
 * contenitori la riusano cosi' com'e', percio' non possono divergere: il
 * pannello la incastona in una `<section class="card">`, la card Lovelace in
 * una `<ha-card>`. Entrambi devono dare al markup un elemento con
 * `id="ocpp-card"`, che `updateWallbox` usa per capire se la card c'e'.
 *
 * Il modulo viene importato dinamicamente propagando il token `?v=` del file
 * che lo carica (vedi `ready()` nel pannello e nella card): la cartella www/ e'
 * servita con cache lunga, quindi un import statico resterebbe fermo alla
 * versione vecchia dopo un aggiornamento.
 */

/**
 * Markup interno della card. Il contenitore (con `id="ocpp-card"`) lo mette
 * chi chiama, perche' cambia tra pannello e dashboard.
 */
export function wallboxMarkup(t) {
  return `
    <div class="chart-head">
      <h2>${t.ocppTitle}</h2>
      <span class="badge idle" id="v-ocpp-link">—</span>
      <button id="ocpp-now-btn" class="charge-btn now" hidden></button>
      <button id="ocpp-charge-btn" class="charge-btn" hidden></button>
    </div>
    <div class="tiles">
      <div class="tile">
        <span class="k">${t.ocppConnStatus}</span>
        <span class="badge idle" id="v-ocpp-status">—</span>
      </div>
      <div class="tile"><span class="k">${t.ocppRequested}</span>
        <span class="val" id="v-ocpp-req">—</span></div>
      <div class="tile"><span class="k">${t.ocppOffered}</span>
        <span class="val" id="v-ocpp-off">—</span></div>
      <div class="tile"><span class="k">${t.ocppSession}</span>
        <span class="val" id="v-ocpp-session">—</span></div>
      <div class="tile"><span class="k">${t.ocppSoc}</span>
        <span class="val" id="v-ocpp-soc">—</span></div>
      <div class="tile"><span class="k">${t.ocppPhases}</span>
        <span class="val phases" id="v-ocpp-phases">—</span></div>
    </div>
    <div class="hint" id="v-ocpp-device"></div>
    <div class="ocpp-warn" id="v-ocpp-warn" style="display:none"></div>`;
}

/**
 * Indirizzo da configurare nella wallbox.
 *
 * L'host e' l'IP LAN di Home Assistant, non quello con cui stiamo navigando:
 * guardando il pannello da fuori casa `location.hostname` e' un indirizzo che
 * la wallbox, attaccata alla rete di casa, non raggiunge. Resta come ripiego.
 */
export function ocppUrl(config, localIp) {
  const c = config || {};
  const host = localIp || location.hostname;
  const id = (c.ocpp_cp_id || "").trim() || "EVBALANCE";
  if (c.ocpp_use_ha_port) {
    return `ws://${host}:${location.port || 8123}/api/evbalance/ocpp/${id}`;
  }
  return `ws://${host}:${c.ocpp_port || 9000}/${id}`;
}

/**
 * Riversa lo stato corrente nella card.
 *
 * `host` e' il pannello o la card: deve offrire `_t`, `_stateOf`, `_numState`,
 * `_fmtEnergy` e `_ocppUrl`. `set(id, testo)` scrive il testo di un elemento.
 */
export function updateWallbox(host, root, set) {
  const card = root.getElementById("ocpp-card");
  if (!card) return;
  const t = host._t;

  const linkSt = host._stateOf("ocpp_connected");
  const linked = linkSt ? linkSt.state === "on" : null;
  const link = root.getElementById("v-ocpp-link");
  if (link) {
    link.textContent = linked == null ? "—" : linked ? t.ocppConnected : t.ocppWaiting;
    link.className = "badge " + (linked ? "charging" : "idle");
  }

  const allowSt = host._stateOf("charging_allowed");
  const st = host._stateOf("ocpp_status");
  const a = (st && st.attributes) || {};
  const raw = st && st.state !== "unknown" && st.state !== "unavailable" ? st.state : null;

  const status = root.getElementById("v-ocpp-status");
  if (status) {
    const states = t.ocppStates || {};
    const stopped = allowSt && allowSt.state === "off";
    // Lo stop manuale spiega il perché di uno stato altrimenti ambiguo.
    status.textContent = stopped
      ? t.ocppStopped
      : raw
        ? states[raw] || raw
        : "—";
    let cls = stopped ? "badge paused" : "badge idle";
    if (stopped) cls = "badge paused";
    else if (raw === "Charging") cls = "badge charging";
    else if (raw === "SuspendedEVSE" || raw === "SuspendedEV") cls = "badge paused";
    else if (raw === "Faulted") cls = "badge err";
    status.className = cls;
  }

  const amps = (v) => (v == null ? "—" : `${v} A`);
  set("v-ocpp-req", amps(a.requested_limit_a));
  set("v-ocpp-off", amps(a.current_offered_a));

  const session = host._numState("ocpp_session_energy");
  set("v-ocpp-session", session == null ? "—" : host._fmtEnergy(session));
  set("v-ocpp-soc", a.soc == null ? "—" : `${a.soc} %`);

  const phases = a.currents_a || {};
  const names = Object.keys(phases).sort();
  set(
    "v-ocpp-phases",
    names.length ? names.map((n) => `${n} ${phases[n]} A`).join(" · ") : "—"
  );

  // Riga identificativa: chi è, che firmware ha, con che ID si presenta.
  const parts = [a.vendor, a.model].filter(Boolean).join(" ");
  const bits = [];
  if (parts) bits.push(parts);
  if (a.firmware) bits.push(`fw ${a.firmware}`);
  if (a.charge_point_id) bits.push(a.charge_point_id);
  set("v-ocpp-device", bits.length ? `${t.ocppDevice}: ${bits.join(" · ")}` : "");

  // Bottone di stop/ripresa: rispecchia lo switch "Ricarica consentita".
  const btn = root.getElementById("ocpp-charge-btn");
  if (btn) {
    const allowed = allowSt ? allowSt.state === "on" : null;
    btn.hidden = allowed == null;
    if (allowed != null) {
      btn.textContent = allowed ? t.ocppStop : t.ocppStart;
      btn.className = "charge-btn" + (allowed ? "" : " resume");
    }
  }

  // "Ricarica ora": scavalca le fasce fino a fine sessione. Manca del tutto
  // col controllo per entità, dove non esiste l'entità corrispondente.
  const nowBtn = root.getElementById("ocpp-now-btn");
  if (nowBtn) {
    const nowSt = host._stateOf("charge_now");
    const on = nowSt ? nowSt.state === "on" : null;
    nowBtn.hidden = on == null;
    if (on != null) {
      nowBtn.textContent = on ? t.ocppChargeNowStop : t.ocppChargeNow;
      nowBtn.className = "charge-btn" + (on ? "" : " now");
      // Con la ricarica ferma a mano non avvierebbe nulla: lo stop vince.
      // Annullarla resta invece sempre possibile.
      const stopped = !!allowSt && allowSt.state === "off" && !on;
      nowBtn.disabled = stopped;
      nowBtn.title = stopped ? t.ocppStopped : "";
    }
  }

  // Un solo avviso per volta, dal più urgente.
  const warn = root.getElementById("v-ocpp-warn");
  if (!warn) return;
  let message = "";
  if (linked === false) {
    message = `${t.ocppUrlHint} ${host._ocppUrl()}`;
  } else if (a.limit_confirmed === false) {
    message = a.limit_error
      ? `${t.ocppLimitWarn} — ${a.limit_error}`
      : t.ocppLimitWarn;
  } else if (a.supports_smart_charging === false) {
    message = t.ocppNoSmart;
  }
  warn.textContent = message;
  warn.style.display = message ? "" : "none";
}

/**
 * Stile della card per la dashboard.
 *
 * Il pannello ha il proprio foglio di stile e non usa questo: qui i colori
 * arrivano dalle variabili del tema di Home Assistant, così la card segue il
 * tema dell'utente invece di imporre quello del pannello.
 */
export const WALLBOX_CSS = `
  .wb { padding:16px; }
  .chart-head { display:flex; align-items:center; gap:8px; margin-bottom:12px; }
  .chart-head h2 { margin:0; font-size:16px; font-weight:600;
    color:var(--primary-text-color); }
  .tiles { display:grid; gap:10px;
    grid-template-columns:repeat(auto-fit, minmax(120px, 1fr)); }
  .tile { display:flex; flex-direction:column; gap:4px; padding:10px 12px;
    border-radius:10px; background:var(--secondary-background-color); }
  .tile .k { font-size:12px; color:var(--secondary-text-color); }
  .tile .val { font-size:18px; font-weight:600;
    color:var(--primary-text-color); }
  .tile .val.phases { font-size:14px; font-weight:500; line-height:1.4; }
  .badge { align-self:flex-start; padding:2px 10px; border-radius:999px;
    font-size:12px; font-weight:600; background:rgba(127,127,127,.18);
    color:var(--primary-text-color); }
  .badge.charging { background:rgba(34,199,139,.18); color:#22c78b; }
  .badge.paused { background:rgba(245,158,11,.18); color:#f59e0b; }
  .badge.err { background:rgba(239,68,68,.18); color:#ef4444; }
  .charge-btn { margin-left:auto; padding:6px 14px; border:0; border-radius:999px;
    font-size:13px; font-weight:600; cursor:pointer; color:#fff;
    background:#f59e0b; }
  .charge-btn.resume { background:#22c78b; }
  .charge-btn.now { background:#6366f1; }
  .charge-btn + .charge-btn { margin-left:8px; }
  .charge-btn:disabled { opacity:.6; cursor:default; }
  .hint { margin-top:10px; font-size:12px; color:var(--secondary-text-color); }
  .hint:empty { display:none; }
  .ocpp-warn { margin-top:10px; padding:8px 12px; border-radius:8px;
    font-size:13px; background:rgba(239,68,68,.12); color:#ef4444;
    border:1px solid rgba(239,68,68,.35); }
`;

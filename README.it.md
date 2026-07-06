<p align="center">
  <img src="brand/logo.png" alt="EV Balance" width="360">
</p>

<p align="center">
  <a href="README.md">English</a> · <b>Italiano</b> · <a href="README.de.md">Deutsch</a> · <a href="README.fr.md">Français</a>
</p>

# EV Balance — Load balancer energetico per Home Assistant

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

Integrazione custom (installabile via **HACS**) che evita il distacco del
contatore per sovraccarico modulando la corrente della EV Charger in base ai
consumi di casa, e che tiene traccia dell'energia per **fasce orarie** (ARERA
F1/F2/F3) con reset giornaliero e mensile.

## Come funziona

Ad ogni ciclo (default ogni 3 s) l'integrazione:

1. legge la **potenza istantanea** della EV Charger e delle sorgenti configurate;
2. calcola il budget disponibile:
   `budget = limite_contatore − margine_sicurezza − consumi_sorgenti`;
3. converte il budget in Ampere (in base a tensione e n° fasi) e lo scrive
   sulla **number entity** della EV Charger;
4. se i consumi non-EV Charger superano il limite, mette la EV Charger **in pausa**.

### Isteresi (anti-flapping)

- **Riduzione / pausa → immediata** (sicurezza).
- **Aumento → consentito solo dopo `hold_seconds`** (default 300 s = 5 min)
  dall'ultima variazione. Così il valore non viene modificato di continuo.

## Installazione (HACS)

1. HACS → *Integrations* → menu ⋮ → **Custom repositories**.
2. Aggiungi l'URL di questo repository, categoria **Integration**.
3. Installa **EV Balance** e riavvia Home Assistant.
4. *Impostazioni → Dispositivi e servizi → Aggiungi integrazione → EV Balance*.

> In alternativa, copia la cartella `custom_components/evbalance/` dentro la
> tua cartella `config/custom_components/` e riavvia.

## Configurazione

**Setup iniziale (strutturale, impostato alla prima configurazione):**

| Parametro | Default | A cosa serve |
|---|---|---|
| Nome | EV Balance | Nome dell'istanza dell'integrazione |
| Sensore potenza EV Charger | — | `sensor.*` (device_class power) in W/kW con la potenza attuale della EV Charger |
| Number corrente EV Charger | — | Entità `number.*` su cui il balancer scrive gli Ampere massimi |
| Sorgenti di consumo | (nessuna) | Sensori di potenza del resto della casa, sottratti dal budget (multi-select) |
| La sorgente include la EV Charger | off | ON se una sorgente misura già anche la EV Charger, così non viene contata due volte |
| Limite massimo contatore | 3300 W | Potenza oltre cui il contatore stacca; è il tetto sotto cui il balancer resta |
| Tensione | 230 V | Tensione di linea, usata per convertire Watt ↔ Ampere |
| Alimentazione / Fasi | Monofase | Monofase (1) o trifase (3), incide sulla conversione W↔A |
| Corrente min | 6 A | Sotto questo valore la EV Charger viene messa in pausa invece che ridotta |
| Corrente max | 16 A | Corrente più alta scrivibile sulla EV Charger |

**Opzioni (modificabili a caldo, senza riavvio):**

| Parametro | Default | A cosa serve |
|---|---|---|
| Sorgenti di consumo | (nessuna) | Come sopra, modificabile in seguito |
| La sorgente include la EV Charger | off | Come sopra, modificabile in seguito |
| Margine di sicurezza | 200 W | Riserva lasciata libera sotto il limite contatore, assorbe i picchi |
| Corrente di pausa | 0 A | Valore scritto per "fermare" la ricarica in pausa (alcune EV Charger richiedono un valore > 0) |
| Step di corrente ammessi | (vuoto) | Elenco di Ampere ammessi separati da virgola (es. `6, 8, 10, 16`); vuoto = ogni intero da min a max |
| Hold seconds | 300 s | Attesa minima prima di poter rialzare la corrente (anti-flapping) |
| Intervallo di aggiornamento | 3 s | Ogni quanto legge la potenza e applica la corrente (minimo 3 s) |
| Preset fasce | ARERA F1/F2/F3 | Set di fasce orarie per il conteggio energia (ARERA o fascia unica) |
| Mostra pannello | on | Mostra/nasconde il pannello EV Balance nella sidebar |

## Entità create

- **Switch** *Bilanciamento attivo* — se OFF legge ma non tocca la EV Charger.
- **Binary sensor** *Ricarica in pausa* — con l'attributo `reasons` (spiega la decisione).
- **Number** *Limite massimo contatore*, *Margine di sicurezza* — tuning live.
- **Sensor** potenza totale/sorgenti/EV Charger, *Corrente concessa*, *Fascia attiva*.
- **Sensor energia** per ogni sorgente × fascia × periodo (giornaliero + mensile),
  in kWh, `state_class: total_increasing` → compatibili con la dashboard Energia.

## Pannello in sidebar

L'integrazione registra un **pannello opzionale in sidebar** (custom element,
nessuno step di build) che mostra potenza live, corrente concessa, limite
contatore e l'energia per fascia degli ultimi mesi. Legge tutto dalle entità
esistenti e dalle long-term statistics del Recorder — nessuno storage extra. Si
attiva/disattiva dalle opzioni (*Mostra pannello*).

## Fasce orarie ARERA

| Fascia | Quando |
|---|---|
| **F1** | Lun–Ven 08:00–19:00 |
| **F2** | Lun–Ven 07:00–08:00 e 19:00–23:00; Sab 07:00–23:00 |
| **F3** | Lun–Ven 23:00–07:00; Sab 23:00–07:00; Domenica e festivi |

Le fasce sono data-driven ([`energy.py`](custom_components/evbalance/energy.py)):
aggiungere un preset custom significa aggiungere regole, senza toccare la logica.

## Sviluppo

La logica di bilanciamento è isolata e testabile in
[`balancer.py`](custom_components/evbalance/balancer.py) (nessuna dipendenza da
Home Assistant).

## ⚠️ Sicurezza

Questo software modula la corrente ma **non sostituisce le protezioni
elettriche** dell'impianto. Imposta sempre un margine di sicurezza adeguato e
verifica il comportamento della tua EV Charger quando riceve corrente 0 A.

### ⚠️ Disclaimer

L'uso dell'applicazione e il settaggio dei parametri **dovrebbero essere
effettuati esclusivamente da persone autorizzate ed esperte**. L'autore declina
ogni responsabilità riguardo possibili danni causati a cose e persone, in modo
diretto o indiretto, derivanti dall'uso di questo software.

## Licenza

Source-available sotto **PolyForm Noncommercial License 1.0.0** con termini
aggiuntivi — vedi [`LICENSE`](LICENSE).

In breve:

- **Gratis** per qualsiasi uso non commerciale / non professionale.
- Copie e opere derivate possono essere ridistribuite **solo all'interno di un
  progetto open source** (licenza approvata OSI, sorgente pubblico completo).
- Tutti i diritti restano di esclusiva proprietà di Matteo Dalle Feste, che può
  cambiare la licenza delle versioni future o chiudere il software in qualsiasi
  momento.
- **L'uso commerciale o professionale richiede un accordo scritto separato** —
  contatta matteo@dallefeste.com.

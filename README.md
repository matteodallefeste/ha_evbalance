# EV Balance — Load balancer energetico per Home Assistant

Integrazione custom (installabile via **HACS**) che evita il distacco del
contatore per sovraccarico modulando la corrente della EV Charger in base ai
consumi di casa, e che tiene traccia dell'energia per **fasce orarie** (ARERA
F1/F2/F3) con reset giornaliero e mensile.

## Come funziona

Ad ogni ciclo (default ogni 15 s) l'integrazione:

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

**Setup iniziale:**

| Campo | Descrizione |
|---|---|
| Sensore potenza EV Charger | `sensor.*` in W (o kW) con la potenza attuale della EV Charger |
| Number corrente EV Charger | `number.*` su cui scrivere gli Ampere massimi |
| Limite massimo contatore | Potenza oltre cui il contatore stacca (es. 3300, 6000) |
| Tensione / Alimentazione | 230V monofase oppure 400V trifase |
| Corrente min / max | Range ammesso dalla EV Charger (es. 6–16 A) |

**Opzioni (modificabili a caldo):** sorgenti di consumo (multi-select di
sensori di potenza), margine di sicurezza, corrente di pausa, `hold_seconds`,
intervallo di aggiornamento, preset fasce (ARERA / fascia unica).

## Entità create

- **Switch** *Bilanciamento attivo* — se OFF legge ma non tocca la EV Charger.
- **Binary sensor** *Ricarica in pausa* — con l'attributo `reasons` (spiega la decisione).
- **Number** *Limite massimo contatore*, *Margine di sicurezza* — tuning live.
- **Sensor** potenza totale/sorgenti/EV Charger, *Corrente concessa*, *Fascia attiva*.
- **Sensor energia** per ogni sorgente × fascia × periodo (giornaliero + mensile),
  in kWh, `state_class: total_increasing` → compatibili con la dashboard Energia.

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

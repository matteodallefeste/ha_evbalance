# EV Balance: l'integrazione tutta italiana per ricaricare l'auto elettrica senza far saltare il contatore

Chi ha una wallbox in casa conosce bene il problema: metti in carica l'auto, poi qualcuno accende il forno, la lavatrice e il phon, e il contatore stacca. La soluzione tradizionale è ricaricare piano, di notte, o rinunciare a usare gli elettrodomestici mentre l'auto è in carica. Ma non è l'unica strada.

**EV Balance** è una nuova integrazione per **Home Assistant**, ideata e sviluppata interamente in Italia da **Matteo Dalle Feste**, che gestisce automaticamente il *bilanciamento del carico* durante la ricarica dell'auto elettrica. In pratica adatta in tempo reale la corrente della wallbox al consumo di casa, dando sempre **priorità alle utenze domestiche**.

## Come funziona

L'idea è semplice quanto efficace. Servono solo due informazioni:

- il **consumo totale della casa**, letto da un meter (misuratore) installato subito dopo il contatore;
- la **potenza assorbita dalla wallbox** durante la ricarica.

Con questi due dati EV Balance verifica di continuo quanta potenza sta usando l'impianto e calcola quanta ne resta disponibile per l'auto. Se in casa i consumi salgono — parte la lavastoviglie, si accende il condizionatore — l'integrazione **abbassa la corrente di ricarica** per non superare il limite. Quando i consumi calano, la **rialza** automaticamente per sfruttare tutta la potenza disponibile.

Il risultato è che l'auto ricarica sempre alla massima velocità possibile in quel momento, senza mai mettere a rischio il contatore e senza doverci pensare.

## Cosa si può configurare

EV Balance è pensata per adattarsi a qualsiasi impianto e a qualsiasi wallbox:

- **Corrente minima e massima** di ricarica (ad esempio da 6 a 16 A);
- gli **step di corrente** supportati dal tuo EV charger (es. 6, 8, 10, 13, 16 A);
- la **potenza massima disponibile**, cioè quella del contratto (o quella dell'impianto, se inferiore);
- un **margine di sicurezza**, per lasciare sempre un cuscinetto sotto il limite;
- il funzionamento sia su impianti **monofase (230 V)** che **trifase (400 V)**.

Per evitare continui su e giù della corrente, l'integrazione usa una logica *anti-flapping*: le riduzioni sono immediate (per sicurezza), mentre gli aumenti avvengono solo dopo un breve tempo di attesa, così la ricarica resta stabile.

## In più: monitoraggio per fasce orarie

Oltre al bilanciamento, EV Balance tiene traccia dell'energia consumata per **fasce orarie ARERA (F1/F2/F3)**, con conteggi giornalieri e mensili. I sensori generati sono compatibili con la **dashboard Energia** di Home Assistant, così è facile capire quanto e quando si è ricaricato.

## Facile da installare

L'installazione avviene tramite **HACS**, il gestore di componenti della community di Home Assistant:

1. In HACS apri il menu **⋮ → Custom repositories**;
2. aggiungi l'URL del repository — `https://github.com/matteodallefeste/ha_evbalance` — scegliendo la categoria **Integration**;
3. installa **EV Balance** e riavvia Home Assistant;
4. vai su **Impostazioni → Dispositivi e servizi → Aggiungi integrazione → EV Balance** e segui la procedura guidata.

In alternativa, si può copiare manualmente la cartella `custom_components/evbalance/` del repository dentro la propria cartella `config/custom_components/` e riavviare Home Assistant.

Tutta la configurazione avviene poi tramite interfaccia grafica, senza toccare file YAML.

## Una nota importante

EV Balance modula la corrente di ricarica, ma **non sostituisce le protezioni elettriche** dell'impianto. L'installazione del meter e la configurazione dei parametri dovrebbero essere affidate a persone competenti, impostando sempre un margine di sicurezza adeguato.

---

*EV Balance è un progetto gratuito per uso non commerciale. Codice, guida completa di installazione e configurazione sono disponibili sul repository del progetto su GitHub: [github.com/matteodallefeste/ha_evbalance](https://github.com/matteodallefeste/ha_evbalance).*

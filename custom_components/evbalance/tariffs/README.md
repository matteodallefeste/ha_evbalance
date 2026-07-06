# Preset di fasce orarie (tariff presets)

Ogni file `*.json` in questa cartella descrive le **fasce orarie** (time-of-use)
di un piano tariffario di un paese. Sono dati puri: per aggiungere un paese basta
una Pull Request con un nuovo file — nessun codice Python.

I file vengono validati in CI contro [`schema.json`](schema.json). Una PR con un
JSON non conforme fallisce automaticamente.

## Come contribuire

1. Copia un file esistente (es. [`it_arera.json`](it_arera.json)) e rinominalo
   `<paese_piano>.json` (minuscolo, es. `es_pvpc.json`).
2. Compila i campi seguendo lo schema qui sotto.
3. Indica una `source_url` ufficiale (regolatore o distributore) da cui hai
   ricavato gli orari.
4. Apri la PR. Il file compare automaticamente nel menu tariffe.

## Schema

```jsonc
{
  "type": "tou",                 // per ora solo "tou" (time-of-use statico)
  "id": "es_pvpc",               // univoco, minuscolo [a-z0-9_]
  "country": "ES",               // ISO 3166-1 alpha-2 (per preselezione + festivi)
  "label": "España — PVPC 2.0TD",// mostrato nel menu
  "source_url": "https://...",   // fonte ufficiale (consigliata)
  "fallback": "valle",           // banda usata quando nessuna regola combacia
  "holidays_as": "valle",        // opzionale: banda nei festivi nazionali
  "bands": [
    { "id": "punta", "rank": 3, "label": "Punta", "color": "#ef4444" },
    { "id": "llano", "rank": 2, "label": "Llano", "color": "#f59e0b" },
    { "id": "valle", "rank": 1, "label": "Valle", "color": "#22c78b" }
  ],
  "seasons": [
    {
      "months": null,            // null = tutto l'anno, oppure es. [10,11,12,1,2,3]
      "rules": [
        { "band": "valle", "days": [0,1,2,3,4], "start": "00:00", "end": "08:00" }
      ]
    }
  ]
}
```

### Regole dei campi

- **`bands[].rank`**: costo relativo, `1` = più economica. Serve al confronto tra
  fasce (oggi reporting, in futuro il controllo della ricarica).
- **`bands[].color`**: opzionale, esadecimale `#rrggbb`, usato nel pannello.
- **`start` / `end`**: formato `"HH:MM"`, in **ora locale**. `end` è **esclusa**;
  usa `"24:00"` per indicare la fine della giornata.
- **`days`**: `0` = lunedì … `6` = domenica.
- **`months`**: lista `1`–`12` per fasce stagionali (es. estate/inverno), oppure
  `null` per tutto l'anno. La prima `season` che copre il mese corrente vince.
- **`holidays_as`**: se presente (e con `country` valido), nei festivi nazionali
  la fascia diventa questa banda. I festivi sono calcolati dalla libreria
  `holidays` in base al `country`.

## Preset disponibili

Paesi europei con una tariffa a fasce (ToU statico) riconoscibile a livello
nazionale/regolatore. Gli orari sono in **ora locale** e, dove il piano cambia
con l'ora legale/solare, sono modellati come due `seasons` per mese.

| File | Paese | Piano | Fasce |
|------|-------|-------|-------|
| [`it_arera.json`](it_arera.json) | 🇮🇹 IT | ARERA F1/F2/F3 | 3 |
| [`it_monoraria.json`](it_monoraria.json) | 🇮🇹 IT | Monoraria (prezzo unico) | 1 |
| [`es_pvpc.json`](es_pvpc.json) | 🇪🇸 ES | PVPC 2.0TD punta/llano/valle | 3 |
| [`fr_hphc.json`](fr_hphc.json) | 🇫🇷 FR | Heures Pleines / Creuses | 2 |
| [`pt_bihoraria.json`](pt_bihoraria.json) | 🇵🇹 PT | Bi-horária (ciclo diário) | 2 |
| [`ie_nightsaver.json`](ie_nightsaver.json) | 🇮🇪 IE | Nightsaver day/night (stagionale) | 2 |
| [`gb_economy7.json`](gb_economy7.json) | 🇬🇧 GB | Economy 7 | 2 |
| [`be_bihourly.json`](be_bihourly.json) | 🇧🇪 BE | Tweevoudig tarief (Vlaanderen) | 2 |
| [`nl_dubbeltarief.json`](nl_dubbeltarief.json) | 🇳🇱 NL | Dubbeltarief normaal/dal | 2 |
| [`gr_deddie.json`](gr_deddie.json) | 🇬🇷 GR | Νυχτερινό / διζωνικό (stagionale) | 2 |
| [`pl_g12.json`](pl_g12.json) | 🇵🇱 PL | Taryfa G12 | 2 |
| [`hr_vtnt.json`](hr_vtnt.json) | 🇭🇷 HR | Viša/niža tarifa (stagionale) | 2 |
| [`si_bloki.json`](si_bloki.json) | 🇸🇮 SI | Omrežnina, 5 časovnih blokov (SODO 2024) | 5 |
| [`dk_radius.json`](dk_radius.json) | 🇩🇰 DK | Nettarif Radius (tarifmodel 3.0) | 3 |
| [`bg_daynight.json`](bg_daynight.json) | 🇧🇬 BG | Дневна/нощна (stagionale) | 2 |
| [`ch_htnt.json`](ch_htnt.json) | 🇨🇭 CH | Hoch-/Niedertarif (comune) | 2 |
| [`fi_yosahko.json`](fi_yosahko.json) | 🇫🇮 FI | Yösähkö giorno/notte | 2 |

### Extra-UE

Paesi non europei con una tariffa a fasce riconoscibile. Dove non esiste uno
standard nazionale unico (USA, Cina, Corea: le fasce variano per utility o
provincia) il preset è un esempio rappresentativo, da adattare al proprio
distributore.

| File | Paese | Piano | Fasce |
|------|-------|-------|-------|
| [`ru_threezone.json`](ru_threezone.json) | 🇷🇺 RU | Трёхзонный (пик/полупик/ночь) | 3 |
| [`in_tod.json`](in_tod.json) | 🇮🇳 IN | Time of Day (MERC/MSEDCL) | 3 |
| [`us_pge_touc.json`](us_pge_touc.json) | 🇺🇸 US | California PG&E E-TOU-C (esempio) | 2 |
| [`cn_tou_beijing.json`](cn_tou_beijing.json) | 🇨🇳 CN | 峰谷分时 (esempio Pechino) | 3 |
| [`jp_tepco_night8.json`](jp_tepco_night8.json) | 🇯🇵 JP | TEPCO おトクなナイト8 giorno/notte | 2 |
| [`kr_kepco_tou.json`](kr_kepco_tou.json) | 🇰🇷 KR | KEPCO 계시별 (stagionale) | 3 |

### Paesi esclusi (di proposito)

- **Prevalentemente spot/dinamici** (nessuna fascia fissa nazionale): 🇳🇴 NO, 🇸🇪 SE,
  🇪🇪 EE, 🇱🇻 LV, 🇱🇹 LT. Il prezzo orario di mercato è un feed esterno, non fasce.
- **Nessuno standard nazionale** (fasce definite dal singolo distributore):
  🇩🇪 DE, 🇦🇹 AT, 🇨🇿 CZ, 🇸🇰 SK, 🇭🇺 HU, 🇱🇺 LU. Contributi con lo schema del proprio
  distributore sono benvenuti (basta un nuovo file). Lo stesso vale per la
  Svizzera, dove [`ch_htnt.json`](ch_htnt.json) è solo un esempio diffuso.

### Note

- Le fasce sono valutate **in ordine**: la prima regola che combacia vince,
  altrimenti si usa `fallback`. Non serve coprire ogni minuto.
- Le tariffe **dinamiche/spot** (prezzo orario da mercato, es. Nord Pool) non si
  modellano qui: sono un feed di prezzo esterno, non fasce fisse.
- Diversi piani cambiano orario con **l'ora legale** (il meccanismo del contatore
  segue di fatto lo spostamento di un'ora): sono resi con due `seasons` per mese
  (inverno/estate), un'approssimazione dello switch DST che nel modello non è al
  minuto ma al mese.

# Icone / brand di EV Balance

Immagini pronte per l'integrazione (sfondo verde-energia con fulmine su gauge).

| File | Dimensione | Uso |
|---|---|---|
| `evbalance/icon.png` | 256×256 | icona |
| `evbalance/icon@2x.png` | 512×512 | icona hi-dpi |
| `evbalance/logo.png` | 924×256 | wordmark |
| `evbalance/logo@2x.png` | 1853×512 | wordmark hi-dpi |

## Farle comparire in HACS / Home Assistant

HACS e la UI di Home Assistant **non** leggono l'icona da questo repo: la
prendono dal repository ufficiale [home-assistant/brands](https://github.com/home-assistant/brands).
Per attivarla:

1. Fai un fork di `home-assistant/brands`.
2. Copia i file sotto `custom_integrations/evbalance/`:
   ```
   custom_integrations/evbalance/icon.png
   custom_integrations/evbalance/icon@2x.png
   custom_integrations/evbalance/logo.png      (opzionale)
   custom_integrations/evbalance/logo@2x.png   (opzionale)
   ```
3. Apri una PR. Una volta unita, l'icona appare automaticamente
   (via `https://brands.home-assistant.io/evbalance/icon.png`).

I PNG sono trasparenti e rispettano i requisiti dei brands (quadrati per le
icone, `@2x` a doppia risoluzione).

## Rigenerare le immagini

Script sorgente: `tools/make_icon.py` (richiede `Pillow`).
```
OUT_DIR=brands/evbalance python tools/make_icon.py
```

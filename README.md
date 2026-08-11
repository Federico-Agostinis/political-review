# Political Review — Documenti Istituzionali

Dashboard che raccoglie automaticamente documenti istituzionali pubblici, li arricchisce con LLM
e li pubblica come sito statico su GitHub Pages.

---

## Panoramica

Il progetto:

1. **Raccoglie documenti** da sette fonti istituzionali del Veneto:
   - **Osservatorio Veneto Lavoro** — bollettini periodici sul mercato del lavoro (HTML server-rendered).
   - **Consiglio comunale di Padova** — ordini del giorno e verbali stenografici
     delle sedute (JSON:API Drupal, il sito del Comune è una SPA Angular senza SSR).
   - **Consiglio comunale di Padova, registro delibere** — testo integrale di ogni deliberazione
     esecutiva, con relatore, dibattito ed esito nominale della votazione (registro Lotus Domino).
   - **Unioncamere del Veneto** — Barometro mensile dell'economia regionale e indagini
     congiunturali (REST API WordPress).
   - **Confindustria Veneto Est** — comunicati stampa sulla congiuntura industriale di Padova,
     Venezia, Treviso e Rovigo (REST API del backend WordPress headless).
   - **Banca d'Italia** — "L'economia del Veneto", rapporto annuale della collana *Economie
     regionali* (URL deterministico per anno).
   - **Provincia di Padova** — ordinanze dirigenziali dall'albo pretorio.
2. **Estrae il testo** e lo salva in `data/documents/<fonte>/<slug>.txt`. Di norma il testo si
   ricava dal PDF; i comunicati di Confindustria non hanno PDF e il testo si prende direttamente
   dal corpo del post; le delibere allegano un Word 97-2003 binario, da cui il testo si estrae
   senza dipendenze di sistema (`olefile`).
3. **Arricchisce con Gemini** (opzionale): sintesi, punti chiave, dati quantitativi (bollettini e
   report economici), decisioni con proponente ed esito (odg, delibere, ordinanze), interventi in
   aula (verbali).
4. **Pubblica** una dashboard React che permette di sfogliare, filtrare e cercare i documenti.

Tutta la pipeline (raccolta, arricchimento, build, deploy) gira su GitHub Actions in cron
giornaliero — non c'è un backend/server: l'output finale è un file JSON statico + una SPA React
che lo legge via fetch.

---

## Pipeline dati

```
Veneto Lavoro (HTML accordion)        --\
Consiglio comunale PD (JSON:API)      --\
Delibere CC Padova (registro Domino)  --\
Unioncamere Veneto (WP REST)          --- scripts/docs_scraper.py --> data/documents_index.json
Confindustria Veneto Est (WP REST)    --/                              + data/documents/**/*.txt
Banca d'Italia (URL per anno)         --/
Provincia di Padova (albo pretorio)   --/

data/documents_index.json --(Gemini)--> scripts/enrich_docs.py --> data/documents_index.json (in-place)
```

- Ogni forma di sito è una `strategy` in `scripts/docs_scraper.py`, instradata da una mappa
  esplicita: aggiungere una fonte significa aggiungere una voce a `SOURCES`, e un `fetch_*` solo
  se la forma del sito è nuova.
- I PDF vengono scaricati in una directory temporanea e **mai** lasciati nel repo: si committa solo
  il testo estratto.
- Deduplicazione: `md5(url_senza_querystring)`.
- L'albo pretorio della Provincia è una finestra scorrevole senza archivio interrogabile: l'indice
  accumula nel tempo atti che sul sito non sono più raggiungibili, ma non è possibile recuperare
  a posteriori quelli già scaduti.
- I documenti si tagliano per conteggio (`MAX_DOCUMENTS_PER_SOURCE`), mai per età: un bollettino
  mensile non deve sparire dall'indice solo perché è vecchio di più di 15 giorni.
- L'arricchimento LLM (Gemini 2.0 Flash Lite) è opzionale e richiede `GEMINI_API_KEY` come secret
  GitHub; senza chiave i campi restano `null`/`[]` e la dashboard resta comunque utilizzabile con
  titolo, data, e link al PDF (graceful degradation). Il workflow ruota su fino a 10 chiavi
  (`GEMINI_API_KEY`..`GEMINI_API_KEY10`) per il rate limiting.
- Ogni `doc_type` usa un prompt dedicato: sui verbali, ad esempio, si estrae `interventions`
  (chi ha detto cosa in aula). I tipi sono otto, raggruppati per forma dell'output:
  `figures` (dati quantitativi) per `bollettino`, `misure`, `report_economico`,
  `comunicato_industria`; `decisions` per `odg`, `delibera`, `ordinanza`;
  `interventions` per `verbale`.
  Un `doc_type` senza voce in `PROMPTS` viene saltato ma resta `summary=None`, quindi rientra nella
  coda a ogni run consumando il budget `--limit`: aggiungendo un tipo, aggiornare sempre `PROMPTS`
  e `PROFILES` in `scripts/enrich_docs.py`.

---

## Dashboard

`dashboard-v2/` è un'app React 19 + TypeScript + Vite. L'unica vista è `DocumentsView`
(`src/components/dashboard/DocumentsView.tsx`), che mostra:

- KPI e grafici di distribuzione per fonte e tipo documento.
- Ricerca/filtro locale su titolo, fonte e tipo.
- Per ogni documento: astratto/sintesi, punti chiave, dati quantitativi o delibere/interventi
  (a seconda del tipo), link al PDF originale, e testo integrale a richiesta.

Il build compilato (`npm run build`, con `VITE_BASE_PATH=/political-review/v2/`) viene copiato
in `v2/` (root) e servito da GitHub Pages sotto `/v2/`. `index.html` (root) è un semplice redirect
verso la build più recente.

---

## Struttura repository

- `dashboard-v2/` — **[SOURCE]** app React della dashboard.
- `v2/` — **[BUILD]** output compilato (generato da CI, non editare a mano).
- `data/` — `documents_index.json` (indice documenti + statistiche), `documents/<fonte>/*.txt`
  (testo estratto).
- `scripts/` — `docs_scraper.py` (raccolta PDF + estrazione testo), `enrich_docs.py`
  (arricchimento LLM), `gemini_client.py` (client Gemini condiviso: rotazione chiavi + circuit
  breaker sui 429), `run_local_pipeline.py` (orchestrazione locale), `init_region.py` (bootstrap
  per un fork verso un'altra regione — non aggiornato ai path documenti, va rivisto a mano).
- `config/` — `project_settings.py` (punto unico di configurazione: regione, path, limiti),
  `requirements.txt`, `Dockerfile`.
- `.github/workflows/docs_pipeline.yml` — cron giornaliero: raccolta documenti → arricchimento LLM
  → build dashboard → sync in `v2/` → commit&push.

---

## Comandi

### Dashboard (`dashboard-v2/`, React+Vite+TS)
```bash
cd dashboard-v2
npm install
npm run dev       # dev server
npm run build     # tsc -b && vite build -> dist/
npm run lint      # eslint .
npm run preview   # preview della build
```

### Pipeline Python (root)
```bash
pip install -r config/requirements.txt

python scripts/run_local_pipeline.py     # intera pipeline locale

# oppure singoli step:
python scripts/docs_scraper.py           # raccolta PDF -> testo
export GEMINI_API_KEY="..."
python scripts/enrich_docs.py --limit 10 # arricchimento LLM (opzionale)
```

---

## Secrets GitHub

| Secret | Obbligatorio | Uso |
|--------|--------------|-----|
| `GEMINI_API_KEY` (..`GEMINI_API_KEY10`) | No | Abilita l'arricchimento LLM dei documenti |

Senza API key la raccolta e l'estrazione testo funzionano comunque: solo i campi di sintesi
restano vuoti.

---

## Note tecniche

- Non esiste ambiente di test automatizzato/CI dei test: la validazione avviene eseguendo gli
  script o `npm run lint`/`npm run build` per la dashboard.
- Nessun backend: la dashboard fa fetch diretto del JSON statico in `data/documents_index.json`;
  ogni modifica allo schema di questo file va rispecchiata sia in `scripts/docs_scraper.py`/
  `scripts/enrich_docs.py` sia in `dashboard-v2/src/types/dashboard.ts`.
- Il workflow GitHub Actions committa e pushua direttamente su `main` (con `[skip ci]`) al termine
  della pipeline: è la fonte primaria di aggiornamento dati.

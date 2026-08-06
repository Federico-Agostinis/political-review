# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Cos'è questo progetto

Political Review è una "Political Intelligence Dashboard" che raccoglie automaticamente documenti istituzionali pubblici (PDF), li arricchisce con LLM e li pubblica come dashboard statica su GitHub Pages. Tutta la pipeline (raccolta, arricchimento, build, deploy) gira su GitHub Actions in cron giornaliero — non c'è un backend/server: l'output finale sono un file JSON statico + una SPA React che lo legge via fetch.

Le fonti attuali sono entrambe venete: l'**Osservatorio Veneto Lavoro** (bollettini periodici sul mercato del lavoro) e il **Consiglio comunale di Padova** (ordini del giorno, delibere approvate, verbali stenografici). `config/project_settings.py` (`REGION_NAME="Veneto"`, `REGION_ID="veneto"`) resta il punto unico di configurazione per nome regione e path dei file dati.

Nota storica: il progetto era originariamente un aggregatore di rassegna stampa (RSS + TGR) forkato da un template pensato per il Piemonte; quella parte (scraping RSS/TGR, NLP/LLM sulle notizie, viste dashboard collegate) è stata rimossa — il progetto ora fa **solo** raccolta documenti istituzionali. `scripts/init_region.py` (bootstrap per un fork verso un'altra regione) non è stato aggiornato a questa nuova realtà: resetta ancora path di notizie che non esistono più e non tocca `data/documents_index.json`/`data/documents/` — va rivisto a mano prima di essere riusato.

## Pipeline dati (il flusso centrale da capire)

```
PDF Veneto Lavoro + Consiglio comunale PD --(scripts/docs_scraper.py)--> data/documents_index.json
                                                                          + data/documents/**/*.txt
data/documents_index.json --(Gemini)--> scripts/enrich_docs.py --> data/documents_index.json (in-place)
```

- **`scripts/docs_scraper.py`**: raccoglie i PDF di Veneto Lavoro (`osservatorio.venetolavoro.it`, HTML server-rendered) e del Consiglio comunale di Padova (odg / delibere approvate / verbali). Il sito del Comune è una SPA Angular senza SSR: i dati si prendono dalla JSON:API Drupal non documentata (`/api/entity?path=/lavori-del-consiglio-comunale-<anno>`, i link PDF stanno in `information_partials[*].text_body.value`). I PDF vengono scaricati in una temp dir e **mai** lasciati nel repo; si committa solo il testo estratto. Dedup con `md5(url_senza_querystring)`.
- **`scripts/enrich_docs.py`**: passata Gemini con prompt diversi per `doc_type` — sui verbali estrae `interventions` (chi ha detto cosa in aula) con `max_output_tokens=8192`. Usa `scripts/gemini_client.py` (rotazione su fino a 10 chiavi `GEMINI_API_KEY`..`GEMINI_API_KEY10` + circuit breaker sui 429).
- L'arricchimento LLM è opzionale: senza `GEMINI_API_KEY` come secret GitHub i campi restano `null`/`[]` e lo script esce con 0 — la dashboard resta utilizzabile con titolo, data, e link al PDF (graceful degradation).
- I documenti si tagliano per conteggio (`MAX_DOCUMENTS_PER_SOURCE` in `config/project_settings.py`), mai per età: un bollettino mensile non deve sparire dall'indice solo perché è vecchio.

## Struttura repository

- `dashboard-v2/` — **[SOURCE]** app React 19 + TypeScript + Vite che è la dashboard live. Vista unica: `components/dashboard/DocumentsView.tsx`. Sotto `src/`: `hooks/useDashboardData.ts` (fetch di `data/documents_index.json`), `store/useDashboardStore.ts` (Zustand, solo `documents`/`isLoading`), `utils/{chartUtils,regionConfig}.ts`, `types/dashboard.ts`. Grafici con Chart.js/react-chartjs-2, styling Tailwind v4.
- `v2/` — **[BUILD]** output compilato di `dashboard-v2` (generato da CI, non editare a mano), servito da GitHub Pages sotto `/v2/`.
- `data/` — `documents_index.json` (indice + statistiche `by_source`/`by_doc_type`/`enriched`), `documents/<veneto_lavoro|comune_padova>/*.txt` (testo estratto dai PDF).
- `scripts/` — `docs_scraper.py` (raccolta), `enrich_docs.py` (arricchimento LLM), `gemini_client.py` (client Gemini condiviso), `run_local_pipeline.py` (orchestrazione locale), `init_region.py` (fork bootstrap, stale — vedi sopra).
- `config/` — `project_settings.py` è il punto unico di configurazione (regione, path, limiti); `requirements.txt`; `Dockerfile`.
- `.github/workflows/docs_pipeline.yml` — cron 05:00 UTC: raccolta documenti (fail-safe, timeout 15min) → arricchimento LLM (opzionale, timeout 20min) → build dashboard-v2 → sync in `v2/` → commit&push.
- `index.html` (root) — semplice redirect verso la dashboard `v2/` compilata più recente.

## Comandi

### Dashboard (dashboard-v2/, React+Vite+TS)
```bash
cd dashboard-v2
npm install
npm run dev       # dev server
npm run build     # tsc -b && vite build -> dist/
npm run lint      # eslint .
npm run preview   # preview della build
```
Il build in CI viene copiato in `v2/` (root) con `VITE_BASE_PATH=/political-review/v2/`; per riprodurre localmente un build "come in produzione" impostare la stessa env var.

### Pipeline Python (root)
```bash
pip install -r config/requirements.txt

python scripts/run_local_pipeline.py         # intera pipeline locale

# oppure singoli step:
python scripts/docs_scraper.py               # documenti istituzionali (PDF -> testo)
export GEMINI_API_KEY="..."
python scripts/enrich_docs.py --limit 10     # arricchimento LLM documenti (opzionale)
```

## Note tecniche importanti

- Non esiste ambiente di test automatizzato/CI dei test: la validazione avviene eseguendo gli script o `npm run lint`/`npm run build` per la dashboard.
- Nessun backend: la dashboard fa fetch diretto di `data/documents_index.json`; ogni modifica allo schema di questo file va rispecchiata sia in `scripts/docs_scraper.py`/`scripts/enrich_docs.py` sia in `dashboard-v2/src/types/dashboard.ts` e negli hook/util che lo consumano.
- Il workflow GitHub Actions committa e pusha direttamente su `main` (con `[skip ci]`) al termine della pipeline: è la fonte primaria di aggiornamento dati, non serve intervento manuale quotidiano.
- `GEMINI_API_KEY` assente è uno stato normale e supportato (graceful degradation), non un errore da correggere.
- `v2/` è generato: non modificarlo a mano, editare sempre `dashboard-v2/src/`.

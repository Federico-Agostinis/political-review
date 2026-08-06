# Dashboard-v2 (React + TypeScript + Vite)

Questa è la versione moderna della dashboard per la Rassegna Stampa, costruita con React e Tailwind CSS.

## 🏗️ Architettura

- **Sorgente (`dashboard-v2/`)**: Contiene il codice React non compilato. I browser non possono eseguire questi file direttamente.
- **Build (`v2/`)**: Contiene i file compilati generati da Vite. Questa cartella viene servita da GitHub Pages ed è quella che gli utenti vedono effettivamente.

## 🚀 Sviluppo Locale

1. Entra nella cartella: `cd dashboard-v2`
2. Installa le dipendenze: `npm install`
3. Avvia il server di sviluppo: `npm run dev`

Durante lo sviluppo, la dashboard caricherà i dati simulati da un proxy locale configurato in `vite.config.ts`.

## 📦 Build e Deploy

Il build viene eseguito automaticamente da GitHub Actions, ma se vuoi farlo manualmente:

```bash
VITE_BASE_PATH=/political-review/v2/ npm run build
```

I file generati in `dist/` devono essere copiati nella cartella `v2/` alla radice del progetto per essere pubblicati.

## 🛠️ Variabili d'Ambiente

- `VITE_BASE_PATH`: Definisce il percorso base (Base URL) per il caricamento degli asset su GitHub Pages. Viene impostato automaticamente dal workflow GitHub in base al nome del repository.

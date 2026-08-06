
import os
from pathlib import Path

# Project Root
BASE_DIR = Path(__file__).parent.parent

# --- Regional Settings ---
REGION_NAME = "Veneto"
REGION_ID = "veneto"  # lowercase, no spaces, used for filenames
REPO_NAME = "political-review"
GH_USERNAME = "Federico-Agostinis"

# --- URLs ---
REPO_BASE_URL = f"https://{GH_USERNAME}.github.io/{REPO_NAME}"

# --- Data Paths ---
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
CONFIG_DIR = BASE_DIR / "config"

# Documenti istituzionali (Veneto Lavoro, Consiglio comunale di Padova)
DOCUMENTS_INDEX = DATA_DIR / "documents_index.json"
DOCUMENTS_TEXT_DIR = DATA_DIR / "documents"

# --- Pipeline Settings ---
SCRAPER_RETRY_COUNT = 3
AI_RETRY_COUNT = 3
AI_DELAY_SECONDS = 0.5
# I documenti si tagliano per conteggio, mai per età: un bollettino mensile
# non deve sparire dall'indice solo perché è vecchio di più di 15 giorni.
MAX_DOCUMENTS_PER_SOURCE = 200

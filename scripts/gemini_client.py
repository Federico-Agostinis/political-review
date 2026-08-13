"""
Client Gemini condiviso: rotazione su piu' chiavi (GEMINI_API_KEY..GEMINI_API_KEY20)
e circuit breaker sui 429. Usato da enrich_docs.py.
"""

import json
import logging
import os
import re
import time
from typing import Optional

import requests

API_KEYS = []
if os.environ.get("GEMINI_API_KEY"):
    API_KEYS.append(os.environ.get("GEMINI_API_KEY"))

for i in range(2, 21):  # Check up to GEMINI_API_KEY20
    k = os.environ.get(f"GEMINI_API_KEY{i}")
    if k:
        API_KEYS.append(k)

GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def check_api_key() -> bool:
    """Verifica se almeno una API key e' configurata."""
    if not API_KEYS:
        logger.warning("Nessuna GEMINI_API_KEY configurata. L'arricchimento verra' saltato.")
        return False
    return True


# Circuit Breaker & Rotation State
CIRCUIT_BREAKER_TRIPPED = False
CONSECUTIVE_429_ERRORS = 0
MAX_CONSECUTIVE_429_GLOBAL = 10  # Higher since we rotate
CURRENT_KEY_INDEX = 0


def get_current_api_key() -> str:
    """Ritorna la chiave corrente ruotando se necessario."""
    if not API_KEYS:
        return ""
    return API_KEYS[CURRENT_KEY_INDEX % len(API_KEYS)]


def rotate_key():
    """Passa alla prossima chiave disponibile."""
    global CURRENT_KEY_INDEX
    CURRENT_KEY_INDEX += 1
    k_idx = CURRENT_KEY_INDEX % len(API_KEYS)
    logger.info(f"🔄 Rotazione API Key: passo alla chiave #{k_idx + 1}")


def call_gemini_api(prompt: str, max_retries: int = None, max_output_tokens: int = 1024,
                    temperature: float = 0.2, timeout: int = 30) -> Optional[str]:
    """Chiama Gemini API con rotazione chiavi e exponential backoff.

    max_output_tokens/temperature/timeout sono parametrizzati per i chiamanti che
    lavorano su documenti lunghi (enrich_docs.py): con i default piu' bassi un
    verbale di consiglio comunale sforerebbe MAX_TOKENS e la risposta tornerebbe
    senza 'parts', indistinguibile da un errore di rete.
    """
    global CIRCUIT_BREAKER_TRIPPED, CONSECUTIVE_429_ERRORS

    if not check_api_key():
        return None

    if CIRCUIT_BREAKER_TRIPPED:
        logger.warning("⛔ Circuit Breaker attivo: salto richiesta API.")
        return None

    # Default retries to 3 + number of extra keys to allow full rotation attempt
    if max_retries is None:
        max_retries = 3 + len(API_KEYS)

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens
        }
    }

    for attempt in range(1, max_retries + 1):
        api_key = get_current_api_key()
        url = f"{GEMINI_ENDPOINT}?key={api_key}"

        try:
            response = requests.post(url, json=body, headers={"Content-Type": "application/json"}, timeout=timeout)

            if response.status_code == 200:
                CONSECUTIVE_429_ERRORS = 0
                # Opzionale: RUOTIAMO comunque a ogni successo per bilanciare il carico (Round Robin)
                rotate_key()

                result = response.json()
                candidate = (result.get("candidates") or [{}])[0]
                finish_reason = candidate.get("finishReason")
                parts = (candidate.get("content") or {}).get("parts") or []

                if not parts:
                    # Senza questo log, un MAX_TOKENS e' indistinguibile da un
                    # errore di rete e l'item verrebbe ritentato per sempre.
                    logger.error(f"Risposta senza contenuto (finishReason={finish_reason}, "
                                 f"maxOutputTokens={max_output_tokens})")
                    return None

                if finish_reason and finish_reason != "STOP":
                    logger.warning(f"Risposta troncata o filtrata: finishReason={finish_reason}")

                return parts[0].get("text")

            elif response.status_code == 429:
                CONSECUTIVE_429_ERRORS += 1
                logger.warning(f"Rate limit (429) su chiave #{CURRENT_KEY_INDEX % len(API_KEYS) + 1}")

                # Ruota chiave immediatamente
                rotate_key()

                # Se abbiamo provato TUTTE le chiavi e continuano i 429, allora sleep
                if CONSECUTIVE_429_ERRORS >= len(API_KEYS):
                     wait_time = (2 ** (attempt - len(API_KEYS) + 1)) + 5
                     logger.warning(f"🔄 Ciclo chiavi completato (tutte {len(API_KEYS)} sature). Attendo {wait_time}s prima di ripartire dalla prima...")
                     time.sleep(wait_time)
                else:
                    # Piccolo sleep per non bombardare
                    time.sleep(1)

                if CONSECUTIVE_429_ERRORS >= MAX_CONSECUTIVE_429_GLOBAL:
                    logger.error(f"⛔ Raggiunti {MAX_CONSECUTIVE_429_GLOBAL} errori 429 consecutivi su diverse chiavi. CIRCUIT BREAKER TRIPPED.")
                    CIRCUIT_BREAKER_TRIPPED = True
                    return None

            elif response.status_code >= 500:
                wait_time = 5 * attempt
                logger.warning(f"Errore Server ({response.status_code}), attendo {wait_time}s (tentativo {attempt}/{max_retries})")
                time.sleep(wait_time)

            else:
                logger.error(f"Errore API non gestito: {response.status_code} - {response.text}")
                return None

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            wait_time = 5 * attempt
            logger.warning(f"Errore rete/timeout ({e}), attendo {wait_time}s (tentativo {attempt}/{max_retries})")
            time.sleep(wait_time)
        except Exception as e:
            logger.error(f"Errore imprevisto: {e}")
            return None

    logger.error(f"Falliti tutti i {max_retries} tentativi per Gemini API")
    return None


def parse_llm_response(response_text: str) -> Optional[dict]:
    """Parse la risposta JSON da Gemini."""
    try:
        # Rimuovi markdown se presente
        clean = re.sub(r'```json\n?|```\n?', '', response_text).strip()
        return json.loads(clean)
    except json.JSONDecodeError as e:
        logger.error(f"Errore parsing JSON: {e}")
        return None

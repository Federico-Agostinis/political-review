"""
Arricchimento LLM dei documenti istituzionali raccolti da docs_scraper.py.

Riusa call_gemini_api()/parse_llm_response() da gemini_client.py, che gia'
implementano la rotazione sulle 10 chiavi e il circuit breaker sui 429.

ATTENZIONE all'ordine degli import: gemini_client costruisce API_KEYS a livello
di modulo leggendo os.environ. Se il .env venisse caricato DOPO l'import, ogni
chiamata tornerebbe None senza errori — funzionando in CI (dove l'env lo imposta
il runner) e fallendo solo in locale. Per questo load_dotenv() sta prima.

Senza GEMINI_API_KEY i campi restano null/[] e lo script esce con 0:
la pagina dashboard resta utilizzabile con titolo, data, abstract e PDF.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.append(str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# Import DOPO load_dotenv: API_KEYS viene popolato all'import di gemini_client.
from gemini_client import call_gemini_api, parse_llm_response, check_api_key  # noqa: E402
from config import project_settings  # noqa: E402

logger = logging.getLogger(__name__)

DOCUMENTS_INDEX = project_settings.DOCUMENTS_INDEX
DOCUMENTS_TEXT_DIR = project_settings.DOCUMENTS_TEXT_DIR

MODEL_TAG = "gemini-3.5-flash-lite"

# Il collo di bottiglia sui verbali e' l'output, non l'input: 57 pagine sono
# ~50k token, ben dentro il contesto del modello. Serve invece spazio in uscita.
PROFILES = {
    "bollettino":         {"max_chars": 25000,  "max_output_tokens": 2048},
    "misure":             {"max_chars": 25000,  "max_output_tokens": 2048},
    "odg":                {"max_chars": 25000,  "max_output_tokens": 4096},
    # Un atto singolo sta sui 10k caratteri, ma le varianti urbanistiche con
    # controdeduzioni arrivano a molto di piu'.
    "delibera":           {"max_chars": 60000,  "max_output_tokens": 4096},
    "verbale":            {"max_chars": 400000, "max_output_tokens": 8192},
    # I rapporti annuali di Banca d'Italia sono ~100 pagine: servono piu' input
    # e piu' spazio in uscita per le figures.
    "report_economico":   {"max_chars": 120000, "max_output_tokens": 4096},
    "comunicato_industria": {"max_chars": 25000, "max_output_tokens": 2048},
    "ordinanza":          {"max_chars": 25000,  "max_output_tokens": 2048},
}
DEFAULT_PROFILE = {"max_chars": 25000, "max_output_tokens": 2048}

BASE_RULES = """Rispondi ESCLUSIVAMENTE con un oggetto JSON valido, senza testo introduttivo e senza blocchi markdown.
Usa solo informazioni presenti nel documento: non inventare nulla. Se un campo non è ricavabile, lascialo vuoto ([] o null).
Scrivi in italiano."""

PROMPT_BOLLETTINO = """Sei un analista del mercato del lavoro. Analizza questo bollettino dell'Osservatorio Veneto Lavoro.

{rules}

Schema richiesto:
{{
  "summary": "sintesi esecutiva di 3-5 frasi sulle dinamiche occupazionali descritte",
  "key_points": ["3-6 punti chiave, uno per stringa"],
  "figures": [{{"label": "nome dell'indicatore", "value": "valore con unità di misura", "note": "contesto o variazione, opzionale"}}],
  "entities": {{"people": [], "organizations": [], "locations": ["province e territori citati"]}}
}}

In "figures" riporta i dati quantitativi salienti: assunzioni, cessazioni, saldo occupazionale, tipologie contrattuali, settori e province più rilevanti. Massimo 8 voci.

DOCUMENTO ({title}):
{text}"""

PROMPT_ODG = """Sei un analista di politica locale. Analizza questo ordine del giorno del Consiglio comunale di Padova.

{rules}

Schema richiesto:
{{
  "summary": "sintesi di 2-4 frasi su cosa è stato trattato nella seduta",
  "key_points": ["2-5 punti chiave sui temi politicamente più rilevanti"],
  "decisions": [{{"numero": "numero del punto o della delibera, se presente", "oggetto": "oggetto della delibera", "proponente": "assessore o consigliere proponente", "esito": "approvata/respinta/rinviata se indicato, altrimenti null"}}],
  "entities": {{"people": ["proponenti e consiglieri citati"], "organizations": [], "locations": ["zone e quartieri di Padova citati"]}}
}}

Elenca in "decisions" TUTTI i punti presenti nel documento, nell'ordine in cui compaiono.

DOCUMENTO ({title}):
{text}"""

PROMPT_DELIBERA = """Sei un analista di politica locale. Analizza il testo integrale di questa deliberazione del Consiglio comunale di Padova.

{rules}

Schema richiesto:
{{
  "summary": "sintesi di 3-5 frasi: cosa dispone la delibera, perché, e come si è conclusa la votazione",
  "key_points": ["3-6 punti chiave su contenuto del provvedimento, effetti pratici e andamento del dibattito"],
  "decisions": [{{"numero": "numero di registro della delibera (formato AAAA/NNNN)", "oggetto": "oggetto della deliberazione", "proponente": "nome e cognome del relatore o proponente politico, non il settore", "esito": "approvata/respinta/rinviata, con i conteggi di voto se presenti (es. 'approvata: 15 favorevoli, 2 astenuti')"}}],
  "entities": {{"people": ["relatore, consiglieri intervenuti e citati"], "organizations": ["enti, società partecipate e associazioni coinvolte"], "locations": ["zone, quartieri e vie di Padova citati"]}}
}}

Il documento contiene l'elenco nominale di presenti e assenti: NON riportarlo in "people", che deve contenere solo chi ha un ruolo attivo (relatore, intervenuti nel dibattito, persone citate nel merito).
In "decisions" inserisci una sola voce, quella della delibera stessa.

DOCUMENTO ({title}):
{text}"""

PROMPT_VERBALE = """Sei un analista di politica locale. Analizza questo verbale stenografico di una seduta del Consiglio comunale di Padova: ti interessa ricostruire CHI HA DETTO COSA in aula.

{rules}

Schema richiesto:
{{
  "summary": "sintesi di 3-5 frasi sull'andamento politico della seduta",
  "key_points": ["3-6 punti chiave: scontri, temi dominanti, decisioni rilevanti"],
  "interventions": [{{"speaker": "cognome e nome del consigliere o assessore", "gruppo": "gruppo consiliare se ricavabile, altrimenti null", "argomento": "argomento dell'intervento", "posizione": "favorevole/contrario/critico/neutro se ricavabile, altrimenti null", "sintesi": "1-2 frasi su cosa ha sostenuto"}}],
  "entities": {{"people": ["persone citate"], "organizations": [], "locations": ["zone e quartieri di Padova citati"]}}
}}

In "interventions" raggruppa per oratore e argomento: un elemento per ogni intervento significativo, massimo 40. Ignora gli interventi puramente procedurali (appelli, verifiche del numero legale).

DOCUMENTO ({title}):
{text}"""

PROMPT_REPORT_ECONOMICO = """Sei un analista economico. Analizza questo documento di analisi congiunturale sull'economia del Veneto (Unioncamere, Banca d'Italia o Confindustria).

{rules}

Schema richiesto:
{{
  "summary": "sintesi esecutiva di 3-5 frasi sul quadro economico descritto",
  "key_points": ["3-6 punti chiave, uno per stringa"],
  "figures": [{{"label": "nome dell'indicatore", "value": "valore con unità di misura", "note": "contesto o variazione, opzionale"}}],
  "entities": {{"people": ["persone citate, se rilevanti"], "organizations": ["enti, associazioni e imprese citate"], "locations": ["province e territori citati"]}}
}}

In "figures" riporta i dati quantitativi salienti: produzione industriale, export, PIL, fatturato, occupazione, credito, variazioni percentuali e settori più rilevanti. Massimo 8 voci.

DOCUMENTO ({title}):
{text}"""

PROMPT_ATTO_PROVINCIA = """Sei un analista di politica locale. Analizza questa ordinanza della Provincia di Padova pubblicata all'albo pretorio.

{rules}

Schema richiesto:
{{
  "summary": "sintesi di 2-4 frasi su cosa dispone l'atto e perché",
  "key_points": ["2-5 punti chiave su effetti pratici e destinatari del provvedimento"],
  "decisions": [{{"numero": "numero dell'atto, se presente", "oggetto": "oggetto del provvedimento", "proponente": "settore o dirigente proponente", "esito": "quanto viene disposto o approvato"}}],
  "entities": {{"people": ["amministratori e dirigenti citati"], "organizations": ["enti e imprese coinvolte"], "locations": ["comuni e strade provinciali citati"]}}
}}

DOCUMENTO ({title}):
{text}"""

PROMPTS = {
    "bollettino": PROMPT_BOLLETTINO,
    "misure": PROMPT_BOLLETTINO,
    "odg": PROMPT_ODG,
    "delibera": PROMPT_DELIBERA,
    "verbale": PROMPT_VERBALE,
    # Un doc_type assente da questa mappa viene saltato ma resta summary=None:
    # rientrerebbe nella coda pending a ogni run, bruciando il budget --limit
    # senza mai riuscire. Ogni nuovo doc_type va aggiunto qui.
    "report_economico": PROMPT_REPORT_ECONOMICO,
    "comunicato_industria": PROMPT_REPORT_ECONOMICO,
    "ordinanza": PROMPT_ATTO_PROVINCIA,
}


def load_index() -> dict:
    if not DOCUMENTS_INDEX.exists():
        return {}
    with open(DOCUMENTS_INDEX, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_index(payload: dict):
    documents = payload.get("documents", [])
    payload["stats"] = build_stats(documents)
    payload["total"] = len(documents)
    with open(DOCUMENTS_INDEX, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def build_stats(documents: list) -> dict:
    by_source, by_doc_type = {}, {}
    enriched = 0
    dates = sorted(d["date"] for d in documents if d.get("date"))

    for doc in documents:
        by_source[doc["source_id"]] = by_source.get(doc["source_id"], 0) + 1
        by_doc_type[doc["doc_type"]] = by_doc_type.get(doc["doc_type"], 0) + 1
        if doc.get("summary"):
            enriched += 1

    return {
        "by_source": by_source,
        "by_doc_type": by_doc_type,
        "enriched": enriched,
        "date_range": {
            "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None,
        },
    }


def read_text(doc: dict) -> str:
    text_path = doc.get("text_path")
    if not text_path:
        return ""
    full = DOCUMENTS_TEXT_DIR.parent / text_path
    if not full.exists():
        return ""
    return full.read_text(encoding="utf-8", errors="replace")


def enrich_document(doc: dict) -> bool:
    """Arricchisce un documento in-place. Ritorna True se ha avuto successo."""
    doc_type = doc.get("doc_type", "")
    profile = PROFILES.get(doc_type, DEFAULT_PROFILE)
    template = PROMPTS.get(doc_type)

    if not template:
        logger.warning(f"Nessun prompt per doc_type={doc_type}, salto")
        return False

    text = read_text(doc)
    if not text.strip():
        logger.warning(f"Testo assente ({doc.get('extraction_status')}), salto: {doc['title'][:60]}")
        return False

    truncated = text[:profile["max_chars"]]
    prompt = template.format(rules=BASE_RULES, title=doc.get("title", ""), text=truncated)

    response = call_gemini_api(
        prompt,
        max_output_tokens=profile["max_output_tokens"],
        timeout=120,
    )
    if not response:
        return False

    parsed = parse_llm_response(response)
    if not parsed:
        return False

    doc["summary"] = parsed.get("summary")
    doc["key_points"] = parsed.get("key_points") or []
    doc["figures"] = parsed.get("figures") or []
    doc["decisions"] = parsed.get("decisions") or []
    doc["interventions"] = parsed.get("interventions") or []

    entities = parsed.get("entities") or {}
    doc["entities"] = {
        "people": entities.get("people") or [],
        "organizations": entities.get("organizations") or [],
        "locations": entities.get("locations") or [],
    }
    doc["enriched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["enriched_by"] = MODEL_TAG
    return True


def main():
    parser = argparse.ArgumentParser(description="Arricchimento LLM documenti istituzionali")
    parser.add_argument("--limit", type=int, default=10, help="Massimo documenti da arricchire (default: 10)")
    parser.add_argument("--doc-type", help="Limita a un solo doc_type (utile per test)")
    args = parser.parse_args()

    payload = load_index()
    documents = payload.get("documents", [])

    if not documents:
        print("ℹ️  Nessun documento da arricchire. Esegui prima docs_scraper.py")
        return 0

    if not check_api_key():
        # Stato normale e supportato: la dashboard mostra abstract e PDF.
        print("⚠️  GEMINI_API_KEY assente: arricchimento saltato (graceful degradation)")
        return 0

    pending = [
        d for d in documents
        if not d.get("summary")
        and d.get("extraction_status") == "ok"
        and d.get("doc_type") in PROMPTS
        and (not args.doc_type or d.get("doc_type") == args.doc_type)
    ]
    orphaned = {
        d.get("doc_type") for d in documents
        if not d.get("summary") and d.get("extraction_status") == "ok" and d.get("doc_type") not in PROMPTS
    }
    if orphaned:
        # Doc_type senza prompt (es. una fonte deprecata di proposito, tipo
        # "decreto_presidente" dopo che provincia_padova e' stata ristretta
        # alle sole ordinanze): restano orfani per sempre, ma senza questo
        # filtro rientrerebbero in pending a ogni run bruciando uno slot di
        # --limit su un fallimento garantito.
        print(f"⏭️  doc_type senza prompt, esclusi dalla coda: {sorted(orphaned)}")
    print(f"🤖 {len(pending)} documenti da arricchire, ne processo max {args.limit}")

    done = 0
    for doc in pending[:args.limit]:
        print(f"   → {doc['doc_type']}: {doc['title'][:65]}")
        if enrich_document(doc):
            done += 1
            print(f"     ✅ arricchito")
        else:
            print(f"     ⚠️  fallito, riprovo al prossimo run")

    save_index(payload)
    print(f"\n✅ {done}/{min(len(pending), args.limit)} documenti arricchiti")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"❌ Errore fatale: {exc}")
        sys.exit(1)

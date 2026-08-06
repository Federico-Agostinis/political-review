"""
Raccolta documenti istituzionali (PDF) e estrazione del testo.

Due sorgenti, due strategie:
  - "liferay"       -> Osservatorio Veneto Lavoro (HTML server-rendered, accordion)
  - "padova_jsonapi"-> Comune di Padova, pagina "Lavori del Consiglio comunale"
                       (il sito e' una SPA Angular senza SSR: si passa dalla
                        JSON:API Drupal /api/entity?path=...)

Scrive data/documents_index.json e il testo estratto in data/documents/<source_id>/<slug>.txt.
I PDF vengono scaricati in una directory temporanea e mai lasciati nel repo:
sia run_local_pipeline.py sia press_review.yml fanno "git add ." alla cieca.

Fail-safe: ogni sorgente e' isolata, una fonte morta non fa fallire il run.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

sys.path.append(str(Path(__file__).parent.parent))
from config import project_settings

DOCUMENTS_INDEX = project_settings.DOCUMENTS_INDEX
DOCUMENTS_TEXT_DIR = project_settings.DOCUMENTS_TEXT_DIR
MAX_PER_SOURCE = project_settings.MAX_DOCUMENTS_PER_SOURCE

USER_AGENT = "Mozilla/5.0 (compatible; PressReviewBot/1.0; +https://github.com/Federico-Agostinis/political-review)"
HEADERS = {"User-Agent": USER_AGENT}
TIMEOUT = 60

SOURCES = [
    {
        "source_id": "veneto_lavoro",
        "collana": "La Bussola",
        "doc_type": "bollettino",
        "source": "Osservatorio Veneto Lavoro",
        "topic": "Lavoro",
        "zone": "Veneto",
        "strategy": "liferay",
        "url": "https://osservatorio.venetolavoro.it/la-bussola",
    },
    {
        "source_id": "veneto_lavoro",
        "collana": "Misure",
        "doc_type": "misure",
        "source": "Osservatorio Veneto Lavoro",
        "topic": "Lavoro",
        "zone": "Veneto",
        "strategy": "liferay",
        "url": "https://osservatorio.venetolavoro.it/misure",
    },
    {
        "source_id": "comune_padova",
        "collana": "Consiglio comunale",
        "source": "Comune di Padova",
        "topic": "Politica locale",
        "zone": "Padova",
        "strategy": "padova_jsonapi",
        "url": "https://www.comune.padova.it/api/entity?path=/lavori-del-consiglio-comunale-{year}",
        "page_url": "https://www.comune.padova.it/lavori-del-consiglio-comunale-{year}",
    },
]

# Prefisso del nome file -> doc_type. I verbali arrivano circa un mese dopo la
# seduta, le delibere approvate qualche giorno dopo: non esiste sempre la terna.
PADOVA_DOC_TYPES = [
    ("elenco_argomenti", "odg"),
    ("approvate", "delibere_approvate"),
    ("verbale_cc", "verbale"),
]


def slugify(value: str) -> str:
    """Slug URL-safe: i .txt vengono serviti da GitHub Pages, accenti e spazi
    nel nome file provocherebbero mismatch di encoding solo in produzione."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return value[:120] or "documento"


def content_hash(pdf_url: str) -> str:
    """Hash sulla parte stabile dell'URL: Liferay appende ?t=<timestamp> che
    cambia a ogni rigenerazione, senza lo split si riscaricherebbe tutto."""
    return hashlib.md5(pdf_url.split("?")[0].encode("utf-8")).hexdigest()[:12]


def parse_date(*candidates) -> str:
    """Restituisce una data nel formato "%Y-%m-%d %H:%M:%S"."""
    for raw in candidates:
        if not raw:
            continue
        raw = str(raw).strip()
        # 15/07/2026
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", raw)
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)} 00:00:00"
        # 2026_04_20 oppure 2026-04-20
        m = re.search(r"(\d{4})[_-](\d{2})[_-](\d{2})", raw)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)} 00:00:00"
        # 2026_06_Bussola -> primo del mese
        m = re.search(r"(\d{4})[_-](\d{2})(?!\d)", raw)
        if m:
            return f"{m.group(1)}-{m.group(2)}-01 00:00:00"
        # epoch in millisecondi (parametro ?t= di Liferay)
        m = re.fullmatch(r"\d{13}", raw)
        if m:
            return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# Strategie di raccolta
# --------------------------------------------------------------------------

def fetch_liferay(source: dict) -> list:
    """Osservatorio Veneto Lavoro: ogni pubblicazione e' un div.accordion-panel
    con titolo nell'header, abstract + "Data pubblicazione" nel contenuto e il
    link al PDF. Il testo dell'anchor e' sempre "Scarica PDF": inutile come titolo."""
    resp = requests.get(source["url"], headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    found = []
    for panel in soup.select("div.accordion-panel"):
        link = panel.select_one('a[href*="/documents/"][href*=".pdf"]')
        if not link:
            continue

        href = link.get("href", "")
        pdf_url = requests.compat.urljoin(source["url"], href)

        header = panel.select_one(".accordion-toggle") or panel.select_one(".accordion-header")
        title = header.get_text(" ", strip=True) if header else ""

        body = panel.select_one(".component-html") or panel
        body_text = body.get_text(" ", strip=True)

        # "Data pubblicazione: 15/07/2026"
        date_match = re.search(r"Data pubblicazione:?\s*([\d/]+)", body_text)
        filename_match = re.search(r"/documents/[^/]+/[^/]+/([^/?]+\.pdf)", href, re.I)
        filename = filename_match.group(1) if filename_match else ""
        timestamp = re.search(r"[?&]t=(\d{13})", href)

        date = parse_date(
            date_match.group(1) if date_match else None,
            filename,
            timestamp.group(1) if timestamp else None,
        )

        # L'abstract e' il corpo ripulito da data e call-to-action.
        abstract = re.sub(r"Data pubblicazione:?\s*[\d/]+", "", body_text)
        abstract = re.sub(r"Scarica PDF", "", abstract).strip()

        if not title:
            title = Path(filename).stem.replace("_", " ") if filename else "Documento"

        found.append({
            "title": title,
            "link": pdf_url,
            "date": date,
            "doc_type": source["doc_type"],
            "abstract": abstract[:2000] or None,
            "slug": slugify(Path(filename).stem or title),
            "page_url": source["url"],
        })

    return found


def fetch_padova(source: dict, backfill_years: int) -> list:
    """Comune di Padova: la pagina "Lavori del Consiglio comunale <anno>" elenca
    i PDF di ogni seduta. Va parsato il JSON (nella risposta grezza gli slash
    sono escapati, una regex sul body raw non troverebbe nulla)."""
    found = []
    year = datetime.now(timezone.utc).year

    for offset in range(backfill_years + 1):
        target_year = year - offset
        url = source["url"].format(year=target_year)
        try:
            resp = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=TIMEOUT)
            if resp.status_code == 404:
                # A gennaio la pagina del nuovo anno non esiste ancora: si prosegue
                # con gli anni precedenti invece di interrompere il backfill.
                print(f"   ℹ️  {target_year}: pagina inesistente")
                continue
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            print(f"   ⚠️  {target_year}: {exc}")
            continue

        data = payload.get("data") or {}

        # I link stanno in information_partials[*].text_body.value, non in
        # information_body (che su questa pagina e' null).
        fragments = []
        main_body = data.get("information_body")
        if isinstance(main_body, dict) and main_body.get("value"):
            fragments.append(main_body["value"])
        for partial in data.get("information_partials") or []:
            text_body = (partial or {}).get("text_body") or {}
            if isinstance(text_body, dict) and text_body.get("value"):
                fragments.append(text_body["value"])
        if not fragments:
            # Fallback difensivo: se la struttura cambia, cerchiamo comunque
            # i PDF nell'intero payload.
            fragments.append(json.dumps(data))

        body = "\n".join(fragments)
        pdf_urls = sorted(set(re.findall(r'https?://[^\s"\'<>\\]+?\.pdf', body)))

        # Drupal disambigua i re-upload con un suffisso "_0": a parita' di
        # tipo e data teniamo la revisione piu' recente.
        best = {}
        for pdf_url in pdf_urls:
            filename = pdf_url.rsplit("/", 1)[-1]
            lowered = filename.lower()

            doc_type = None
            for prefix, mapped in PADOVA_DOC_TYPES:
                if lowered.startswith(prefix):
                    doc_type = mapped
                    break
            # Scarta gli "odg_GENERALE_con_esito_*": registri cumulativi
            # dell'intero anno, senza data di seduta.
            if not doc_type:
                continue

            date = parse_date(filename)
            key = (doc_type, date)
            if key not in best or filename > best[key][0]:
                best[key] = (filename, pdf_url)

        for (doc_type, date), (filename, pdf_url) in best.items():
            label = {
                "odg": "Ordine del giorno",
                "delibere_approvate": "Delibere approvate",
                "verbale": "Verbale della seduta",
            }[doc_type]

            found.append({
                "title": f"Consiglio comunale {date[:10]} — {label}",
                "link": pdf_url,
                "date": date,
                "doc_type": doc_type,
                "abstract": None,
                "slug": slugify(Path(filename).stem),
                "page_url": source.get("page_url", source["url"]).format(year=target_year),
            })

        print(f"   📄 {target_year}: {len(best)} documenti rilevanti")

    return found


# --------------------------------------------------------------------------
# Download / estrazione
# --------------------------------------------------------------------------

def extract_pdf_text(pdf_url: str, dest: Path) -> dict:
    """Scarica il PDF in una temp dir, ne estrae il testo e lo scrive in dest.
    Ritorna {extraction_status, pages, chars}."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_pdf = Path(tmpdir) / "doc.pdf"
        try:
            resp = requests.get(pdf_url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            tmp_pdf.write_bytes(resp.content)
        except Exception as exc:
            print(f"      ❌ download fallito: {exc}")
            return {"extraction_status": "failed", "pages": 0, "chars": 0}

        try:
            reader = PdfReader(str(tmp_pdf))
            pages = len(reader.pages)
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception as exc:
            print(f"      ❌ estrazione fallita: {exc}")
            return {"extraction_status": "failed", "pages": 0, "chars": 0}

    if not text:
        # PDF scansionato senza layer testo: chars=0 da solo sarebbe ambiguo.
        return {"extraction_status": "empty", "pages": pages, "chars": 0}

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return {"extraction_status": "ok", "pages": pages, "chars": len(text)}


def load_index() -> list:
    if not DOCUMENTS_INDEX.exists():
        return []
    try:
        with open(DOCUMENTS_INDEX, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload.get("documents", [])
    except Exception as exc:
        print(f"⚠️  Indice documenti illeggibile, riparto da zero: {exc}")
        return []


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


def save_index(documents: list):
    documents.sort(key=lambda d: d.get("date", ""), reverse=True)

    # Cap per conteggio e per sorgente, mai per età.
    capped, seen = [], {}
    for doc in documents:
        sid = doc["source_id"]
        seen[sid] = seen.get(sid, 0) + 1
        if seen[sid] <= MAX_PER_SOURCE:
            capped.append(doc)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(capped),
        "stats": build_stats(capped),
        "documents": capped,
    }

    DOCUMENTS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    with open(DOCUMENTS_INDEX, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Raccolta documenti istituzionali")
    parser.add_argument("--backfill-years", type=int, default=2,
                        help="Anni pregressi da recuperare per il Consiglio comunale (default: 2)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Massimo numero di nuovi documenti da scaricare in questo run (0 = nessun limite)")
    args = parser.parse_args()

    print("📚 Raccolta documenti istituzionali")

    documents = load_index()
    known = {doc["content_hash"] for doc in documents}
    print(f"   Indice esistente: {len(documents)} documenti")

    candidates = []
    for source in SOURCES:
        label = f"{source['source']} / {source['collana']}"
        print(f"\n🔎 {label}")
        try:
            if source["strategy"] == "liferay":
                items = fetch_liferay(source)
            else:
                items = fetch_padova(source, args.backfill_years)
            print(f"   Trovati {len(items)} documenti")
            for item in items:
                candidates.append((source, item))
        except Exception as exc:
            # Fail-safe: una fonte morta non deve fermare le altre.
            print(f"   ⚠️  Sorgente non raggiungibile, la salto: {exc}")

    new_count = 0
    for source, item in candidates:
        chash = content_hash(item["link"])
        if chash in known:
            continue
        if args.limit and new_count >= args.limit:
            print(f"\n⏸️  Limite di {args.limit} nuovi documenti raggiunto")
            break

        print(f"\n⬇️  {item['title'][:70]}")
        text_rel = f"{source['source_id']}/{item['slug']}.txt"
        result = extract_pdf_text(item["link"], DOCUMENTS_TEXT_DIR / text_rel)

        documents.append({
            # Blocco NewsItem-compatibile
            "content_hash": chash,
            "title": item["title"],
            "link": item["link"],
            "date": item["date"],
            "source": source["source"],
            "topic": source["topic"],
            "zone": source["zone"],

            # Blocco documento
            "source_id": source["source_id"],
            "collana": source["collana"],
            "doc_type": item["doc_type"],
            "page_url": item["page_url"],
            # relativo a data/: e' cio' che rende corretta la base-url lato client
            "text_path": f"documents/{text_rel}",
            "pages": result["pages"],
            "chars": result["chars"],
            "extraction_status": result["extraction_status"],
            "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),

            # Blocco arricchimento (popolato da enrich_docs.py)
            "abstract": item["abstract"],
            "summary": None,
            "key_points": [],
            "figures": [],
            "decisions": [],
            "interventions": [],
            "entities": {"people": [], "organizations": [], "locations": []},
            "enriched_at": None,
            "enriched_by": None,
        })
        known.add(chash)
        new_count += 1
        print(f"      ✅ {result['extraction_status']} — {result['pages']} pagine, {result['chars']} caratteri")

    save_index(documents)
    print(f"\n✅ {new_count} nuovi documenti. Indice: {DOCUMENTS_INDEX}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"❌ Errore fatale: {exc}")
        sys.exit(1)

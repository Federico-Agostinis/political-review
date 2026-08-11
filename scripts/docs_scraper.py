"""
Raccolta documenti istituzionali e estrazione del testo.

Sette sorgenti, sei strategie:
  - "liferay"       -> Osservatorio Veneto Lavoro (HTML server-rendered, accordion)
  - "padova_jsonapi"-> Comune di Padova, pagina "Lavori del Consiglio comunale"
                       (il sito e' una SPA Angular senza SSR: si passa dalla
                        JSON:API Drupal /api/entity?path=...)
  - "padova_delibere_nsf" -> Comune di Padova, registro Lotus Domino delle
                       delibere esecutive: testo integrale di ogni atto in .doc
  - "wp_rest"       -> Unioncamere del Veneto e Confindustria Veneto Est, entrambi
                       WordPress con REST API aperta. Confindustria espone il suo
                       backend headless su content.confindustriavenest.it (il sito
                       pubblico e' una SPA Nuxt, inservibile).
  - "bankitalia"    -> Banca d'Italia, "L'economia del Veneto": URL deterministico
                       per anno, nessun HTML da parsare.
  - "albo_padova"   -> Provincia di Padova, albo pretorio (lista -> dettaglio -> PDF)

Il testo di un documento arriva per tre vie alternative:
  - PDF scaricato ed estratto (extract_pdf_text), il caso normale;
  - testo gia' presente nella risposta API (write_inline_text), usato per i
    comunicati Confindustria, che sono HTML e non hanno alcun PDF allegato;
  - allegato .doc Word 97-2003 (extract_doc_text), usato dalle delibere del
    Consiglio comunale: il registro Domino non pubblica PDF dell'atto.

Scrive data/documents_index.json e il testo estratto in data/documents/<source_id>/<slug>.txt.
I PDF vengono scaricati in una directory temporanea e mai lasciati nel repo:
sia run_local_pipeline.py sia press_review.yml fanno "git add ." alla cieca.

Fail-safe: ogni sorgente e' isolata, una fonte morta non fa fallire il run.
"""

import argparse
import hashlib
import html
import json
import os
import re
import ssl
import struct
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import olefile
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
    {
        # source_id separato da "comune_padova" di proposito: il cap di
        # save_index() e' per source_id, e le delibere (~100/anno) altrimenti
        # spingerebbero fuori verbali e odg dallo stesso secchio da 200.
        # Per la dashboard resta un solo ente: raggruppa su "source".
        "source_id": "comune_padova_delibere",
        "collana": "Deliberazioni di Consiglio",
        "doc_type": "delibera",
        "source": "Comune di Padova",
        "topic": "Politica locale",
        "zone": "Padova",
        "strategy": "padova_delibere_nsf",
        # Registro Lotus Domino delle delibere esecutive: la vista elenca 30
        # righe per pagina e linka il testo integrale di ogni atto come .doc.
        "url": "https://serviziweb4.comune.padova.it/Percorsi/DelibereEsecutivePadova.nsf/DelibereConsiglioEsecutive?OpenView",
        "max_pages": 3,
    },
    {
        "source_id": "unioncamere_veneto",
        "collana": "Barometro dell'economia regionale",
        "doc_type": "report_economico",
        "source": "Unioncamere del Veneto",
        "topic": "Economia",
        "zone": "Veneto",
        "strategy": "wp_rest",
        "content_kind": "pdf",
        "url": "https://www.unioncamereveneto.it/wp-json/wp/v2/posts",
        # 640 = categoria "statistiche": Barometro mensile e indagini congiunturali.
        "wp_params": {"categories": 640},
        "max_pages": 2,
    },
    {
        "source_id": "confindustria_veneto_est",
        "collana": "Comunicati stampa",
        "doc_type": "comunicato_industria",
        "source": "Confindustria Veneto Est",
        "topic": "Industria",
        "zone": "Veneto orientale",
        "strategy": "wp_rest",
        # I comunicati non allegano PDF: il testo sta nel corpo del post.
        "content_kind": "html",
        "url": "https://content.confindustriavenest.it/wp-json/wp/v2/comunicatostampa",
        "wp_params": {},
        # 2 pagine = ~100 comunicati, poco piu' di un anno. Con 4 pagine si
        # arriverebbe a 200, cioe' l'intero MAX_DOCUMENTS_PER_SOURCE: i comunicati
        # sono la fonte meno densa e da soli sbilancerebbero l'indice.
        "max_pages": 2,
        "public_url": "https://www.confindustriavenest.it/stampa-e-media/comunicati-stampa/{slug}",
    },
    {
        "source_id": "banca_italia",
        "collana": "Economie regionali — L'economia del Veneto",
        "doc_type": "report_economico",
        "source": "Banca d'Italia",
        "topic": "Economia",
        "zone": "Veneto",
        "strategy": "bankitalia",
        # Il Veneto e' sempre il n. 5 della collana, pubblicato a giugno.
        "url": "https://www.bancaditalia.it/pubblicazioni/economie-regionali/{year}/{year}-0005/{yy}05-veneto.pdf",
        "page_url": "https://www.bancaditalia.it/pubblicazioni/economie-regionali/{year}/{year}-0005/index.html",
    },
    {
        "source_id": "provincia_padova",
        "collana": "Albo pretorio",
        "source": "Provincia di Padova",
        "topic": "Politica locale",
        "zone": "Padova",
        "strategy": "albo_padova",
        "url": "https://attiweb.provincia.padova.it/AlboOnline/ricercaAlbo",
        "max_pages": 4,
        # Etichette come compaiono in lista (nel menu a tendina sono diverse:
        # li' le ordinanze sono "ORDINANZE"). Tutto il resto e' rumore
        # tecnico-contabile: le determine dirigenziali da sole sono meta' dell'albo.
        # La lookup e' esatta: un tipo assente da questo dict viene scartato.
        "tipi": {
            "OD ORDINANZA DIRIGENTE": "ordinanza",
        },
    },
]

# Prefisso del nome file -> doc_type. I verbali arrivano circa un mese dopo la
# seduta, l'ordine del giorno prima: non esiste sempre la coppia.
# Gli "Approvate_*.pdf" non sono qui di proposito: erano una tabella di una
# pagina (~1700 caratteri, niente relatore ne' voti), sostituita dal testo
# integrale dei singoli atti raccolto dalla strategia "padova_delibere_nsf".
PADOVA_DOC_TYPES = [
    ("elenco_argomenti", "odg"),
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


class _LegacyTLSAdapter(requests.adapters.HTTPAdapter):
    """Il server Domino del Comune negozia una chiave Diffie-Hellman corta che
    OpenSSL 3 rifiuta di default ("dh key too small"). SECLEVEL=1 e' il minimo
    che funziona: con 2 l'handshake fallisce, 0 non serve. L'abbassamento resta
    confinato a questa sessione, non tocca le altre fonti: e' un sito pubblico
    interrogato in sola lettura, senza credenziali."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def legacy_tls_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount("https://", _LegacyTLSAdapter())
    return session


def fetch_padova_delibere(source: dict) -> list:
    """Comune di Padova, registro Domino delle delibere esecutive.

    Sostituisce gli "Approvate_*.pdf", che erano una tabella di una pagina senza
    relatore ne' voti: qui ogni riga linka il testo integrale dell'atto (oggetto,
    discussione, esito nominale della votazione).

    Una sola richiesta per pagina di vista: il link al .doc sta gia' nella riga,
    la pagina "?OpenDocument" e' soltanto un meta-refresh verso quel file."""
    found = []
    session = legacy_tls_session()
    min_year = datetime.now(timezone.utc).year - source.get("backfill_years", 0)

    for page in range(source.get("max_pages", 3)):
        # La vista pagina a blocchi di 30 partendo da 1, non da 0.
        start = page * 30 + 1
        resp = session.get(source["url"], params={"Start": start}, timeout=TIMEOUT)
        resp.raise_for_status()
        # HTML Domino anni '90: dichiarato ISO-8859, non UTF-8.
        soup = BeautifulSoup(resp.content.decode("latin-1", errors="replace"), "html.parser")

        rows = 0
        for tr in soup.find_all("tr"):
            cells = tr.find_all("td")
            # Le tabelle sono annidate: senza il filtro sul numero di celle la
            # riga esterna catturerebbe in blocco i link di tutte le righe.
            if len(cells) != 6:
                continue
            texts = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
            ncron = texts[4]
            if not re.fullmatch(r"\d{4}/\d{4}", ncron):
                continue

            doc_link = next(
                (a for a in tr.find_all("a", href=True) if a["href"].lower().endswith(".doc")),
                None,
            )
            if not doc_link:
                # Delibera senza allegato testuale: senza .doc non c'e' nulla da
                # estrarre, l'oggetto da solo non vale un documento.
                continue

            # ATTENZIONE: la vista usa il formato americano MM/DD/YYYY, mentre
            # parse_date legge DD/MM/YYYY. Va convertita qui, altrimenti ogni
            # data fino al giorno 12 risulterebbe sbagliata ma plausibile.
            us = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", texts[1])
            date = parse_date(f"{us.group(3)}-{us.group(1)}-{us.group(2)}" if us else None)
            # Si filtra sulla data di delibera, non sulla prima colonna: la vista
            # e' ordinata per esecutivita', che cade anche settimane dopo il voto.
            if int(date[:4]) < min_year:
                continue

            settori = texts[2]
            # La cella del relatore ripete in coda il settore: "Andrea Ragona
            # Settore Urbanistica..." -> resta il solo nome.
            relatore = texts[3]
            if settori and relatore.endswith(settori):
                relatore = relatore[: -len(settori)].strip()

            oggetto = texts[5].rstrip(".")
            found.append({
                "title": f"Delibera CC {ncron} — {oggetto[:150]}",
                "link": requests.compat.urljoin(source["url"], doc_link["href"]),
                "date": date,
                "doc_type": source["doc_type"],
                "abstract": " — ".join(p for p in (relatore, settori, oggetto) if p)[:2000] or None,
                "slug": slugify(f"delibera_cc_{ncron}_{oggetto[:60]}"),
                # Il .doc non e' navigabile: si punta alla scheda del registro.
                "page_url": requests.compat.urljoin(source["url"], cells[5].find("a")["href"]),
                # Instrada su extract_doc_text: non e' un PDF.
                "content_kind": "doc",
            })
            rows += 1

        print(f"   📄 Start={start}: {rows} delibere")
        if not rows:
            break

    return found


def _clean_html(fragment: str) -> str:
    """Testo leggibile da un frammento HTML di WordPress."""
    return BeautifulSoup(fragment or "", "html.parser").get_text(" ", strip=True)


def fetch_wp_rest(source: dict) -> list:
    """WordPress REST API, usata da Unioncamere del Veneto e Confindustria Veneto Est.

    Due modalita', decise da "content_kind":
      - "pdf"  -> i PDF sono linkati nel corpo del post (Unioncamere)
      - "html" -> il post *e'* il documento, non c'e' nessun PDF (Confindustria)
    """
    found = []
    kind = source.get("content_kind", "pdf")

    for page in range(1, source.get("max_pages", 2) + 1):
        params = {
            "per_page": 50,
            "page": page,
            "orderby": "date",
            "order": "desc",
            "_fields": "id,date,link,slug,title,content",
            **source.get("wp_params", {}),
        }
        resp = requests.get(source["url"], params=params, headers=HEADERS, timeout=TIMEOUT)
        # Oltre l'ultima pagina WordPress risponde 400, non una lista vuota.
        if resp.status_code == 400:
            break
        resp.raise_for_status()
        posts = resp.json()
        if not posts:
            break

        for post in posts:
            title = html.unescape(_clean_html(post.get("title", {}).get("rendered", ""))) or "Documento"
            body = post.get("content", {}).get("rendered", "") or ""
            date = parse_date(post.get("date"))
            year = date[:4]

            if kind == "html":
                text = html.unescape(_clean_html(body))
                if not text:
                    continue
                link = source["public_url"].format(slug=post.get("slug", post.get("id")))
                found.append({
                    "title": title,
                    "link": link,
                    "date": date,
                    "doc_type": source["doc_type"],
                    "abstract": text[:2000] or None,
                    "slug": slugify(post.get("slug") or title),
                    "page_url": link,
                    "text": text,
                })
                continue

            pdf_urls = list(dict.fromkeys(re.findall(r'https?://[^\s"\'<>\\]+?\.pdf', body)))
            # Ogni post del Barometro ripete un link fisso a un numero vecchio
            # (2023_11): tenendo solo i PDF caricati nell'anno del post si scarta.
            same_year = [u for u in pdf_urls if f"/uploads/{year}/" in u]
            for pdf_url in (same_year or pdf_urls):
                filename = pdf_url.rsplit("/", 1)[-1]
                found.append({
                    "title": title,
                    "link": pdf_url,
                    "date": date,
                    "doc_type": source["doc_type"],
                    "abstract": html.unescape(_clean_html(body))[:2000] or None,
                    "slug": slugify(Path(filename).stem or post.get("slug") or title),
                    "page_url": post.get("link") or source["url"],
                })

        if len(posts) < 50:
            break

    return found


def fetch_bankitalia(source: dict) -> list:
    """Banca d'Italia, "L'economia del Veneto": l'URL e' ricavabile dall'anno,
    quindi si sondano gli anni invece di parsare un indice. Il rapporto esce a
    giugno: sull'anno corrente, prima di allora, il 404 e' il comportamento atteso."""
    found = []
    year_now = datetime.now(timezone.utc).year

    for year in range(year_now, year_now - 4, -1):
        url = source["url"].format(year=year, yy=str(year)[2:])
        try:
            resp = requests.head(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
            if resp.status_code != 200 or "pdf" not in resp.headers.get("Content-Type", ""):
                continue
        except Exception as exc:
            print(f"   ⚠️  {year}: {exc}")
            continue

        found.append({
            "title": f"L'economia del Veneto — Rapporto annuale {year}",
            "link": url,
            "date": f"{year}-06-15 00:00:00",
            "doc_type": source["doc_type"],
            "abstract": None,
            "slug": slugify(f"economia_veneto_{year}"),
            "page_url": source["page_url"].format(year=year, yy=str(year)[2:]),
        })

    return found


def fetch_albo_padova(source: dict) -> list:
    """Provincia di Padova, albo pretorio.

    Due passi: la lista espone pannelli Bootstrap (30 per pagina) da cui si legge
    il tipo di atto, il PDF sta solo nella pagina di dettaglio.

    L'albo e' una finestra scorrevole e non esiste un endpoint per lo storico
    (archivioAlbo con parametri GET restituisce zero risultati). La dedup per
    md5(url) fa si' che l'indice accumuli nel tempo atti che sul sito non sono
    piu' raggiungibili."""
    found = []
    tipi = source["tipi"]

    for page in range(1, source.get("max_pages", 4) + 1):
        resp = requests.get(source["url"], params={"page": page}, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        panels = soup.select("div.panel.panel-primary")
        if not panels:
            break

        for panel in panels:
            # "Tipo pubblicazione:" e' un'etichetta, il valore sta nel div gemello.
            doc_type = None
            for label in panel.select(".testata-etichetta"):
                if "tipo pubblicazione" in label.get_text(strip=True).lower():
                    value = label.find_next_sibling("div")
                    if value:
                        doc_type = tipi.get(value.get_text(strip=True))
                    break
            if not doc_type:
                continue

            detail = panel.select_one("a.dettaglio-doc")
            if not detail:
                continue

            panel_text = panel.get_text(" ", strip=True)
            date_match = re.search(r"Pubblicazione dal\s*(\d{2}/\d{2}/\d{4})", panel_text)
            oggetto_match = re.search(r"Oggetto:\s*(.+?)(?:\s*Documento in Pubblicazione|$)", panel_text)
            numero_match = re.search(r"Albo n\.\s*([\d/]+)", panel_text)

            oggetto = (oggetto_match.group(1).strip() if oggetto_match else "").rstrip(".")
            numero = numero_match.group(1) if numero_match else ""
            title = f"Ordinanza — {oggetto[:150]}" if oggetto else f"Ordinanza albo n. {numero}"

            detail_url = requests.compat.urljoin(source["url"], detail.get("href", ""))
            try:
                detail_resp = requests.get(detail_url, headers=HEADERS, timeout=TIMEOUT)
                detail_resp.raise_for_status()
            except Exception as exc:
                print(f"   ⚠️  dettaglio non raggiungibile: {exc}")
                continue

            detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
            pdf_url = None
            for anchor in detail_soup.select('a[href*="/download/albo/"]'):
                href = anchor.get("href", "")
                # "sbustato=true" e' la copia senza firma digitale: stesso contenuto,
                # ma l'URL cambia e creerebbe un duplicato.
                if "sbustato" in href:
                    continue
                pdf_url = requests.compat.urljoin(detail_url, href)
                break
            if not pdf_url:
                continue

            found.append({
                "title": title,
                "link": pdf_url,
                "date": parse_date(date_match.group(1) if date_match else None),
                "doc_type": doc_type,
                "abstract": oggetto[:2000] or None,
                "slug": slugify(f"{doc_type}_{numero.replace('/', '_')}_{oggetto[:60]}"),
                "page_url": detail_url,
            })

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


def _doc_to_text(raw: bytes) -> str:
    """Testo da un .doc Word 97-2003 (contenitore OLE).

    Il testo non e' contiguo nello stream "WordDocument": va ricomposto pezzo per
    pezzo seguendo la piece table (plcPcd), che sta in uno dei due table stream.
    Si evita cosi' una dipendenza di sistema (antiword/catdoc/LibreOffice) per
    l'unica fonte che non pubblica PDF."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_doc = Path(tmpdir) / "doc.doc"
        tmp_doc.write_bytes(raw)
        ole = olefile.OleFileIO(str(tmp_doc))
        try:
            wd = ole.openstream("WordDocument").read()
            # Il bit 0x0200 dei flag della FIB dice quale table stream e' valido.
            flags = struct.unpack_from("<H", wd, 0x0A)[0]
            table_name = "1Table" if flags & 0x0200 else "0Table"
            if not ole.exists(table_name):
                raise ValueError(f"stream {table_name} assente")
            table = ole.openstream(table_name).read()
            fc_clx, lcb_clx = struct.unpack_from("<II", wd, 0x01A2)
            clx = table[fc_clx:fc_clx + lcb_clx]
        finally:
            ole.close()

    # Il Clx e' una sequenza di Prc (marker 0x01) seguita dal Pcdt (marker 0x02).
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 0x02:
        raise ValueError("piece table non trovata")

    lcb = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5:pos + 5 + lcb]
    count = (len(plc) - 4) // 12
    cps = struct.unpack_from(f"<{count + 1}I", plc, 0)

    chunks = []
    for i in range(count):
        fc = struct.unpack_from("<I", plc, 4 * (count + 1) + 8 * i + 2)[0]
        length = cps[i + 1] - cps[i]
        # Bit 30 acceso: testo compresso in cp1252, un byte per carattere.
        if fc & 0x40000000:
            offset = (fc & ~0x40000000) // 2
            chunks.append(wd[offset:offset + length].decode("cp1252", errors="replace"))
        else:
            chunks.append(wd[fc:fc + length * 2].decode("utf-16-le", errors="replace"))

    text = "".join(chunks)
    # \r = fine paragrafo, \x07 = fine cella di tabella, \x0b = a capo forzato.
    text = text.replace("\r", "\n").replace("\x07", "\n").replace("\x0b", "\n")
    # Restano i marcatori di campo, note e ancore immagine.
    return "".join(ch for ch in text if ch >= " " or ch in "\n\t").strip()


def extract_doc_text(doc_url: str, dest: Path, session: requests.Session) -> dict:
    """Come extract_pdf_text ma per gli allegati .doc del registro Domino.
    pages=0 come in write_inline_text: il formato non espone un conteggio."""
    try:
        resp = session.get(doc_url, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        print(f"      ❌ download fallito: {exc}")
        return {"extraction_status": "failed", "pages": 0, "chars": 0}

    try:
        text = _doc_to_text(resp.content)
    except Exception as exc:
        print(f"      ❌ estrazione .doc fallita: {exc}")
        return {"extraction_status": "failed", "pages": 0, "chars": 0}

    if not text:
        return {"extraction_status": "empty", "pages": 0, "chars": 0}

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return {"extraction_status": "ok", "pages": 0, "chars": len(text)}


def write_inline_text(text: str, dest: Path) -> dict:
    """Scrive un testo gia' in mano (nessun download) rispettando il contratto di
    extract_pdf_text, cosi' build_stats, il capping e il frontend non cambiano.
    pages=0 marca "documento senza PDF a monte"."""
    text = (text or "").strip()
    if not text:
        return {"extraction_status": "empty", "pages": 0, "chars": 0}

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return {"extraction_status": "ok", "pages": 0, "chars": len(text)}


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
            # Mappa esplicita: con un if/else una strategia sconosciuta finirebbe
            # silenziosamente nel ramo else invece di segnalare l'errore.
            strategies = {
                "liferay": lambda s: fetch_liferay(s),
                "padova_jsonapi": lambda s: fetch_padova(s, args.backfill_years),
                "padova_delibere_nsf": lambda s: fetch_padova_delibere(s),
                "wp_rest": lambda s: fetch_wp_rest(s),
                "bankitalia": lambda s: fetch_bankitalia(s),
                "albo_padova": lambda s: fetch_albo_padova(s),
            }
            items = strategies[source["strategy"]](source)
            print(f"   Trovati {len(items)} documenti")
            for item in items:
                candidates.append((source, item))
        except Exception as exc:
            # Fail-safe: una fonte morta non deve fermare le altre.
            print(f"   ⚠️  Sorgente non raggiungibile, la salto: {exc}")

    # Sessione riusata per tutti i .doc: l'host del registro Domino richiede
    # l'handshake TLS permissivo (vedi legacy_tls_session).
    doc_session = legacy_tls_session()

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
        dest = DOCUMENTS_TEXT_DIR / text_rel
        # Le fonti HTML (comunicati Confindustria) portano gia' il testo con se':
        # non c'e' nessun PDF da scaricare. Le delibere del Comune allegano un
        # .doc, non un PDF, e vivono su un host che vuole la sessione TLS legacy.
        if item.get("text"):
            result = write_inline_text(item["text"], dest)
        elif item.get("content_kind") == "doc":
            result = extract_doc_text(item["link"], dest, doc_session)
        else:
            result = extract_pdf_text(item["link"], dest)

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

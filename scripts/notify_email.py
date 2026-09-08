#!/usr/bin/env python3
"""
Notifica email dei documenti istituzionali nuovi.

Gira dopo docs_scraper.py + enrich_docs.py: confronta l'indice con il registro
degli invii già fatti (data/notified_docs.json) e manda UNA mail con i soli
documenti mai notificati, riproducendo la scheda della dashboard — sintesi,
punti chiave, dati salienti, delibere, interventi — e il link al PDF originale.
Se non c'è niente di nuovo non parte nessuna mail.

Tre vincoli guidano il disegno:

1. Il registro sta in data/ apposta: è la stessa directory che il workflow
   impacchetta in data.tar.gz e ripristina a ogni run. Fuori da data/ andrebbe
   perso a ogni run e la mail successiva rinotificherebbe l'intero indice.
   Al primo avvio il registro non esiste: viene inizializzato con tutti i
   documenti presenti e NON parte nessuna mail (altrimenti arriverebbero ~280
   documenti di storico); si notifica dal giorno dopo in avanti.

2. L'arricchimento Gemini procede a 10 documenti per run mentre lo scraper ne
   raccoglie fino a 80: un documento trovato oggi spesso non ha ancora la
   `summary`. Per questo un documento senza sintesi resta in attesa fino a
   GRACE_HOURS dalla raccolta; scaduta l'attesa parte comunque con l'abstract
   della fonte, che è esattamente il fallback mostrato dalla dashboard.

3. Il registro si aggiorna SOLO dopo un invio riuscito: se l'SMTP fallisce lo
   script esce con 1 e quei documenti rientrano nella mail del giorno dopo.

Senza SMTP_USER/SMTP_PASSWORD/MAIL_TO lo script esce con 0 senza fare nulla,
come enrich_docs.py senza GEMINI_API_KEY: la pipeline non si rompe.

Variabili d'ambiente (secret GitHub o .env in locale):
    MAIL_TO         destinatari, separati da virgola   [obbligatorio]
    SMTP_USER       utente SMTP                        [obbligatorio]
    SMTP_PASSWORD   password/app password              [obbligatorio]
    SMTP_HOST       default smtp.gmail.com
    SMTP_PORT       default 465 (SSL); 587 -> STARTTLS automatico
    SMTP_SECURITY   ssl | starttls | plain             [default dedotto dalla porta]
    MAIL_FROM       mittente, default SMTP_USER
"""

import argparse
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from config import project_settings  # noqa: E402

DOCUMENTS_INDEX = project_settings.DOCUMENTS_INDEX
NOTIFIED_LEDGER = project_settings.DATA_DIR / "notified_docs.json"
DASHBOARD_URL = f"{project_settings.REPO_BASE_URL}/v2/"

# Oltre questa attesa un documento parte anche senza sintesi AI, con
# l'abstract della fonte: meglio una mail un po' più povera che una mail mai
# inviata perché la coda di arricchimento non arriva mai a quel documento.
DEFAULT_GRACE_HOURS = 48

# Gmail tronca le mail sopra i ~102 KB. Oltre questa soglia i documenti
# restano nella mail ma in forma compatta (titolo + link), non omessi.
DEFAULT_MAX_DETAILED = 20

# Stesse etichette di DOC_TYPE_LABELS in DocumentsView.tsx.
DOC_TYPE_LABELS = {
    "bollettino": "Bollettino",
    "misure": "Misure",
    "odg": "Ordine del giorno",
    "delibera": "Delibera",
    "verbale": "Verbale",
    "report_economico": "Report economico",
    "comunicato_industria": "Comunicato industria",
    "ordinanza": "Ordinanza",
}

MONTHS_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
             "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]

MAX_KEY_POINTS = 8
MAX_FIGURES = 8
MAX_DECISIONS = 10
MAX_INTERVENTIONS = 6


# --------------------------------------------------------------------------
# Dati
# --------------------------------------------------------------------------

def load_documents() -> list:
    if not DOCUMENTS_INDEX.exists():
        print(f"⚠️  Indice non trovato: {DOCUMENTS_INDEX}")
        return []
    with open(DOCUMENTS_INDEX, "r", encoding="utf-8") as fh:
        return json.load(fh).get("documents", [])


def load_ledger() -> dict | None:
    """None = registro assente (primo avvio), da distinguere da registro vuoto."""
    if not NOTIFIED_LEDGER.exists():
        return None
    try:
        with open(NOTIFIED_LEDGER, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload.get("notified"), dict):
            return payload
        print("⚠️  Registro notifiche malformato: lo reinizializzo")
    except Exception as exc:
        print(f"⚠️  Registro notifiche illeggibile ({exc}): lo reinizializzo")
    return None


def save_ledger(ledger: dict):
    ledger["updated_at"] = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    NOTIFIED_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(NOTIFIED_LEDGER, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str | None) -> datetime | None:
    """Legge sia gli scraped_at ISO ('2026-08-11T05:57:34Z') sia le date
    dell'indice ('2026-08-11 00:00:00'), che non hanno timezone."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fmt_date(value: str | None) -> str:
    parsed = parse_ts(value)
    if not parsed:
        return value or "data non disponibile"
    return f"{parsed.day} {MONTHS_IT[parsed.month - 1]} {parsed.year}"


def select_new(documents: list, notified: dict, grace_hours: int) -> tuple[list, list]:
    """Ritorna (da_notificare, in_attesa_di_arricchimento), ordinati per data."""
    deadline = now_utc() - timedelta(hours=grace_hours)
    ready, waiting = [], []

    for doc in documents:
        # Senza content_hash non c'è modo di ricordare che è già stato mandato:
        # meglio saltarlo che rimandarlo tutti i giorni.
        if not doc.get("content_hash") or doc["content_hash"] in notified:
            continue
        if not doc.get("summary"):
            scraped = parse_ts(doc.get("scraped_at"))
            # scraped_at assente (documento vecchio, pre-campo): non ha senso
            # trattenerlo, la finestra di grazia è già ampiamente scaduta.
            if scraped and scraped > deadline:
                waiting.append(doc)
                continue
        ready.append(doc)

    ready.sort(key=lambda d: d.get("date", ""), reverse=True)
    return ready, waiting


# --------------------------------------------------------------------------
# Rendering — la scheda della mail rispecchia il pannello di dettaglio della
# dashboard (DocumentsView.tsx), stesso ordine e stesso fallback summary ->
# abstract. HTML con stili inline e tabelle: niente flex/grid, i client di
# posta non li reggono.
# --------------------------------------------------------------------------

C_BG, C_CARD, C_TEXT, C_MUTED, C_ACCENT, C_LINE = (
    "#f4f5f7", "#ffffff", "#1b2430", "#5b6675", "#4f46e5", "#e3e6ea")


def doc_type_label(doc: dict) -> str:
    return DOC_TYPE_LABELS.get(doc.get("doc_type"), doc.get("doc_type", "documento"))


def summary_of(doc: dict) -> tuple[str, str]:
    """(etichetta, testo) — stesso fallback della dashboard."""
    if doc.get("summary"):
        return "Sintesi AI", doc["summary"]
    if doc.get("abstract"):
        return "Abstract della fonte", doc["abstract"]
    return "Sintesi", "Nessuna sintesi disponibile: apri il documento originale."


def render_doc_html(doc: dict) -> str:
    label, summary = summary_of(doc)
    meta = " · ".join(filter(None, [doc.get("source"), doc.get("collana"), fmt_date(doc.get("date"))]))
    blocks = [
        f'<tr><td style="padding:0 0 6px 0;">'
        f'<span style="display:inline-block;font-size:11px;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.06em;color:{C_ACCENT};background:#eef0fe;border-radius:4px;padding:3px 8px;">'
        f'{escape(doc_type_label(doc))}</span></td></tr>',

        f'<tr><td style="padding:2px 0 4px 0;font-size:17px;font-weight:700;line-height:1.35;">'
        f'<a href="{escape(doc.get("link", "") or DASHBOARD_URL)}" style="color:{C_TEXT};text-decoration:none;">'
        f'{escape(doc.get("title", "(senza titolo)"))}</a></td></tr>',

        f'<tr><td style="padding:0 0 12px 0;font-size:12px;color:{C_MUTED};">{escape(meta)}</td></tr>',

        f'<tr><td style="padding:0 0 4px 0;font-size:11px;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.06em;color:{C_MUTED};">{escape(label)}</td></tr>',
        f'<tr><td style="padding:0 0 14px 0;font-size:14px;line-height:1.6;color:{C_TEXT};">'
        f'{escape(summary)}</td></tr>',
    ]

    points = (doc.get("key_points") or [])[:MAX_KEY_POINTS]
    if points:
        items = "".join(
            f'<li style="margin:0 0 5px 0;">{escape(str(p))}</li>' for p in points)
        blocks.append(
            f'<tr><td style="padding:0 0 14px 0;font-size:14px;line-height:1.55;color:{C_TEXT};">'
            f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;'
            f'color:{C_MUTED};margin-bottom:6px;">Punti chiave</div>'
            f'<ul style="margin:0;padding-left:18px;">{items}</ul></td></tr>')

    figures = (doc.get("figures") or [])[:MAX_FIGURES]
    if figures:
        rows = ""
        for fig in figures:
            note = (f'<span style="font-weight:400;color:{C_MUTED};"> '
                    f'({escape(str(fig["note"]))})</span>') if fig.get("note") else ""
            rows += (f'<tr><td style="padding:3px 10px 3px 0;font-size:13px;color:{C_MUTED};">'
                     f'{escape(str(fig.get("label", "")))}</td>'
                     f'<td style="padding:3px 0;font-size:13px;font-weight:700;color:{C_TEXT};">'
                     f'{escape(str(fig.get("value", "")))}{note}</td></tr>')
        blocks.append(
            f'<tr><td style="padding:0 0 14px 0;">'
            f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;'
            f'color:{C_MUTED};margin-bottom:6px;">Dati salienti</div>'
            f'<table cellpadding="0" cellspacing="0" border="0">{rows}</table></td></tr>')

    decisions = (doc.get("decisions") or [])[:MAX_DECISIONS]
    if decisions:
        items = []
        for dec in decisions:
            head = " — ".join(filter(None, [dec.get("numero"), dec.get("oggetto")]))
            tail = " · ".join(filter(None, [dec.get("proponente"), dec.get("esito")]))
            items.append(f'<li style="margin:0 0 5px 0;">{escape(head or "—")}'
                         + (f' <span style="color:{C_MUTED};">({escape(tail)})</span>' if tail else "")
                         + '</li>')
        extra = len(doc.get("decisions") or []) - len(decisions)
        blocks.append(
            f'<tr><td style="padding:0 0 14px 0;font-size:13px;line-height:1.55;color:{C_TEXT};">'
            f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;'
            f'color:{C_MUTED};margin-bottom:6px;">Punti deliberati</div>'
            f'<ul style="margin:0;padding-left:18px;">{"".join(items)}</ul>'
            + (f'<div style="font-size:12px;color:{C_MUTED};margin-top:4px;">e altri {extra} punti</div>' if extra > 0 else "")
            + '</td></tr>')

    interventions = doc.get("interventions") or []
    if interventions:
        items = []
        for iv in interventions[:MAX_INTERVENTIONS]:
            who = escape(str(iv.get("speaker", "")))
            gruppo = f' <span style="color:{C_MUTED};">({escape(str(iv.get("gruppo")))})</span>' if iv.get("gruppo") else ""
            items.append(f'<li style="margin:0 0 5px 0;"><b>{who}</b>{gruppo}: '
                         f'{escape(str(iv.get("argomento", "")))}</li>')
        extra = len(interventions) - len(items)
        blocks.append(
            f'<tr><td style="padding:0 0 14px 0;font-size:13px;line-height:1.55;color:{C_TEXT};">'
            f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;'
            f'color:{C_MUTED};margin-bottom:6px;">Interventi in aula ({len(interventions)})</div>'
            f'<ul style="margin:0;padding-left:18px;">{"".join(items)}</ul>'
            + (f'<div style="font-size:12px;color:{C_MUTED};margin-top:4px;">e altri {extra} interventi sulla dashboard</div>' if extra > 0 else "")
            + '</td></tr>')

    links = [f'<a href="{escape(doc["link"])}" style="color:{C_ACCENT};font-weight:700;text-decoration:none;">'
             f'Apri il documento originale &rarr;</a>'] if doc.get("link") else []
    if doc.get("page_url") and doc.get("page_url") != doc.get("link"):
        links.append(f'<a href="{escape(doc["page_url"])}" style="color:{C_MUTED};text-decoration:none;">'
                     f'Pagina della fonte</a>')
    blocks.append(
        f'<tr><td style="padding:2px 0 0 0;font-size:13px;">{" &nbsp;·&nbsp; ".join(links)}</td></tr>')

    return (f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="background:{C_CARD};border:1px solid {C_LINE};border-radius:10px;'
            f'padding:18px 20px;margin:0 0 14px 0;">{"".join(blocks)}</table>')


def render_compact_html(docs: list) -> str:
    rows = "".join(
        f'<li style="margin:0 0 6px 0;">'
        f'<a href="{escape(d.get("link", "") or DASHBOARD_URL)}" style="color:{C_TEXT};">'
        f'{escape(d.get("title", "(senza titolo)"))}</a> '
        f'<span style="color:{C_MUTED};">— {escape(doc_type_label(d))}, {escape(fmt_date(d.get("date")))}</span>'
        f'</li>' for d in docs)
    return (f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="background:{C_CARD};border:1px solid {C_LINE};border-radius:10px;'
            f'padding:18px 20px;margin:0 0 14px 0;">'
            f'<tr><td style="font-size:11px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:.06em;color:{C_MUTED};padding-bottom:8px;">'
            f'Altri {len(docs)} documenti</td></tr>'
            f'<tr><td style="font-size:13px;line-height:1.5;">'
            f'<ul style="margin:0;padding-left:18px;">{rows}</ul></td></tr></table>')


def render_html(detailed: list, compact: list, waiting: int) -> str:
    total = len(detailed) + len(compact)
    heading = "1 nuovo documento" if total == 1 else f"{total} nuovi documenti"
    body = "".join(render_doc_html(d) for d in detailed)
    if compact:
        body += render_compact_html(compact)
    note = (f'<p style="font-size:12px;color:{C_MUTED};margin:4px 0 0 0;">'
            f'{waiting} documento in attesa di analisi AI: arriverà appena pronto.</p>'
            if waiting == 1 else
            f'<p style="font-size:12px;color:{C_MUTED};margin:4px 0 0 0;">'
            f'{waiting} documenti in attesa di analisi AI: arriveranno appena pronti.</p>'
            if waiting else "")

    return f"""<div style="background:{C_BG};padding:24px 12px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:{C_TEXT};">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;margin:0 auto;">
<tr><td style="padding:0 0 16px 0;">
  <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.12em;color:{C_ACCENT};">Political Review · {escape(project_settings.REGION_NAME)}</div>
  <div style="font-size:22px;font-weight:800;margin-top:4px;">{heading}</div>
  <div style="font-size:13px;color:{C_MUTED};margin-top:2px;">{escape(fmt_date(now_utc().strftime('%Y-%m-%d %H:%M:%S')))}</div>
</td></tr>
<tr><td>{body}</td></tr>
<tr><td style="padding:6px 0 0 0;">
  <a href="{DASHBOARD_URL}" style="color:{C_ACCENT};font-weight:700;text-decoration:none;font-size:13px;">Apri la dashboard &rarr;</a>
  {note}
</td></tr>
</table></div>"""


def render_text(detailed: list, compact: list, waiting: int) -> str:
    lines = [f"Political Review · {project_settings.REGION_NAME}",
             f"{len(detailed) + len(compact)} nuovi documenti — {fmt_date(now_utc().strftime('%Y-%m-%d %H:%M:%S'))}",
             ""]
    for doc in detailed:
        label, summary = summary_of(doc)
        lines += [
            f"[{doc_type_label(doc)}] {doc.get('title', '(senza titolo)')}",
            " · ".join(filter(None, [doc.get("source"), doc.get("collana"), fmt_date(doc.get("date"))])),
            "",
            f"{label}: {summary}",
        ]
        for point in (doc.get("key_points") or [])[:MAX_KEY_POINTS]:
            lines.append(f"  - {point}")
        for fig in (doc.get("figures") or [])[:MAX_FIGURES]:
            note = f" ({fig.get('note')})" if fig.get("note") else ""
            lines.append(f"  · {fig.get('label')}: {fig.get('value')}{note}")
        if doc.get("link"):
            lines.append(f"Documento originale: {doc['link']}")
        if doc.get("page_url") and doc.get("page_url") != doc.get("link"):
            lines.append(f"Pagina della fonte: {doc['page_url']}")
        lines += ["", "-" * 60, ""]

    if compact:
        lines.append(f"Altri {len(compact)} documenti:")
        for doc in compact:
            lines.append(f"  - [{doc_type_label(doc)}] {doc.get('title', '')} — {doc.get('link', '')}")
        lines.append("")

    lines.append(f"Dashboard: {DASHBOARD_URL}")
    if waiting:
        lines.append(f"({waiting} documenti in attesa di analisi AI: arriveranno appena pronti.)")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Invio
# --------------------------------------------------------------------------

def smtp_config() -> dict | None:
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    recipients = [a for a in re.split(r"[,;\s]+", os.environ.get("MAIL_TO", "")) if a]

    if not (user and password and recipients):
        return None

    port = int(os.environ.get("SMTP_PORT", "").strip() or 465)
    security = os.environ.get("SMTP_SECURITY", "").strip().lower()
    return {
        "host": os.environ.get("SMTP_HOST", "").strip() or "smtp.gmail.com",
        "port": port,
        "user": user,
        "password": password,
        "sender": os.environ.get("MAIL_FROM", "").strip() or user,
        "recipients": recipients,
        # Convenzione universale: 465 implicit TLS, 587 STARTTLS.
        "security": security or ("starttls" if port == 587 else "ssl"),
    }


def send_email(cfg: dict, subject: str, text: str, html: str):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Political Review", cfg["sender"]))
    msg["To"] = ", ".join(cfg["recipients"])
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    if cfg["security"] == "ssl":
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=60)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=60)
    with server:
        if cfg["security"] == "starttls":
            server.starttls()
        server.login(cfg["user"], cfg["password"])
        server.send_message(msg)


# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Notifica email dei documenti nuovi")
    parser.add_argument("--grace-hours", type=int, default=DEFAULT_GRACE_HOURS,
                        help="ore di attesa dell'arricchimento AI prima di notificare comunque")
    parser.add_argument("--max-detailed", type=int, default=DEFAULT_MAX_DETAILED,
                        help="documenti con scheda completa; i restanti in elenco compatto")
    parser.add_argument("--dry-run", action="store_true",
                        help="stampa la mail senza inviarla e senza toccare il registro")
    parser.add_argument("--html-out", metavar="PATH",
                        help="salva l'HTML della mail su file (per anteprima)")
    parser.add_argument("--reseed", action="store_true",
                        help="riallinea il registro a tutto l'indice senza inviare nulla")
    args = parser.parse_args()

    documents = load_documents()
    if not documents:
        print("Nessun documento nell'indice: niente da notificare.")
        return 0

    ledger = load_ledger()
    stamp = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")

    if ledger is None or args.reseed:
        reason = "Registro notifiche assente" if ledger is None else "Reseed richiesto"
        ledger = ledger or {"version": 1, "seeded_at": stamp, "notified": {}}
        ledger["notified"].update({d["content_hash"]: stamp for d in documents
                                   if d.get("content_hash")})
        if args.dry_run:
            print(f"{reason}: [dry-run] inizializzerei con {len(ledger['notified'])} documenti.")
            return 0
        save_ledger(ledger)
        print(f"{reason}: inizializzato con {len(ledger['notified'])} documenti. "
              f"Le notifiche partono dai documenti raccolti d'ora in poi.")
        return 0

    ready, waiting = select_new(documents, ledger["notified"], args.grace_hours)

    if not ready:
        print(f"Nessun documento nuovo da notificare "
              f"({len(waiting)} in attesa di arricchimento AI).")
        return 0

    detailed = ready[:args.max_detailed]
    compact = ready[args.max_detailed:]
    total = len(ready)
    subject = (f"[Political Review] 1 nuovo documento — {fmt_date(stamp)}" if total == 1
               else f"[Political Review] {total} nuovi documenti — {fmt_date(stamp)}")
    html = render_html(detailed, compact, len(waiting))
    text = render_text(detailed, compact, len(waiting))

    if args.html_out:
        Path(args.html_out).write_text(html, encoding="utf-8")
        print(f"HTML salvato in {args.html_out}")

    cfg = smtp_config()
    if args.dry_run or cfg is None:
        if cfg is None and not args.dry_run:
            print("SMTP non configurato (servono SMTP_USER, SMTP_PASSWORD, MAIL_TO): "
                  "nessuna mail inviata, registro invariato.")
        print(f"--- {subject} ---")
        print(text)
        return 0

    try:
        send_email(cfg, subject, text, html)
    except Exception as exc:
        # Registro invariato: questi documenti rientrano nella mail di domani.
        print(f"❌ Invio fallito ({type(exc).__name__}: {exc}). Registro invariato, riprovo al prossimo run.")
        return 1

    ledger["notified"].update({d["content_hash"]: stamp for d in ready if d.get("content_hash")})
    save_ledger(ledger)
    print(f"✅ Mail inviata a {', '.join(cfg['recipients'])}: {total} documenti "
          f"({len(detailed)} con scheda completa, {len(compact)} in elenco). "
          f"{len(waiting)} in attesa di arricchimento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

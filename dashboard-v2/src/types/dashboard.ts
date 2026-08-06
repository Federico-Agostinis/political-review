// Documenti istituzionali (Veneto Lavoro, Consiglio comunale di Padova).
// Prodotti da scripts/docs_scraper.py + scripts/enrich_docs.py.
export type DocumentType =
    | 'bollettino'
    | 'misure'
    | 'odg'
    | 'delibere_approvate'
    | 'verbale';

export interface DocumentFigure {
    label: string;
    value: string;
    note?: string | null;
}

export interface DocumentDecision {
    numero?: string | null;
    oggetto: string;
    proponente?: string | null;
    esito?: string | null;
}

export interface DocumentIntervention {
    speaker: string;
    gruppo?: string | null;
    argomento: string;
    posizione?: string | null;
    sintesi: string;
}

export interface DocumentItem {
    // Blocco NewsItem-compatibile: consente di riusare gli helper esistenti
    content_hash: string;
    title: string;
    link: string;
    date: string;
    source: string;
    topic: string;
    zone: string;

    // Blocco documento
    source_id: string;
    collana: string;
    doc_type: DocumentType;
    page_url: string;
    /** Relativo a data/ — es. "documents/veneto_lavoro/2026_06_bussola.txt" */
    text_path: string;
    pages?: number;
    chars?: number;
    extraction_status?: 'ok' | 'empty' | 'failed';
    scraped_at?: string;

    // Blocco arricchimento (null/[] senza GEMINI_API_KEY)
    abstract?: string | null;
    summary?: string | null;
    key_points?: string[];
    figures?: DocumentFigure[];
    decisions?: DocumentDecision[];
    interventions?: DocumentIntervention[];
    entities?: {
        people?: string[];
        organizations?: string[];
        locations?: string[];
    };
    enriched_at?: string | null;
    enriched_by?: string | null;
}

export interface DocumentsIndex {
    generated_at: string;
    total: number;
    stats?: {
        by_source?: Record<string, number>;
        by_doc_type?: Record<string, number>;
        enriched?: number;
        date_range?: { first: string | null; last: string | null };
    };
    documents: DocumentItem[];
}

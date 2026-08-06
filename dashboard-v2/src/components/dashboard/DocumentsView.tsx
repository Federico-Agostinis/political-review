import React, { useMemo, useState } from 'react';
import axios from 'axios';
import {
    Landmark,
    FileText,
    ExternalLink,
    BarChart3,
    PieChart,
    Gavel,
    MessagesSquare,
    Sparkles,
    Users,
    MapPin,
    Building2,
    Search as SearchIcon,
} from 'lucide-react';
import { useDashboardStore } from '../../store/useDashboardStore';
import { Card } from '../ui/Card';
import { DashboardChart } from '../ui/Chart';
import { getDistributionData } from '../../utils/chartUtils';
import type { DocumentItem, DocumentType } from '../../types/dashboard';

const DOC_TYPE_LABELS: Record<DocumentType, string> = {
    bollettino: 'Bollettino',
    misure: 'Misure',
    odg: 'Ordine del giorno',
    delibere_approvate: 'Delibere approvate',
    verbale: 'Verbale',
};

const DOC_TYPE_STYLES: Record<DocumentType, string> = {
    bollettino: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
    misure: 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30',
    odg: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    delibere_approvate: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    verbale: 'bg-violet-500/15 text-violet-400 border-violet-500/30',
};

const formatDate = (value: string) =>
    new Date(value.replace(' ', 'T')).toLocaleDateString('it-IT', {
        day: '2-digit',
        month: 'long',
        year: 'numeric',
    });

export const DocumentsView: React.FC = () => {
    const { documents } = useDashboardStore();

    const [sourceFilter, setSourceFilter] = useState<string>('all');
    const [typeFilter, setTypeFilter] = useState<string>('all');
    const [query, setQuery] = useState('');
    const [selectedHash, setSelectedHash] = useState<string | null>(null);
    const [fullText, setFullText] = useState<Record<string, string>>({});
    const [loadingText, setLoadingText] = useState<string | null>(null);
    const [showText, setShowText] = useState(false);

    const sources = useMemo(
        () => Array.from(new Set(documents.map(d => d.source))).sort(),
        [documents]
    );
    const types = useMemo(
        () => Array.from(new Set(documents.map(d => d.doc_type))).sort(),
        [documents]
    );

    // NB: niente applyFilters — i filtri globali hanno days=30 di default e
    // farebbero sparire un bollettino mensile pubblicato 45 giorni fa.
    const filtered = useMemo(() => {
        const needle = query.trim().toLowerCase();
        return documents
            .filter(d => sourceFilter === 'all' || d.source === sourceFilter)
            .filter(d => typeFilter === 'all' || d.doc_type === typeFilter)
            .filter(d => !needle || `${d.title} ${d.summary ?? ''} ${d.abstract ?? ''}`.toLowerCase().includes(needle))
            .sort((a, b) => b.date.localeCompare(a.date));
    }, [documents, sourceFilter, typeFilter, query]);

    const selected = useMemo(
        () => filtered.find(d => d.content_hash === selectedHash) ?? filtered[0] ?? null,
        [filtered, selectedHash]
    );

    const bySource = useMemo(() => getDistributionData(documents, 'source', 6), [documents]);
    const byType = useMemo(() => {
        const raw = getDistributionData(documents, 'doc_type', 6);
        return {
            ...raw,
            labels: raw.labels.map(l => DOC_TYPE_LABELS[l as DocumentType] ?? l),
        };
    }, [documents]);

    const enrichedCount = useMemo(() => documents.filter(d => d.summary).length, [documents]);

    const loadFullText = async (doc: DocumentItem) => {
        if (showText) {
            setShowText(false);
            return;
        }
        setShowText(true);
        if (fullText[doc.content_hash]) return;

        setLoadingText(doc.content_hash);
        try {
            // Unico idioma corretto sia in dev sia su GitHub Pages: text_path è
            // relativo a data/, quindi si riusa la base-url di useDashboardData.
            const baseUrl = import.meta.env.DEV
                ? '/api-data'
                : new URL('../data', window.location.href).pathname;
            const res = await axios.get(`${baseUrl}/${doc.text_path}`, { responseType: 'text' });
            setFullText(prev => ({ ...prev, [doc.content_hash]: String(res.data) }));
        } catch {
            setFullText(prev => ({ ...prev, [doc.content_hash]: 'Impossibile caricare il testo del documento.' }));
        } finally {
            setLoadingText(null);
        }
    };

    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            {/* Hero */}
            <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-indigo-900 via-slate-900 to-slate-900 border border-white/10 shadow-2xl p-6 md:p-8">
                <div className="relative z-10 flex flex-col md:flex-row md:items-center justify-between gap-6">
                    <div className="flex items-center gap-6">
                        <div className="w-20 h-20 bg-white/10 text-indigo-300 rounded-2xl flex items-center justify-center border border-white/10">
                            <Landmark size={36} />
                        </div>
                        <div>
                            <h2 className="text-3xl font-black text-white mb-1 tracking-tight">Atti e Documenti</h2>
                            <p className="text-indigo-100/70 text-lg font-medium">Fonti primarie degli enti pubblici</p>
                            <div className="flex flex-wrap items-center gap-2 mt-3">
                                {sources.map(s => (
                                    <span key={s} className="text-[10px] font-black uppercase tracking-widest bg-white/5 text-indigo-200 px-2 py-1 rounded border border-white/10">
                                        {s}
                                    </span>
                                ))}
                            </div>
                        </div>
                    </div>
                    <div className="flex gap-6">
                        <div className="text-center">
                            <div className="text-4xl font-black text-white">{documents.length}</div>
                            <div className="text-[10px] uppercase tracking-widest text-indigo-200/60 font-bold">Documenti</div>
                        </div>
                        <div className="text-center">
                            <div className="text-4xl font-black text-indigo-300">{enrichedCount}</div>
                            <div className="text-[10px] uppercase tracking-widest text-indigo-200/60 font-bold">Analizzati AI</div>
                        </div>
                    </div>
                </div>
            </div>

            {documents.length === 0 ? (
                <Card>
                    <div className="p-12 text-center text-text-muted italic text-sm">
                        Nessun documento raccolto. Esegui <code className="text-accent">scripts/docs_scraper.py</code>.
                    </div>
                </Card>
            ) : (
                <>
                    {/* Filtri */}
                    <Card>
                        <div className="flex flex-col lg:flex-row gap-4">
                            <div className="flex-1 relative">
                                <SearchIcon size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                                <input
                                    type="text"
                                    value={query}
                                    onChange={e => setQuery(e.target.value)}
                                    placeholder="Cerca nei documenti..."
                                    className="w-full bg-white/5 border border-border/50 rounded-lg pl-9 pr-3 py-2 text-sm text-text-main placeholder:text-text-muted focus:outline-none focus:border-accent/50"
                                />
                            </div>
                            <select
                                value={sourceFilter}
                                onChange={e => setSourceFilter(e.target.value)}
                                className="bg-white/5 border border-border/50 rounded-lg px-3 py-2 text-sm text-text-main focus:outline-none focus:border-accent/50"
                            >
                                <option value="all">Tutti gli enti</option>
                                {sources.map(s => <option key={s} value={s}>{s}</option>)}
                            </select>
                            <select
                                value={typeFilter}
                                onChange={e => setTypeFilter(e.target.value)}
                                className="bg-white/5 border border-border/50 rounded-lg px-3 py-2 text-sm text-text-main focus:outline-none focus:border-accent/50"
                            >
                                <option value="all">Tutti i tipi</option>
                                {types.map(t => <option key={t} value={t}>{DOC_TYPE_LABELS[t] ?? t}</option>)}
                            </select>
                        </div>
                    </Card>

                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                        {/* Lista */}
                        <div className="lg:col-span-1">
                            <Card
                                title="Documenti"
                                icon={FileText}
                                headerRight={<span className="text-xs font-bold text-accent bg-accent/10 px-2 py-1 rounded-full">{filtered.length}</span>}
                                noPadding
                            >
                                {filtered.length === 0 ? (
                                    <div className="p-10 text-center text-text-muted italic text-sm">Nessun risultato</div>
                                ) : (
                                    <div className="divide-y divide-border/30 max-h-[900px] overflow-y-auto custom-scrollbar">
                                        {filtered.map(doc => {
                                            const isActive = selected?.content_hash === doc.content_hash;
                                            return (
                                                <button
                                                    key={doc.content_hash}
                                                    onClick={() => { setSelectedHash(doc.content_hash); setShowText(false); }}
                                                    className={`w-full text-left p-4 transition-all border-l-4 ${isActive ? 'border-accent bg-accent/5' : 'border-transparent hover:bg-white/[0.02]'}`}
                                                >
                                                    <div className="flex items-center justify-between gap-2 mb-2">
                                                        <span className={`text-[9px] font-black uppercase tracking-wider px-1.5 py-0.5 rounded border ${DOC_TYPE_STYLES[doc.doc_type]}`}>
                                                            {DOC_TYPE_LABELS[doc.doc_type] ?? doc.doc_type}
                                                        </span>
                                                        <span className="text-[10px] text-text-muted">{formatDate(doc.date)}</span>
                                                    </div>
                                                    <h4 className="text-sm font-bold text-text-main leading-tight line-clamp-3">{doc.title}</h4>
                                                    <div className="flex items-center gap-2 mt-2">
                                                        <span className="text-[10px] text-text-muted">{doc.collana}</span>
                                                        {!doc.summary && (
                                                            <span className="text-[8px] font-black uppercase text-amber-400/80">• in attesa AI</span>
                                                        )}
                                                    </div>
                                                </button>
                                            );
                                        })}
                                    </div>
                                )}
                            </Card>
                        </div>

                        {/* Dettaglio */}
                        <div className="lg:col-span-2 space-y-6">
                            {selected && (
                                <Card
                                    title={selected.title}
                                    subtitle={`${selected.source} · ${selected.collana} · ${formatDate(selected.date)}`}
                                    icon={Landmark}
                                    headerRight={
                                        <a
                                            href={selected.link}
                                            target="_blank"
                                            rel="noreferrer"
                                            className="flex items-center gap-1.5 text-[10px] font-black uppercase bg-accent/20 text-accent hover:bg-accent/30 px-3 py-2 rounded-lg transition-colors whitespace-nowrap"
                                        >
                                            <ExternalLink size={12} /> PDF originale
                                        </a>
                                    }
                                >
                                    <div className="space-y-5">
                                        {/* Sintesi */}
                                        <div>
                                            <div className="flex items-center gap-2 text-[10px] font-black text-accent uppercase tracking-widest mb-2">
                                                <Sparkles size={12} />
                                                {selected.summary ? 'Sintesi AI' : 'Abstract della fonte'}
                                                {!selected.summary && (
                                                    <span className="text-amber-400/80 normal-case tracking-normal font-medium">
                                                        — in attesa di analisi AI
                                                    </span>
                                                )}
                                            </div>
                                            <p className="text-sm text-text-muted leading-relaxed">
                                                {selected.summary || selected.abstract || 'Nessuna sintesi disponibile.'}
                                            </p>
                                        </div>

                                        {/* Punti chiave */}
                                        {!!selected.key_points?.length && (
                                            <div>
                                                <div className="text-[10px] font-black text-text-muted uppercase tracking-widest mb-2">Punti chiave</div>
                                                <ul className="space-y-1.5">
                                                    {selected.key_points.map((point, i) => (
                                                        <li key={i} className="text-sm text-text-muted flex gap-2">
                                                            <span className="text-accent mt-0.5">•</span>
                                                            <span>{point}</span>
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        )}

                                        {/* Dati quantitativi */}
                                        {!!selected.figures?.length && (
                                            <div>
                                                <div className="flex items-center gap-2 text-[10px] font-black text-text-muted uppercase tracking-widest mb-2">
                                                    <BarChart3 size={12} /> Dati salienti
                                                </div>
                                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                                    {selected.figures.map((fig, i) => (
                                                        <div key={i} className="bg-white/5 rounded-lg p-3 border border-border/30">
                                                            <div className="text-[10px] text-text-muted uppercase tracking-wide">{fig.label}</div>
                                                            <div className="text-lg font-black text-text-main">{fig.value}</div>
                                                            {fig.note && <div className="text-[10px] text-text-muted italic mt-0.5">{fig.note}</div>}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}

                                        {/* Delibere */}
                                        {!!selected.decisions?.length && (
                                            <div>
                                                <div className="flex items-center gap-2 text-[10px] font-black text-text-muted uppercase tracking-widest mb-2">
                                                    <Gavel size={12} /> Punti all'ordine del giorno ({selected.decisions.length})
                                                </div>
                                                <div className="overflow-x-auto">
                                                    <table className="w-full text-left text-xs">
                                                        <thead>
                                                            <tr className="text-[9px] uppercase tracking-wider text-text-muted border-b border-border/40">
                                                                <th className="py-2 pr-3 font-black">N.</th>
                                                                <th className="py-2 pr-3 font-black">Oggetto</th>
                                                                <th className="py-2 pr-3 font-black">Proponente</th>
                                                                <th className="py-2 font-black">Esito</th>
                                                            </tr>
                                                        </thead>
                                                        <tbody className="divide-y divide-border/20">
                                                            {selected.decisions.map((dec, i) => (
                                                                <tr key={i} className="align-top">
                                                                    <td className="py-2 pr-3 text-text-muted whitespace-nowrap">{dec.numero || '—'}</td>
                                                                    <td className="py-2 pr-3 text-text-main">{dec.oggetto}</td>
                                                                    <td className="py-2 pr-3 text-text-muted whitespace-nowrap">{dec.proponente || '—'}</td>
                                                                    <td className="py-2 text-text-muted whitespace-nowrap">{dec.esito || '—'}</td>
                                                                </tr>
                                                            ))}
                                                        </tbody>
                                                    </table>
                                                </div>
                                            </div>
                                        )}

                                        {/* Interventi in aula */}
                                        {!!selected.interventions?.length && (
                                            <div>
                                                <div className="flex items-center gap-2 text-[10px] font-black text-text-muted uppercase tracking-widest mb-2">
                                                    <MessagesSquare size={12} /> Interventi in aula ({selected.interventions.length})
                                                </div>
                                                <div className="space-y-2 max-h-[420px] overflow-y-auto custom-scrollbar pr-1">
                                                    {selected.interventions.map((iv, i) => (
                                                        <div key={i} className="bg-white/5 rounded-lg p-3 border-l-2 border-violet-500/40">
                                                            <div className="flex flex-wrap items-center gap-2 mb-1">
                                                                <span className="text-xs font-bold text-text-main">{iv.speaker}</span>
                                                                {iv.gruppo && (
                                                                    <span className="text-[9px] font-black uppercase bg-white/5 text-text-muted px-1.5 py-0.5 rounded">{iv.gruppo}</span>
                                                                )}
                                                                {iv.posizione && (
                                                                    <span className="text-[9px] font-black uppercase bg-violet-500/15 text-violet-300 px-1.5 py-0.5 rounded">{iv.posizione}</span>
                                                                )}
                                                            </div>
                                                            <div className="text-[11px] text-accent/80 font-medium mb-1">{iv.argomento}</div>
                                                            <p className="text-[11px] text-text-muted leading-relaxed italic">{iv.sintesi}</p>
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}

                                        {/* Entità */}
                                        {selected.entities && (
                                            <div className="flex flex-col gap-2">
                                                {([
                                                    ['people', Users, 'Persone'],
                                                    ['organizations', Building2, 'Organizzazioni'],
                                                    ['locations', MapPin, 'Luoghi'],
                                                ] as const).map(([key, Icon, label]) => {
                                                    const values = selected.entities?.[key] ?? [];
                                                    if (!values.length) return null;
                                                    return (
                                                        <div key={key} className="flex items-start gap-2">
                                                            <Icon size={12} className="text-text-muted mt-1 shrink-0" />
                                                            <div className="flex flex-wrap gap-1.5">
                                                                <span className="sr-only">{label}</span>
                                                                {values.slice(0, 14).map((v, i) => (
                                                                    <span key={i} className="text-[10px] bg-white/5 text-text-muted px-2 py-0.5 rounded-full border border-border/30">{v}</span>
                                                                ))}
                                                            </div>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        )}

                                        {/* Testo integrale */}
                                        <div className="pt-3 border-t border-border/30">
                                            <button
                                                onClick={() => loadFullText(selected)}
                                                className="flex items-center gap-2 text-[10px] font-black uppercase bg-white/5 text-text-muted hover:bg-white/10 hover:text-text-main px-3 py-2 rounded-lg transition-all"
                                            >
                                                <FileText size={12} />
                                                {showText ? 'Nascondi testo integrale' : 'Testo integrale'}
                                                {selected.pages ? <span className="opacity-60">({selected.pages} pp.)</span> : null}
                                            </button>

                                            {showText && (
                                                <div className="mt-3 p-4 bg-black/40 rounded-xl border border-white/5">
                                                    {loadingText === selected.content_hash ? (
                                                        <div className="text-xs text-accent py-4 text-center animate-pulse">Caricamento…</div>
                                                    ) : (
                                                        <pre className="text-[11px] text-text-muted leading-relaxed whitespace-pre-wrap font-serif max-h-[500px] overflow-y-auto custom-scrollbar">
                                                            {fullText[selected.content_hash] ?? 'Nessun testo disponibile.'}
                                                        </pre>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </Card>
                            )}

                            {/* Grafici */}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <Card title="Documenti per ente" icon={PieChart}>
                                    <div className="h-[260px] w-full mt-2">
                                        <DashboardChart type="doughnut" data={bySource} />
                                    </div>
                                </Card>
                                <Card title="Documenti per tipo" icon={BarChart3}>
                                    <div className="h-[260px] w-full mt-2">
                                        <DashboardChart type="bar" data={byType} options={{ indexAxis: 'y' as const }} />
                                    </div>
                                </Card>
                            </div>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
};

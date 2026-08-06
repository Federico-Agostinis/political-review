import React from 'react';
import { Navbar } from './Navbar';
import { useDashboardStore } from '../../store/useDashboardStore';
import { useDashboardData } from '../../hooks/useDashboardData';
import { getRegionConfig } from '../../utils/regionConfig';

export const Layout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const { isLoading } = useDashboardStore();
    useDashboardData(); // Initialize data fetching

    return (
        <div className="min-h-screen bg-bg-main text-text-main flex flex-col">
            <Navbar />

            <main className="flex-1 px-4 lg:px-8 max-w-7xl mx-auto w-full pt-24">
                {isLoading ? (
                    <div className="fixed inset-0 flex flex-col items-center justify-center bg-bg-main z-40">
                        <div className="relative">
                            <div className="w-16 h-16 border-4 border-accent/20 border-t-accent rounded-full animate-spin" />
                            <div className="absolute inset-0 flex items-center justify-center">
                                <div className="w-8 h-8 bg-accent/10 rounded-full animate-pulse" />
                            </div>
                        </div>
                        <p className="mt-6 text-text-muted font-medium tracking-widest animate-pulse uppercase text-xs">
                            Sincronizzazione Dati...
                        </p>
                    </div>
                ) : (
                    <div className="animate-in fade-in slide-in-from-bottom-4 duration-700">
                        {children}
                    </div>
                )}
            </main>

            <footer className="py-8 px-4 border-t border-border/30 text-center mt-12">
                <p className="text-text-muted text-xs">
                    Political Intelligence Dashboard © {new Date().getFullYear()} — Monitoraggio documenti istituzionali {getRegionConfig().REGION_NAME}
                </p>
            </footer>
        </div>
    );
};

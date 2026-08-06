import { create } from 'zustand';
import type { DocumentItem } from '../types/dashboard';

interface DashboardState {
    // Documenti istituzionali (Veneto Lavoro, Consiglio comunale di Padova)
    documents: DocumentItem[];
    setDocuments: (docs: DocumentItem[]) => void;

    isLoading: boolean;
    setIsLoading: (loading: boolean) => void;
}

export const useDashboardStore = create<DashboardState>((set) => ({
    documents: [],
    setDocuments: (documents) => set({ documents }),

    isLoading: true,
    setIsLoading: (isLoading) => set({ isLoading }),
}));

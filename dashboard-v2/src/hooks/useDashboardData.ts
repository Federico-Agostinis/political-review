import { useEffect } from 'react';
import axios from 'axios';
import { useDashboardStore } from '../store/useDashboardStore';
import type { DocumentsIndex } from '../types/dashboard';

export const useDashboardData = () => {
    const { setDocuments, setIsLoading } = useDashboardStore();

    useEffect(() => {
        const fetchData = async () => {
            setIsLoading(true);
            try {
                const baseUrl = import.meta.env.DEV ?
                    '/api-data' :
                    new URL('../data', window.location.href).pathname;
                const t = new Date().getTime();

                const docsRes = await axios.get<DocumentsIndex>(`${baseUrl}/documents_index.json?t=${t}`)
                    .catch(() => ({ data: { generated_at: '', total: 0, documents: [] } as DocumentsIndex }));

                setDocuments(docsRes.data?.documents ?? []);
            } catch (error) {
                console.error('Failed to fetch dashboard data:', error);
                setDocuments([]);
            } finally {
                setIsLoading(false);
            }
        };

        fetchData();
    }, [setDocuments, setIsLoading]);
};

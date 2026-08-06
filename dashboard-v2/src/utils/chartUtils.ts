/**
 * Premium Liquid Glass Color Palette
 */
export const CHART_COLORS = {
    primary: '#6366f1',   // Indigo 500
    success: '#10b981',   // Emerald 500
    warning: '#f59e0b',   // Amber 500
    danger: '#f43f5e',    // Rose 500
    neutral: '#64748b',   // Slate 500 (Better than previous grey)
    accent: '#3b82f6',    // Blue 500
    vibrant_teal: '#2dd4bf',
    vibrant_rose: '#fb7185',
    vibrant_amber: '#fbbf24',
    vibrant_indigo: '#818cf8',
    transparent_primary: 'rgba(99, 102, 241, 0.1)',
    palette: [
        '#6366f1', // Indigo
        '#10b981', // Emerald
        '#f59e0b', // Amber
        '#f43f5e', // Rose
        '#8b5cf6', // Violet
        '#06b6d4', // Cyan
        '#ec4899', // Pink
        '#14b8a6', // Teal
        '#3b82f6', // Blue
        '#f97316'  // Orange
    ]
};

export const getDistributionData = <T,>(data: T[], field: keyof T, limit = 10) => {
    const counts: Record<string, number> = {};
    data.forEach(item => {
        const value = String(item[field] || 'Sconosciuto');
        counts[value] = (counts[value] || 0) + 1;
    });

    const sorted = Object.entries(counts)
        .sort(([, a], [, b]) => b - a)
        .slice(0, limit);

    return {
        labels: sorted.map(([label]) => label),
        datasets: [
            {
                label: 'Volume',
                data: sorted.map(([, count]) => count),
                backgroundColor: sorted.map((_, i) => CHART_COLORS.palette[i % CHART_COLORS.palette.length]),
                borderRadius: 8,
                hoverOpacity: 0.9
            },
        ],
    };
};

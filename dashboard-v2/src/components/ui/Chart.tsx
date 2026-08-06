import React from 'react';
import {
    Chart as ChartJS,
    CategoryScale,
    LinearScale,
    PointElement,
    LineElement,
    BarElement,
    ArcElement,
    Title,
    Tooltip,
    Legend,
    Filler,
} from 'chart.js';
import type { ChartOptions, ChartData } from 'chart.js';
import { Line, Bar, Doughnut, Bubble } from 'react-chartjs-2';

ChartJS.register(
    CategoryScale,
    LinearScale,
    PointElement,
    LineElement,
    BarElement,
    ArcElement,
    Title,
    Tooltip,
    Legend,
    Filler
);

interface ChartProps {
    type: 'line' | 'bar' | 'doughnut' | 'bubble';
    data: ChartData<any>;
    options?: ChartOptions<any>;
    height?: number;
    onDataSelect?: (index: number, datasetIndex: number) => void;
}

export const DashboardChart: React.FC<ChartProps> = ({ type, data, options, height = 300, onDataSelect }) => {
    const handleclick = (_evt: any, elements: any[]) => {
        if (elements && elements.length > 0 && onDataSelect) {
            const { index, datasetIndex } = elements[0];
            onDataSelect(index, datasetIndex);
        }
    };

    const defaultOptions: ChartOptions<any> = {
        responsive: true,
        maintainAspectRatio: false,
        onClick: handleclick,
        plugins: {
            legend: {
                display: type === 'doughnut',
                position: 'bottom',
                labels: {
                    color: '#94a3b8',
                    font: { size: 10, weight: '500' },
                    usePointStyle: true,
                    padding: 15
                }
            },
            tooltip: {
                backgroundColor: '#1e293b',
                titleColor: '#f1f5f9',
                bodyColor: '#94a3b8',
                borderColor: 'rgba(148, 163, 184, 0.1)',
                borderWidth: 1,
                padding: 10,
                displayColors: false,
            }
        },
        scales: type !== 'doughnut' ? {
            x: {
                grid: { display: false },
                ticks: { color: '#94a3b8', font: { size: 10 } }
            },
            y: {
                grid: { color: 'rgba(148, 163, 184, 0.05)' },
                ticks: { color: '#94a3b8', font: { size: 10 } }
            }
        } : undefined
    };

    const combinedOptions = { ...defaultOptions, ...options };

    switch (type) {
        case 'line':
            return <Line data={data} options={combinedOptions} height={height} />;
        case 'bar':
            return <Bar data={data} options={combinedOptions} height={height} />;
        case 'doughnut':
            return <Doughnut data={data} options={combinedOptions} height={height} />;
        case 'bubble':
            return <Bubble data={data} options={combinedOptions} height={height} />;
        default:
            return null;
    }
};

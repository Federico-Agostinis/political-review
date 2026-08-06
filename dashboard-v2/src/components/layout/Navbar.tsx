import React from 'react';
import { Landmark } from 'lucide-react';
import { getRegionConfig } from '../../utils/regionConfig';

export const Navbar: React.FC = () => {
    return (
        <nav className="fixed top-0 left-0 right-0 h-16 z-50 px-4 flex items-center glass border-b border-border/50">
            <div className="flex items-center gap-3">
                <Landmark className="w-6 h-6 text-accent" />
                <div className="flex flex-col">
                    <span className="font-bold text-lg leading-none text-text-main">Political Intelligence</span>
                    <span className="text-[10px] uppercase tracking-wider text-text-muted">
                        Documenti Istituzionali {getRegionConfig().REGION_NAME}
                    </span>
                </div>
            </div>
        </nav>
    );
};

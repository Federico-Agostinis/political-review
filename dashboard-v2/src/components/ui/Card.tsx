import React from 'react';
import { clsx } from 'clsx';
import type { LucideIcon } from 'lucide-react';

interface CardProps {
    title?: React.ReactNode;
    subtitle?: React.ReactNode;
    icon?: LucideIcon;
    headerRight?: React.ReactNode;
    children: React.ReactNode;
    className?: string;
    bodyClassName?: string;
    noPadding?: boolean;
    allowOverflow?: boolean;
}

export const Card: React.FC<CardProps> = ({
    title,
    subtitle,
    icon: Icon,
    headerRight,
    children,
    className,
    bodyClassName,
    noPadding = false,
    allowOverflow = false
}) => {
    return (
        <div className={clsx(
            "glass glass-hover transition-all duration-300",
            !allowOverflow && "overflow-hidden",
            className
        )}>
            {(title || headerRight || Icon) && (
                <div className="px-5 py-4 border-b border-border/50 flex items-center justify-between bg-white/[0.02]">
                    <div className="flex items-center gap-3">
                        {Icon && (
                            <div className="p-1.5 bg-white/5 rounded-lg text-text-muted">
                                <Icon size={16} />
                            </div>
                        )}
                        <div>
                            {title && <h3 className="font-bold text-sm tracking-tight flex items-center gap-2">{title}</h3>}
                            {subtitle && <p className="text-[10px] text-text-muted uppercase font-medium tracking-wider">{subtitle}</p>}
                        </div>
                    </div>
                    {headerRight && <div>{headerRight}</div>}
                </div>
            )}
            <div className={clsx(!noPadding && "p-5", bodyClassName)}>
                {children}
            </div>
        </div>
    );
};

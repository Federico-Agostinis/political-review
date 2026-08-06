/**
 * Region configuration loaded at runtime from /region_config.json
 * This allows changing the region without rebuilding the app.
 */

export interface RegionConfig {
    REGION_NAME: string;
    REGION_ID: string;
}

// Default fallback (matches project_settings.py defaults)
const DEFAULT_CONFIG: RegionConfig = {
    REGION_NAME: 'Veneto',
    REGION_ID: 'veneto',
};

let _config: RegionConfig = DEFAULT_CONFIG;
let _loaded = false;

export async function loadRegionConfig(): Promise<RegionConfig> {
    if (_loaded) return _config;
    try {
        const base = import.meta.env.BASE_URL || './';
        const res = await fetch(`${base}region_config.json`);
        if (res.ok) {
            const data = await res.json();
            _config = { ...DEFAULT_CONFIG, ...data };
        }
    } catch {
        // silently fall back to default
    }
    _loaded = true;
    return _config;
}

export function getRegionConfig(): RegionConfig {
    return _config;
}

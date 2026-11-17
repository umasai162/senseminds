/**
 * MULTI GAS ANALYZER IoT GATEWAY - API Client Service
 * Resolves Backend URL dynamically.
 * Priority:
 * 1. Previously verified working URL in this browser session.
 * 2. window.location.hostname:8000 (auto-matches localhost, 127.0.0.1, or client's LAN IP).
 * 3. VITE_API_URL from .env
 * 4. Fallback candidate list: http://localhost:8000, http://192.168.1.6:8000, http://192.168.1.37:8000
 */

let activeBackendUrl = null;

export const getApiBaseUrl = () => {
  if (activeBackendUrl) {
    return activeBackendUrl;
  }
  // If running in browser, connect to port 8000 on the same host that served the frontend
  if (typeof window !== 'undefined' && window.location && window.location.hostname) {
    const host = window.location.hostname;
    if (host) {
      return `http://${host}:8000`;
    }
  }
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL.replace(/\/+$/, '');
  }
  return 'http://localhost:8000';
};

export const setApiBaseUrl = (url) => {
  if (url) {
    activeBackendUrl = url.replace(/\/+$/, '');
  }
};

export const fetchHealth = async () => {
  const current = getApiBaseUrl();
  const host = typeof window !== 'undefined' && window.location ? window.location.hostname : 'localhost';

  // List candidate URLs to probe with auto-fallback
  const candidates = [
    current,
    `http://${host}:8000`,
    'http://localhost:8000',
    'http://127.0.0.1:8000',
    'http://192.168.1.37:8000',
    'http://192.168.1.6:8000'
  ];

  // De-duplicate candidates preserving order
  const uniqueCandidates = [...new Set(candidates.filter(Boolean))];

  for (const cand of uniqueCandidates) {
    try {
      const res = await fetch(`${cand}/health`, { signal: AbortSignal.timeout(2000) });
      if (res.ok) {
        const data = await res.json();
        activeBackendUrl = cand;
        return data;
      }
    } catch (err) {
      // Continue to next candidate
    }
  }

  throw new Error(`Unable to reach FastAPI backend on LAN. Probed: ${uniqueCandidates.join(', ')}`);
};

export const fetchLatestReadings = async (sensorId = null) => {
  const baseUrl = getApiBaseUrl();
  const url = sensorId !== null
    ? `${baseUrl}/readings/latest?sensor_id=${sensorId}`
    : `${baseUrl}/readings/latest?all_sensors=true`;
  const res = await fetch(url);
  if (!res.ok) {
    if (res.status === 404) return [];
    throw new Error(`Failed to fetch latest readings (HTTP ${res.status})`);
  }
  return await res.json();
};

export const fetchReadingsHistory = async (params = {}) => {
  const baseUrl = getApiBaseUrl();
  const query = new URLSearchParams();
  if (params.sensorId) query.set('sensor_id', params.sensorId);
  if (params.gasType) query.set('gas_type', params.gasType);
  if (params.limit) query.set('limit', params.limit);
  if (params.skip) query.set('skip', params.skip);

  const qs = query.toString();
  const url = `${baseUrl}/readings/${qs ? `?${qs}` : ''}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch readings history (HTTP ${res.status})`);
  return await res.json();
};

export const fetchStats = async () => {
  const url = `${getApiBaseUrl()}/readings/stats`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch sensor stats (HTTP ${res.status})`);
  return await res.json();
};

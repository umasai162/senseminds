import React, { useState, useEffect, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import DashboardView from './components/DashboardView';
import ScrubbersView from './components/ScrubbersView';
import ScrubberDetailView from './components/ScrubberDetailView';
import AlertsView from './components/AlertsView';
import ReportsView from './components/ReportsView';
import SettingsView from './components/SettingsView';
import LogSheetView from './components/LogSheetView';
import ThemeToggle from './components/ThemeToggle';
import LandingPage from './components/LandingPage';
import AdminLogin from './components/AdminLogin';
import AICopilotChat from './components/AICopilotChat';
import { Activity, Menu } from 'lucide-react';

import { INITIAL_SENSORS, INITIAL_ALERTS } from './data/scrubberData';
import { getApiBaseUrl, fetchHealth } from './api';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [previousTab, setPreviousTab] = useState('dashboard');
  const [selectedSensorId, setSelectedSensorId] = useState(null);

  const [sensors, setSensors] = useState(INITIAL_SENSORS);
  const [alerts, setAlerts] = useState(INITIAL_ALERTS);
  const [toast, setToast] = useState(null);

  // LAN Gateway connectivity state
  const [gatewayConnected, setGatewayConnected] = useState(null); // null (checking), true, false
  const [gatewayError, setGatewayError] = useState(null);
  const [lastSyncTime, setLastSyncTime] = useState(null);
  const [isSyncing, setIsSyncing] = useState(false);
  
  // Landing Page & Login State Machine: 'landing' | 'login' | 'dashboard'
  const [viewState, setViewState] = useState('landing');
  const [isExiting, setIsExiting] = useState(false);

  // Mobile navigation drawer state
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // Theme state: 'light' (default) | 'dark'
  const [theme, setTheme] = useState('light');

  const handleEnterDashboard = () => {
    setIsExiting(true);
    setTimeout(() => {
      setViewState('login');
      setIsExiting(false);
    }, 450);
  };

  const handleLoginSuccess = () => {
    setIsExiting(true);
    setTimeout(() => {
      setViewState('dashboard');
      setIsExiting(false);
      showToast('🔑 Administrator Session Authorized', 'success');
    }, 450);
  };

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  // Fetch live telemetry from FastAPI Backend over LAN
  const fetchTelemetry = useCallback(async () => {
    setIsSyncing(true);
    const apiUrl = getApiBaseUrl();

    try {
      // 1. Check health
      await fetchHealth();
      setGatewayConnected(true);
      setGatewayError(null);

      // 2. Fetch latest telemetry
      const response = await fetch(`${apiUrl}/readings/?limit=100`);
      if (response.ok) {
        const data = await response.json();
        setLastSyncTime(new Date().toLocaleTimeString());

        if (Array.isArray(data) && data.length > 0) {
          // Map latest reading per sensor_id (data is descending order)
          const latestBySensor = {};
          data.forEach(reading => {
            const sid = String(reading.sensor_id);
            if (!latestBySensor[sid]) {
              latestBySensor[sid] = reading;
            }
          });

          setSensors(prev => {
            const updated = prev.map(s => {
              const latest = latestBySensor[String(s.sensor_id)];
              if (latest) {
                const isOffline = latest.status?.toLowerCase() === 'offline';
                return {
                  ...s,
                  device_name: latest.device_name || s.device_name,
                  gas_type: latest.gas_type || s.gas_type,
                  unit: latest.unit || s.unit,
                  currentValue: latest.value,
                  status: latest.status,
                  isOffline: isOffline,
                  lastUpdated: new Date(latest.timestamp).toLocaleTimeString()
                };
              }
              return s;
            });

            // Dynamically register any sensors discovered in telemetry not present in initial list
            const existingIds = new Set(prev.map(s => String(s.sensor_id)));
            const newlyDiscovered = [];
            Object.entries(latestBySensor).forEach(([sid, r]) => {
              if (!existingIds.has(sid)) {
                newlyDiscovered.push({
                  sensor_id: r.sensor_id,
                  device_name: r.device_name || `Sensor ${r.sensor_id}`,
                  gas_type: r.gas_type || 'Gas',
                  currentValue: r.value,
                  unit: r.unit || 'ppm',
                  status: r.status,
                  isOffline: r.status?.toLowerCase() === 'offline',
                  lastUpdated: new Date(r.timestamp).toLocaleTimeString(),
                  location: 'IoT Gateway Dynamic Channel',
                  aiObservations: 'Active sensor telemetry received via LAN gateway.'
                });
              }
            });

            return [...updated, ...newlyDiscovered];
          });
        }
      }
    } catch (err) {
      console.warn("Failed to fetch live telemetry:", err);
      setGatewayConnected(false);
      setGatewayError(`LAN Gateway Unreachable at ${apiUrl}. Verify FastAPI backend is running.`);
    } finally {
      setIsSyncing(false);
    }
  }, []);

  useEffect(() => {
    if (viewState !== 'dashboard') return;

    // Initial fetch
    fetchTelemetry();

    // Poll every 5 seconds (5-10 second requirement)
    const interval = setInterval(fetchTelemetry, 5000);
    return () => clearInterval(interval);
  }, [viewState, fetchTelemetry]);

  const handleRefreshTelemetry = () => {
    fetchTelemetry();
    showToast('🔄 Synchronizing live sensor telemetry...', 'info');
  };


  const toggleTheme = () => {
    setTheme(prev => (prev === 'light' ? 'dark' : 'light'));
  };

  // Toast notification helper with 4s auto-dismiss
  const showToast = (message, type = 'info') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  // Select a sensor to open Detail page
  const handleSelectSensor = (id) => {
    setSelectedSensorId(id);
    if (activeTab !== 'detail') {
      setPreviousTab(activeTab);
    }
    setActiveTab('detail');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // Go back from Detail page
  const handleBackFromDetail = () => {
    setActiveTab(previousTab || 'dashboard');
  };

  // Toggle Alert Status (Open <-> Resolved)
  const handleToggleAlertStatus = (alertId) => {
    setAlerts(prev => prev.map(alt => {
      if (alt.id === alertId) {
        const nextStatus = alt.status === 'Open' ? 'Resolved' : 'Open';
        showToast(
          nextStatus === 'Resolved'
            ? `Alert ${alt.id} marked as Resolved.`
            : `Alert ${alt.id} reopened.`,
          nextStatus === 'Resolved' ? 'success' : 'warning'
        );
        return { ...alt, status: nextStatus };
      }
      return alt;
    }));
  };

  const activeAlertsCount = alerts.filter(a => a.status === 'Open').length;
  const currentSensor = sensors.find(s => s.sensor_id === selectedSensorId) || sensors[0];

  // Render Landing Page
  if (viewState === 'landing') {
    return (
      <div className={isExiting ? "landing-exit-active" : ""} style={{ transition: 'opacity 0.45s ease-out' }}>
        <LandingPage onEnterDashboard={handleEnterDashboard} />
      </div>
    );
  }

  // Render Admin Login Page
  if (viewState === 'login') {
    return (
      <div className={isExiting ? "landing-exit-active" : ""} style={{ transition: 'opacity 0.45s ease-out' }}>
        <AdminLogin onLoginSuccess={handleLoginSuccess} />
      </div>
    );
  }

  return (
    <div className="app-container" style={{ minHeight: '100vh', background: 'var(--bg-main)', color: 'var(--text-main)' }}>
      
      {/* 1. Sidebar Navigation (Supports Mobile Drawer) */}
      <Sidebar
        activeTab={activeTab === 'detail' ? previousTab : activeTab}
        setActiveTab={(tab) => {
          setActiveTab(tab);
          if (tab !== 'detail') setSelectedSensorId(null);
        }}
        activeAlertsCount={activeAlertsCount}
        scrubbersCount={sensors.length}
        isOpen={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
      />

      {/* 2. Main Content Container */}
      <main style={{
        flex: 1,
        padding: '0 32px 40px 32px',
        maxWidth: '1600px',
        margin: '0 auto',
        width: '100%',
        boxSizing: 'border-box',
        display: 'flex',
        flexDirection: 'column'
      }}>
        
        {/* Top Header Bar */}
        <header style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px 0',
          borderBottom: '1px solid var(--border-subtle)',
          marginBottom: '20px'
        }}>
          {/* Mobile Menu Toggle Button */}
          <button
            onClick={() => setMobileNavOpen(true)}
            className="mobile-header-btn btn-interactive"
            aria-label="Open menu"
            style={{
              padding: '8px 12px',
              borderRadius: '8px',
              border: '1px solid var(--border-color)',
              background: 'var(--bg-card)',
              color: 'var(--text-main)',
              fontSize: '13px',
              fontWeight: 700,
              cursor: 'pointer',
              alignItems: 'center',
              gap: '6px'
            }}
          >
            <Menu size={18} color="var(--primary)" />
            <span>Menu</span>
          </button>

          <div style={{ marginLeft: 'auto' }}>
            <ThemeToggle theme={theme} onToggleTheme={toggleTheme} />
          </div>
        </header>

        {/* Floating Toast Notification */}
        {toast && (
          <div className="toast-slide-in" style={{
            position: 'fixed',
            top: '70px',
            right: '24px',
            zIndex: 9999,
            padding: '12px 20px',
            borderRadius: '10px',
            background: toast.type === 'warning' ? 'var(--accent-warning)' : toast.type === 'danger' ? 'var(--accent-danger)' : 'var(--primary)',
            color: '#ffffff',
            fontWeight: 700,
            fontSize: '13px',
            boxShadow: 'var(--card-shadow)',
            display: 'flex',
            alignItems: 'center',
            gap: '10px'
          }}>
            {toast.message}
          </div>
        )}

        {/* View Routing with Smooth Page Transitions */}
        <div key={activeTab + (selectedSensorId || '')} className="page-transition">
          {activeTab === 'dashboard' && (
            <DashboardView
              sensors={sensors}
              onSelectSensor={handleSelectSensor}
              onRefresh={handleRefreshTelemetry}
              gatewayConnected={gatewayConnected}
              gatewayError={gatewayError}
              lastSyncTime={lastSyncTime}
              isSyncing={isSyncing}
            />
          )}

          {activeTab === 'logsheet' && (
            <LogSheetView />
          )}

          {activeTab === 'scrubbers' && (
            <ScrubbersView
              scrubbers={sensors}
              onSelectScrubber={handleSelectSensor}
            />
          )}

          {activeTab === 'detail' && (
            <ScrubberDetailView
              scrubber={currentSensor}
              alerts={alerts}
              onBack={handleBackFromDetail}
              onResolveAlert={(id) => handleToggleAlertStatus(id)}
            />
          )}

          {activeTab === 'alerts' && (
            <AlertsView
              alerts={alerts}
              onToggleAlertStatus={handleToggleAlertStatus}
              onSelectScrubber={handleSelectSensor}
            />
          )}

          {activeTab === 'reports' && (
            <ReportsView
              scrubbers={sensors}
              alerts={alerts}
            />
          )}

          {activeTab === 'settings' && (
            <SettingsView />
          )}

          {activeTab === 'copilot' && (
            <AICopilotChat />
          )}
        </div>

        {/* Universal Footer */}
        <footer style={{
          marginTop: 'auto',
          paddingTop: '30px',
          borderTop: '1px solid var(--border-subtle)',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          fontSize: '11.5px',
          color: 'var(--text-dim)',
          gap: '10px'
        }}>
          <span>SenseMinds Industrial Environmental Intelligence v5.0 • Gas Analyzer Dashboard</span>
          <span>Laurus Labs Pharmaceutical Unit 1 Operations</span>
          <span style={{ color: 'var(--accent-success)', fontWeight: 700 }}>Telemetry Engine: Active</span>
        </footer>

      </main>

    </div>
  );
}

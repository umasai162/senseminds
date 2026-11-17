// Comprehensive Sensor Telemetry & H2S Gas Monitoring Data

export const ALERTS_RULES = {
  HEALTHY_MAX_PPM: 5.0,
  WARNING_MAX_PPM: 10.0,
  CRITICAL_MAX_PPM: 20.0,
};

export const evaluateStatus = (val, gasType = 'PPM', statusStr = 'ONLINE') => {
  if (statusStr && statusStr.toLowerCase() === 'offline') return 'Offline';
  if (val === null || val === undefined) return 'Offline';

  if (gasType && gasType.toLowerCase() === 'ph') {
    if (val >= 10.0 && val <= 12.0) return 'Healthy';
    if ((val >= 7.0 && val < 10.0) || (val > 12.0 && val <= 12.5)) return 'Warning';
    return 'Critical';
  }

  if (val < ALERTS_RULES.WARNING_MAX_PPM) return 'Healthy';
  if (val < ALERTS_RULES.CRITICAL_MAX_PPM) return 'Warning';
  return 'Critical';
};

export const getStatusColor = (status) => {
  switch ((status || '').toLowerCase()) {
    case 'healthy':
      return { text: '#2E7D32', bg: 'rgba(46, 125, 50, 0.12)', border: 'rgba(46, 125, 50, 0.35)' };
    case 'warning':
      return { text: '#d97706', bg: 'rgba(217, 119, 6, 0.12)', border: 'rgba(217, 119, 6, 0.35)' };
    case 'critical':
      return { text: '#dc2626', bg: 'rgba(220, 38, 38, 0.12)', border: 'rgba(220, 38, 38, 0.35)' };
    case 'offline':
      return { text: '#94a3b8', bg: 'rgba(148, 163, 184, 0.12)', border: 'rgba(148, 163, 184, 0.35)' };
    default:
      return { text: '#455A64', bg: 'rgba(69, 90, 100, 0.12)', border: 'rgba(69, 90, 100, 0.35)' };
  }
};

export const POSSIBLE_CAUSES = [
  'Sensor calibration issue',
  'High H2S concentration leak',
  'Ventilation malfunction'
];

export const RECOMMENDED_ACTIONS = [
  'Verify H2S sensor calibration',
  'Inspect immediate area for leaks',
  'Schedule preventive maintenance'
];

// Seed sensors list (all values start neutral/offline awaiting live telemetry)
export const INITIAL_SENSORS = [
  {
    sensor_id: 2,
    device_name: 'Uniphos 500DT (H2S)',
    gas_type: 'H2S',
    currentValue: null,
    avgValue: null,
    minValue: null,
    maxValue: null,
    unit: 'ppm',
    status: 'OFFLINE',
    lastUpdated: 'Awaiting telemetry...',
    location: 'Main Gas Pipeline / Gateway Unit',
    aiObservations: 'Awaiting telemetry from Raspberry Pi IoT Gateway.',
    maintenanceHistory: []
  },
  {
    sensor_id: 1,
    device_name: 'Carbon Monoxide (CO)',
    gas_type: 'CO',
    currentValue: null,
    avgValue: null,
    minValue: null,
    maxValue: null,
    unit: 'PPM',
    status: 'OFFLINE',
    lastUpdated: 'Awaiting telemetry...',
    location: 'Main Gas Pipeline',
    aiObservations: 'Awaiting telemetry from Raspberry Pi IoT Gateway.',
    maintenanceHistory: []
  },
  {
    "sensor_id": "AMMONIA-SCRUBBER",
    "device_name": "Ammonia (NH3) Neutralization Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone SCRUBBER",
    "aiObservations": "Scrubber Telemetry Offline. Inspect Modbus Transmitters and Field PLC Cabinets",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-604",
    "device_name": "Hydrochloric Acid (HCl) Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 604",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "FES-101",
    "device_name": "Acid Fume Extraction (HCl / HNO3) Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 101",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-601",
    "device_name": "Hydrogen Sulfide (H2S) Caustic Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 601",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-502",
    "device_name": "Sulfur Dioxide (SO2) Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 502",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-501",
    "device_name": "Chlorine Gas (Cl2) Absorption Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 501",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-701",
    "device_name": "VOC / Solvent Vapor Carbon Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 701",
    "aiObservations": "Scrubber Telemetry Offline. Inspect Modbus Transmitters and Field PLC Cabinets",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "FES-102",
    "device_name": "Alkaline Amine (NH3 / Amines) Fume Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 102",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-304",
    "device_name": "Sodium Hypochlorite (NaOCl) Odor Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 304",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-605",
    "device_name": "Bromine Vapor (Br2) Neutralization Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 605",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-303",
    "device_name": "Nitrogen Oxides (NOx) Reduction Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 303",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-301",
    "device_name": "Hydrogen Sulfide (H2S) Primary Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 301",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "GCB-6-SCRUBBER",
    "device_name": "Chlorinated Gas Absorption Column (Cl2)",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone SCRUBBER",
    "aiObservations": "Scrubber Telemetry Offline. Inspect Modbus Transmitters and Field PLC Cabinets",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-107",
    "device_name": "Methyl Chloride (CH3Cl) Off-Gas Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 107",
    "aiObservations": "Scrubber Telemetry Offline. Inspect Modbus Transmitters and Field PLC Cabinets",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-106",
    "device_name": "Triethylamine (TEA) Basic Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 106",
    "aiObservations": "Scrubber Telemetry Offline. Inspect Modbus Transmitters and Field PLC Cabinets",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-105",
    "device_name": "Acetic Acid (CH3COOH) Vapor Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 105",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-901",
    "device_name": "Emergency Caustic Tail-Gas (NaOH) Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 901",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-102",
    "device_name": "Phosgene / Carbonyl Gas (COCl2) Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 102",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-104",
    "device_name": "Sulfuric Acid (H2SO4) Mist Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 104",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-302",
    "device_name": "Hydrogen Bromide (HBr) Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 302",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-606",
    "device_name": "Formaldehyde (HCHO) Recovery Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 606",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  },
  {
    "sensor_id": "SCB-103",
    "device_name": "Acid Gas Multi-Stage (HCl / H2S) Scrubber",
    "gas_type": "pH",
    "currentValue": null,
    "avgValue": null,
    "minValue": null,
    "maxValue": null,
    "unit": "pH",
    "status": "OFFLINE",
    "lastUpdated": "Awaiting telemetry...",
    "location": "Unit 1 - Production Zone 103",
    "aiObservations": "Awaiting telemetry from Raspberry Pi IoT Gateway.",
    "maintenanceHistory": []
  }
];

// Time-series trend generator for Daily (24h), Weekly (7d), and Monthly (30d)
export const generateTrendData = (sensor, filter = 'Daily') => {
  const currentValue = sensor.currentValue;
  const baseAvg = sensor.avgValue || currentValue;
  const min = sensor.minValue || Math.max(0, currentValue - 2);
  const max = sensor.maxValue || currentValue + 5;

  const points = filter === 'Daily' ? 24 : filter === 'Weekly' ? 7 : 30;
  const data = [];

  for (let i = points - 1; i >= 0; i--) {
    let label = '';
    let variance = (Math.sin(i * 0.8) * 0.4) + (Math.cos(i * 0.3) * 0.2);
    
    if (filter === 'Daily') {
      const hour = (new Date().getHours() - i + 24) % 24;
      label = `${hour.toString().padStart(2, '0')}:00`;
    } else if (filter === 'Weekly') {
      const d = new Date();
      d.setDate(d.getDate() - i);
      label = d.toLocaleDateString('en-US', { weekday: 'short' });
    } else {
      const d = new Date();
      d.setDate(d.getDate() - i);
      label = `${d.getMonth() + 1}/${d.getDate()}`;
    }

    // Ensure the last point matches currentValue
    let value = i === 0 ? currentValue : Math.max(min, Math.min(max, +(baseAvg + variance).toFixed(2)));

    data.push({
      time: label,
      value: value,
      warningThreshold: ALERTS_RULES.WARNING_MAX_PPM,
      criticalThreshold: ALERTS_RULES.CRITICAL_MAX_PPM,
    });
  }

  return data;
};

// Initial system alerts dataset following the explicit required rules
export const INITIAL_ALERTS = [];

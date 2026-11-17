#!/usr/bin/env python3
"""
MULTI GAS ANALYZER IoT GATEWAY - Sensor Reader & LAN Sync Daemon
================================================================
Platform: Raspberry Pi IoT Gateway
Target Backend: FastAPI CEMS Ingestion Engine (LAN: http://192.168.1.37:8000)
Data Persistence:
  - CSV Log: /home/pi/gas_gateway_data/readings.csv
  - Offline Buffer: /home/pi/gas_gateway_data/pending_readings.jsonl

Functions:
  1. Reads Uniphos 500DT H2S gas sensor via RS485 Modbus RTU every 10 seconds.
  2. Persists every collected reading locally to CSV on the Raspberry Pi.
  3. Transmits telemetry via HTTP POST to the backend over LAN.
  4. Stores unsent readings to pending_readings.jsonl on network dropouts.
  5. Automatically flushes and synchronizes pending readings in batches of 100 on reconnection.
  6. Deduplicates readings to ensure zero data corruption or repeated records.
"""

import os
import sys
import time
import json
import csv
import argparse
from datetime import datetime, timezone
from pathlib import Path
import requests

# -------------------------------------------------------------
# Configuration & Environment Defaults
# -------------------------------------------------------------
# Candidate Backend URLs (will auto-discover active host if primary times out)
configured_url = os.getenv("BACKEND_URL")
CANDIDATE_BACKEND_URLS = [
    configured_url,
    "http://192.168.1.6:8000/readings/",
    "http://192.168.1.37:8000/readings/",
    "http://192.168.55.108:8000/readings/",
    "http://127.0.0.1:8000/readings/",
    "http://localhost:8000/readings/"
]
# Remove None and duplicate values preserving order
CANDIDATE_BACKEND_URLS = [u for i, u in enumerate(CANDIDATE_BACKEND_URLS) if u and u not in CANDIDATE_BACKEND_URLS[:i]]
BACKEND_URL = CANDIDATE_BACKEND_URLS[0]

if os.name == "nt":
    DEFAULT_DATA_DIR = str(Path(__file__).resolve().parent / "gas_gateway_data")
else:
    DEFAULT_DATA_DIR = "/home/pi/gas_gateway_data"

GATEWAY_DATA_DIR = os.getenv("GATEWAY_DATA_DIR", DEFAULT_DATA_DIR)

# Fall back to local ./gas_gateway_data if default Linux path is not writable (e.g. testing on Windows/macOS)
try:
    data_dir_path = Path(GATEWAY_DATA_DIR)
    data_dir_path.mkdir(parents=True, exist_ok=True)
    DATA_DIR = str(data_dir_path)
except Exception:
    fallback_dir = Path(__file__).resolve().parent / "gas_gateway_data"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    DATA_DIR = str(fallback_dir)

CSV_FILE_PATH = os.path.join(DATA_DIR, "readings.csv")
PENDING_FILE_PATH = os.path.join(DATA_DIR, "pending_readings.jsonl")

PORT = os.getenv("MODBUS_PORT", "/dev/ttyUSB0")
SLAVE_ID = int(os.getenv("SLAVE_ID", "2"))
GAS_REGISTER_ADDRESS = int(os.getenv("GAS_REGISTER_ADDRESS", "31"))
READ_INTERVAL = int(os.getenv("READ_INTERVAL", "10")) # 10 seconds
SYNC_BATCH_SIZE = int(os.getenv("SYNC_BATCH_SIZE", "100"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "5"))

# Optional Modbus import with graceful fallback
MODBUS_AVAILABLE = False
try:
    from pymodbus.client import ModbusSerialClient
    MODBUS_AVAILABLE = True
except ImportError:
    ModbusSerialClient = None

# -------------------------------------------------------------
# Local Storage Helpers
# -------------------------------------------------------------
CSV_HEADERS = ["timestamp", "sensor_id", "device_name", "gas_type", "value", "unit", "status"]

def append_to_csv(reading: dict):
    """Appends a single reading to the local CSV audit file."""
    file_exists = os.path.exists(CSV_FILE_PATH)
    try:
        with open(CSV_FILE_PATH, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "timestamp": reading.get("timestamp"),
                "sensor_id": reading.get("sensor_id"),
                "device_name": reading.get("device_name"),
                "gas_type": reading.get("gas_type"),
                "value": reading.get("value") if reading.get("value") is not None else "",
                "unit": reading.get("unit"),
                "status": reading.get("status")
            })
    except Exception as e:
        print(f"[Gateway Error] Failed to write to CSV ({CSV_FILE_PATH}): {e}")

def append_to_pending(reading: dict):
    """Appends an unsent reading to the pending_readings.jsonl queue."""
    try:
        # Check if already pending to avoid duplicates
        if is_reading_pending(reading):
            return
        with open(PENDING_FILE_PATH, mode="a", encoding="utf-8") as f:
            f.write(json.dumps(reading) + "\n")
    except Exception as e:
        print(f"[Gateway Error] Failed to write to pending queue: {e}")

def is_reading_pending(reading: dict) -> bool:
    """Checks whether a reading with the identical timestamp and sensor_id is already queued."""
    if not os.path.exists(PENDING_FILE_PATH):
        return False
    try:
        with open(PENDING_FILE_PATH, mode="r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("timestamp") == reading.get("timestamp") and item.get("sensor_id") == reading.get("sensor_id"):
                    return True
    except Exception:
        pass
    return False

def get_pending_count() -> int:
    """Returns number of unsent records currently queued in pending_readings.jsonl."""
    if not os.path.exists(PENDING_FILE_PATH):
        return 0
    try:
        with open(PENDING_FILE_PATH, mode="r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0

def flush_pending_readings(backend_url: str, batch_size: int = SYNC_BATCH_SIZE):
    """
    Synchronizes offline buffered records to the backend in batches of up to SYNC_BATCH_SIZE.
    Uses POST /readings/ batch format: {"readings": [...]}.
    """
    if not os.path.exists(PENDING_FILE_PATH):
        return

    try:
        with open(PENDING_FILE_PATH, mode="r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
    except Exception as e:
        print(f"[Sync Error] Cannot read pending file: {e}")
        return

    if not lines:
        return

    batch_lines = lines[:batch_size]
    remaining_lines = lines[batch_size:]

    batch_readings = []
    for l in batch_lines:
        try:
            batch_readings.append(json.loads(l))
        except Exception:
            continue

    if not batch_readings:
        return

    print(f"[Gateway Sync] Attempting to flush {len(batch_readings)} buffered reading(s) to backend...")

    try:
        payload = {"readings": batch_readings}
        res = requests.post(backend_url, json=payload, timeout=HTTP_TIMEOUT)
        if res.status_code in (200, 201):
            # Rewrite pending file with remaining lines
            with open(PENDING_FILE_PATH, mode="w", encoding="utf-8") as f:
                for rem in remaining_lines:
                    f.write(rem + "\n")
            print(f"[Gateway Sync] Successfully flushed {len(batch_readings)} record(s). Remaining pending: {len(remaining_lines)}")
        else:
            print(f"[Gateway Sync] Server returned status code {res.status_code}. Pending records preserved.")
    except requests.exceptions.RequestException as e:
        print(f"[Gateway Sync] Network unreachable during flush: {e}. Retry scheduled.")

# -------------------------------------------------------------
# Modbus Client & Sensor Interfacing
# -------------------------------------------------------------
client = None

def get_modbus_client():
    global client
    if not MODBUS_AVAILABLE:
        return None
    if client is None:
        client = ModbusSerialClient(
            port=PORT,
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=2
        )
    return client

def read_sensor_hardware(simulate: bool = False):
    """
    Reads H2S gas value from Uniphos 500DT via RS485 Modbus.
    If simulate=True, generates a realistic sensor reading for testing without hardware.
    """
    if simulate:
        import random
        base_h2s = 5.4 + random.uniform(-0.6, 1.2)
        return round(base_h2s, 2), "ONLINE"

    if not MODBUS_AVAILABLE:
        print("[Gateway Warning] pymodbus is not installed. Sensor state marked OFFLINE.")
        return None, "OFFLINE"

    mb_client = get_modbus_client()
    try:
        if not mb_client.connected:
            connected = mb_client.connect()
            if not connected:
                return None, "OFFLINE"

        response = mb_client.read_holding_registers(
            address=GAS_REGISTER_ADDRESS,
            count=1,
            slave=SLAVE_ID
        )

        if response.isError():
            return None, "OFFLINE"
        
        gas_val = float(response.registers[0])
        return gas_val, "ONLINE"

    except Exception as e:
        print(f"[Modbus Error] Communication failed: {e}")
        try:
            if mb_client:
                mb_client.close()
        except Exception:
            pass
        return None, "OFFLINE"

# -------------------------------------------------------------
# Main Gateway Loop
# -------------------------------------------------------------
def run_gateway(simulate: bool = False):
    print("=" * 60)
    print(" MULTI GAS ANALYZER IoT GATEWAY - SENSOR DAEMON")
    print("=" * 60)
    print(f"Device       : Raspberry Pi IoT Gateway")
    print(f"Sensor       : Uniphos 500DT (H2S)")
    print(f"Port / Slave : {PORT} (Slave ID: {SLAVE_ID})")
    print(f"Interval     : {READ_INTERVAL} seconds")
    print(f"Backend Target : {BACKEND_URL}")
    print(f"Data Dir     : {DATA_DIR}")
    print(f"CSV Path     : {CSV_FILE_PATH}")
    print(f"Pending Path : {PENDING_FILE_PATH}")
    print(f"Mode         : {'SIMULATION / TEST' if simulate else 'HARDWARE MODBUS'}")
    print("=" * 60)
    print()

    try:
        while True:
            # ISO timestamp formatted for ISO 8601 parsing
            now_dt = datetime.now(timezone.utc)
            timestamp = now_dt.isoformat()

            # 1. Read Sensor
            gas_value, status = read_sensor_hardware(simulate=simulate)

            payload = {
                "sensor_id": SLAVE_ID,
                "device_name": "Uniphos 500DT",
                "gas_type": "H2S",
                "value": gas_value,
                "unit": "ppm",
                "status": status,
                "timestamp": timestamp
            }

            # 2. Persist locally to CSV on Raspberry Pi SD card
            append_to_csv(payload)

            # 3. Flush any previously accumulated pending readings first
            flush_pending_readings(BACKEND_URL, batch_size=SYNC_BATCH_SIZE)

            # 4. Transmit current reading to FastAPI backend
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Sensor {payload['sensor_id']} ({payload['gas_type']}): {payload['value'] if payload['value'] is not None else '---'} {payload['unit']} [{payload['status']}]")

            transmitted = False
            for target_url in CANDIDATE_BACKEND_URLS:
                try:
                    res = requests.post(target_url, json=payload, timeout=HTTP_TIMEOUT)
                    if res.status_code in (200, 201):
                        BACKEND_URL = target_url
                        print(f"  └─>> [HTTP {res.status_code}] Transmitted successfully to {target_url}.")
                        transmitted = True
                        break
                    else:
                        print(f"  └─>> [HTTP {res.status_code}] Rejected by {target_url}.")
                except requests.exceptions.RequestException:
                    continue

            if not transmitted:
                print(f"  └─>> [Network Error] Could not reach backend ({BACKEND_URL}). Storing in pending queue.")
                append_to_pending(payload)

            pending_count = get_pending_count()
            if pending_count > 0:
                print(f"  └─>> Pending offline queue: {pending_count} reading(s) stored locally.")

            # Sleep for configured interval
            time.sleep(READ_INTERVAL)

    except KeyboardInterrupt:
        print("\n[Gateway] Daemon stopped by user.")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
            print("[Gateway] RS485 Modbus connection closed.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi Gas Analyzer IoT Gateway Daemon")
    parser.add_argument("--simulate", "-s", action="store_true", help="Run with simulated sensor data (for verification without hardware)")
    args = parser.parse_args()

    # Also check environment variable GATEWAY_MODE
    sim_mode = args.simulate or os.getenv("GATEWAY_MODE", "").lower() in ("simulate", "simulation", "test", "true", "1")
    run_gateway(simulate=sim_mode)

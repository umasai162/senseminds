#!/usr/bin/env python3
"""
multi_gas_gateway_same_port.py

Industrial-style Modbus RTU gateway for MULTIPLE gas analyzers on the
SAME RS-485 / USB-RS485 port.

Target Backend: FastAPI CEMS Ingestion Engine (LAN: http://192.168.55.108:8000 / http://192.168.1.37:8000)

IMPORTANT:
1. Multiple analyzers may share one RS-485 bus.
2. Every analyzer on the same bus MUST have a UNIQUE Modbus slave ID.
3. Example:
       /dev/ttyUSB0
          ID 1 -> CO
          ID 2 -> H2S
          ID 3 -> NO2
          ID 4 -> SO2
4. Two physical analyzers with the SAME Modbus ID on the SAME RS-485 bus
   cannot be reliably distinguished by software. Change their slave IDs.

This version:
- Automatically detects serial ports.
- Keeps each physical serial port open.
- Scans a configurable Modbus ID range ONCE at startup.
- Detects multiple analyzers on the SAME physical port.
- Shows how many analyzers are connected to each port.
- Reads all discovered analyzers every 5 seconds.
- A valid Modbus response containing value 0 is ONLINE.
- Uses retries during discovery/read operations.
- Does not use SQLite.
- Saves CSV data to SD card.
- Stores ONLINE readings locally when backend is unavailable.
- Synchronizes queued data when backend returns.
"""

import os
import sys
import csv
import json
import time
import threading
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    serial = None
    SERIAL_AVAILABLE = False


# ============================================================
# APPLICATION CONFIGURATION
# ============================================================

APP_NAME = "MULTI GAS ANALYZER IoT GATEWAY"

READ_INTERVAL = int(os.getenv("READ_INTERVAL", "5"))

# Auto-configured LAN backend with candidate fallbacks
DEFAULT_BACKEND = os.getenv("BACKEND_URL", "http://192.168.55.108:8000/readings/")
CANDIDATE_BACKEND_URLS = [
    DEFAULT_BACKEND,
    "http://192.168.55.108:8000/readings/",
    "http://192.168.1.37:8000/readings/",
    "http://192.168.1.6:8000/readings/",
    "http://127.0.0.1:8000/readings/",
    "http://localhost:8000/readings/",
]
# De-duplicate preserving order
CANDIDATE_BACKEND_URLS = [u for i, u in enumerate(CANDIDATE_BACKEND_URLS) if u and u not in CANDIDATE_BACKEND_URLS[:i]]
BACKEND_URL = CANDIDATE_BACKEND_URLS[0]

# Base Directory: use /home/pi/gas_gateway_data on Raspberry Pi, fallback to ./gas_gateway_data on Windows/dev
if os.name == "nt":
    DEFAULT_BASE_DIR = str(Path(__file__).resolve().parent / "gas_gateway_data")
else:
    DEFAULT_BASE_DIR = "/home/pi/gas_gateway_data"

BASE_DIR = os.getenv("GATEWAY_DATA_DIR", DEFAULT_BASE_DIR)

try:
    Path(BASE_DIR).mkdir(parents=True, exist_ok=True)
    # Test writability
    test_file = os.path.join(BASE_DIR, ".write_test")
    with open(test_file, "w") as f:
        f.write("ok")
    os.remove(test_file)
except Exception:
    BASE_DIR = str(Path(__file__).resolve().parent / "gas_gateway_data")
    Path(BASE_DIR).mkdir(parents=True, exist_ok=True)

CSV_FILE = os.path.join(BASE_DIR, "gas_readings.csv")
PENDING_FILE = os.path.join(BASE_DIR, "pending_readings.jsonl")

SYNC_BATCH_SIZE = int(os.getenv("SYNC_BATCH_SIZE", "100"))


# ============================================================
# MODBUS / SERIAL CONFIGURATION
# ============================================================

BAUDRATE = 9600
BYTESIZE = 8 if not serial else serial.EIGHTBITS
PARITY = 'N' if not serial else serial.PARITY_NONE
STOPBITS = 1 if not serial else serial.STOPBITS_ONE

DISCOVERY_TIMEOUT = 0.30
READ_TIMEOUT = 1.00

DISCOVERY_RETRIES = 2
READ_RETRIES = 2

MODBUS_FUNCTION_READ_HOLDING = 3

# Analyzer manual:
# Register 32 = zero-based Modbus address 31.
PROCESS_VALUE_REGISTER = 31

# Decimal register = manual register 15 -> zero-based address 14.
DECIMAL_REGISTER = 14

# Gas-name register = manual register 17 -> zero-based address 16.
GAS_NAME_REGISTER = 16
GAS_NAME_REGISTER_COUNT = 8

# Unit register = manual register 25 -> zero-based address 24.
UNIT_REGISTER = 24
UNIT_REGISTER_COUNT = 4


# ============================================================
# MODBUS ID DISCOVERY
# ============================================================
SCAN_ID_START = 1
SCAN_ID_END = 16


# ============================================================
# OPTIONAL KNOWN SENSOR CONFIGURATION
# ============================================================
KNOWN_SENSORS = {
    1: {
        "gas_type": "CO",
        "decimals": 0,
        "unit": "PPM",
        "device_name": "Uniphos Gas Analyzer",
    },
    2: {
        "gas_type": "H2S",
        "decimals": 1,
        "unit": "PPM",
        "device_name": "Uniphos Gas Analyzer",
    },
}


# ============================================================
# GLOBAL STATE
# ============================================================

open_ports = {}
port_locks = {}
connected_analyzers = {}
state_lock = threading.Lock()
backend_online = False
SIMULATE_MODE = False


# ============================================================
# DIRECTORY / CSV
# ============================================================

def create_directories():
    os.makedirs(BASE_DIR, exist_ok=True)


def initialize_csv():
    if os.path.exists(CSV_FILE) and os.path.getsize(CSV_FILE) > 0:
        return

    with open(CSV_FILE, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([
            "timestamp",
            "sensor_id",
            "port",
            "device_name",
            "gas_type",
            "value",
            "unit",
            "status",
        ])


# ============================================================
# TIME
# ============================================================

def get_timestamp():
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    return now.strftime("%Y-%m-%dT%H:%M:%S%z")[:-2] + ":" + now.strftime("%z")[-2:]


# ============================================================
# MODBUS CRC16
# ============================================================

def modbus_crc(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


# ============================================================
# MODBUS REQUEST
# ============================================================

def build_modbus_request(slave_id, register_address, register_count=1):
    frame = bytes([
        slave_id,
        MODBUS_FUNCTION_READ_HOLDING,
        (register_address >> 8) & 0xFF,
        register_address & 0xFF,
        (register_count >> 8) & 0xFF,
        register_count & 0xFF,
    ])
    crc = modbus_crc(frame)
    frame += bytes([
        crc & 0xFF,
        (crc >> 8) & 0xFF,
    ])
    return frame


# ============================================================
# SERIAL PORT FAILURE / RECOVERY
# ============================================================

def mark_serial_port_failed(ser, reason="serial port failure"):
    failed_port = getattr(ser, "port", None)
    if not failed_port:
        return

    print(f"\nSERIAL PORT FAILURE: {failed_port} | {reason}")
    try:
        ser.close()
    except Exception:
        pass

    if open_ports.get(failed_port) is ser:
        open_ports.pop(failed_port, None)
        port_locks.pop(failed_port, None)


def read_modbus_registers(ser, slave_id, register_address, count=1,
                          timeout=READ_TIMEOUT, retries=READ_RETRIES):
    if not ser:
        return None

    request = build_modbus_request(
        slave_id,
        register_address,
        count,
    )

    try:
        ser.timeout = timeout
        ser.write_timeout = timeout
    except Exception as exc:
        mark_serial_port_failed(ser, f"serial configuration failed: {exc}")
        return None

    try:
        for attempt in range(retries):
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                ser.write(request)
                ser.flush()

                header = ser.read(3)
                if len(header) != 3:
                    continue

                response_slave = header[0]
                response_function = header[1]
                byte_count = header[2]

                if response_slave != slave_id:
                    continue

                if response_function & 0x80:
                    ser.read(2)
                    continue

                if response_function != MODBUS_FUNCTION_READ_HOLDING:
                    continue

                expected_byte_count = count * 2
                if byte_count != expected_byte_count:
                    continue

                remaining = ser.read(byte_count + 2)
                if len(remaining) != byte_count + 2:
                    continue

                response = header + remaining
                received_crc = response[-2] | (response[-1] << 8)
                calculated_crc = modbus_crc(response[:-2])

                if received_crc != calculated_crc:
                    continue

                data = response[3:3 + byte_count]
                registers = []
                for i in range(count):
                    high_byte = data[i * 2]
                    low_byte = data[i * 2 + 1]
                    registers.append((high_byte << 8) | low_byte)

                return registers

            except (serial.SerialException, OSError, IOError) as exc:
                mark_serial_port_failed(ser, f"serial I/O error: {exc}")
                return None
            except Exception:
                continue

        return None

    except Exception as exc:
        mark_serial_port_failed(ser, f"serial port error: {exc}")
        return None


# ============================================================
# READ ASCII REGISTERS
# ============================================================

def registers_to_ascii(registers):
    raw = bytearray()
    for value in registers:
        raw.append((value >> 8) & 0xFF)
        raw.append(value & 0xFF)
    text = raw.decode("ascii", errors="ignore")
    return text.replace("\x00", "").strip()


# ============================================================
# PORT DETECTION
# ============================================================

def detect_serial_ports():
    if not SERIAL_AVAILABLE:
        return []

    candidates = []
    preferred_ports = [
        "/dev/serial0",
        "/dev/serial1",
        "/dev/ttyAMA0",
        "/dev/ttyAMA1",
        "/dev/ttyS0",
        "/dev/ttyS1",
    ]

    for port in preferred_ports:
        if os.path.exists(port):
            candidates.append(port)

    try:
        for info in serial.tools.list_ports.comports():
            device = info.device
            description = f"{info.description} {info.manufacturer} {info.product}".lower()
            if getattr(info, 'vid', None) == 0x2C7C or "quectel" in description or "ec25" in description:
                continue
            candidates.append(device)
    except Exception:
        pass

    final_ports = []
    physical_devices = set()

    for port in candidates:
        try:
            real_path = os.path.realpath(port)
            if real_path in physical_devices:
                continue
            physical_devices.add(real_path)
            final_ports.append(port)
        except Exception:
            if port not in final_ports:
                final_ports.append(port)

    return final_ports


# ============================================================
# OPEN / CLOSE PORTS
# ============================================================

def open_serial_port(port):
    if not SERIAL_AVAILABLE:
        return None
    try:
        ser = serial.Serial(
            port=port,
            baudrate=BAUDRATE,
            bytesize=BYTESIZE,
            parity=PARITY,
            stopbits=STOPBITS,
            timeout=READ_TIMEOUT,
            write_timeout=READ_TIMEOUT,
        )
        time.sleep(0.05)
        return ser
    except Exception as exc:
        print(f"Cannot open {port}: {exc}")
        return None


def refresh_serial_ports(verbose=True):
    global open_ports, port_locks

    detected = detect_serial_ports()
    detected_set = set(detected)
    currently_open = set(open_ports.keys())

    removed_ports = sorted(currently_open - detected_set)
    added_ports = sorted(detected_set - currently_open)

    if verbose and (added_ports or removed_ports):
        print()
        print("=" * 78)
        print("SERIAL PORT CHANGE DETECTED")
        print("=" * 78)

    for old_port in removed_ports:
        print(f"  DISCONNECTED: {old_port}")
        ser = open_ports.pop(old_port, None)
        port_locks.pop(old_port, None)
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    for port in added_ports:
        ser = open_serial_port(port)
        if ser is not None:
            open_ports[port] = ser
            port_locks[port] = threading.Lock()
            print(f"  RECONNECTED/NEW PORT: {port}")

    if verbose and (added_ports or removed_ports):
        print("=" * 78)

    return added_ports, removed_ports


def open_detected_ports():
    detected = detect_serial_ports()
    print()
    print("=" * 78)
    print("AUTOMATIC SERIAL PORT DETECTION")
    print("=" * 78)

    if not detected:
        print("No serial ports detected.")
        print("=" * 78)
        return []

    for port in detected:
        print(f"  Detected: {port}")

    for port in detected:
        if port in open_ports:
            continue
        ser = open_serial_port(port)
        if ser is not None:
            open_ports[port] = ser
            port_locks[port] = threading.Lock()
            print(f"  OPEN: {port}")

    print("=" * 78)
    return list(open_ports.keys())


def close_all_ports():
    for port, ser in list(open_ports.items()):
        try:
            ser.close()
        except Exception:
            pass
    open_ports.clear()
    port_locks.clear()


# ============================================================
# ANALYZER METADATA
# ============================================================

def get_default_config(sensor_id):
    if sensor_id in KNOWN_SENSORS:
        return dict(KNOWN_SENSORS[sensor_id])

    return {
        "gas_type": f"MODBUS_ID_{sensor_id}",
        "decimals": 0,
        "unit": "PPM",
        "device_name": "Gas Analyzer",
    }


def read_analyzer_metadata(port, sensor_id):
    ser = open_ports.get(port)
    lock = port_locks.get(port)
    config = get_default_config(sensor_id)

    if ser is None or lock is None:
        return config

    with lock:
        decimal_regs = read_modbus_registers(
            ser,
            sensor_id,
            DECIMAL_REGISTER,
            1,
            timeout=DISCOVERY_TIMEOUT,
            retries=1,
        )
        if decimal_regs is not None:
            decimal_value = decimal_regs[0]
            if 0 <= decimal_value <= 6:
                config["decimals"] = decimal_value

        gas_regs = read_modbus_registers(
            ser,
            sensor_id,
            GAS_NAME_REGISTER,
            GAS_NAME_REGISTER_COUNT,
            timeout=DISCOVERY_TIMEOUT,
            retries=1,
        )
        if gas_regs is not None:
            gas_name = registers_to_ascii(gas_regs)
            if gas_name:
                config["gas_type"] = gas_name[:40]

        unit_regs = read_modbus_registers(
            ser,
            sensor_id,
            UNIT_REGISTER,
            UNIT_REGISTER_COUNT,
            timeout=DISCOVERY_TIMEOUT,
            retries=1,
        )
        if unit_regs is not None:
            unit_name = registers_to_ascii(unit_regs)
            if unit_name:
                config["unit"] = unit_name[:16]

    return config


# ============================================================
# TEST ONE MODBUS ID ON ONE PORT
# ============================================================

def test_analyzer_on_port(port, sensor_id):
    ser = open_ports.get(port)
    lock = port_locks.get(port)

    if ser is None or lock is None:
        return None

    with lock:
        registers = read_modbus_registers(
            ser,
            sensor_id,
            PROCESS_VALUE_REGISTER,
            1,
            timeout=DISCOVERY_TIMEOUT,
            retries=DISCOVERY_RETRIES,
        )

    if registers is None:
        return None

    raw_value = registers[0]
    print(f"    FOUND -> ID={sensor_id} | Raw Reg32={raw_value}")
    return raw_value


# ============================================================
# DISCOVER ALL ANALYZERS ON ALL PORTS
# ============================================================

def discover_analyzers():
    global connected_analyzers

    if SIMULATE_MODE:
        print()
        print("=" * 78)
        print("SIMULATION MODE ACTIVE - REGISTERING KNOWN INDUSTRIAL ANALYZERS")
        print("=" * 78)
        discovered = {}
        for s_id, conf in KNOWN_SENSORS.items():
            discovered[("VIRTUAL_PORT_RS485", s_id)] = {
                "port": "VIRTUAL_PORT_RS485",
                "sensor_id": s_id,
                "gas_type": conf["gas_type"],
                "decimals": conf["decimals"],
                "unit": conf["unit"],
                "device_name": conf["device_name"],
            }
            print(f"  Discovered Simulator: ID={s_id} -> {conf['gas_type']} ({conf['unit']})")
        connected_analyzers = discovered
        return discovered

    open_detected_ports()
    discovered = {}

    print()
    print("=" * 78)
    print("MULTI-ANALYZER DISCOVERY")
    print("=" * 78)

    if not open_ports:
        print("No usable serial ports.")
        connected_analyzers = {}
        return {}

    print(
        f"Scanning Modbus IDs {SCAN_ID_START} to {SCAN_ID_END} "
        f"on {len(open_ports)} physical port(s)"
    )

    for port in sorted(open_ports.keys()):
        print()
        print(f"SCANNING PORT: {port}")
        port_count = 0

        for sensor_id in range(SCAN_ID_START, SCAN_ID_END + 1):
            print(f"  Testing ID {sensor_id:3d}...", end=" ", flush=True)
            raw_value = test_analyzer_on_port(port, sensor_id)

            if raw_value is None:
                print("NO RESPONSE")
                continue

            config = read_analyzer_metadata(port, sensor_id)
            key = (port, sensor_id)

            discovered[key] = {
                "port": port,
                "sensor_id": sensor_id,
                "gas_type": config["gas_type"],
                "decimals": config["decimals"],
                "unit": config["unit"],
                "device_name": config["device_name"],
            }
            port_count += 1

        print(f"  >>> {port}: {port_count} analyzer(s) detected")

    connected_analyzers = discovered

    print()
    print("=" * 78)
    print("DISCOVERY RESULT")
    print("=" * 78)

    if not discovered:
        print("NO ANALYZERS DETECTED")
    else:
        print(f"TOTAL ANALYZERS DETECTED: {len(discovered)}")
        for port in sorted(open_ports.keys()):
            analyzers = [
                info for info in discovered.values()
                if info["port"] == port
            ]
            if not analyzers:
                continue
            print()
            print(f"PORT {port} -> {len(analyzers)} ANALYZER(S)")
            for info in sorted(analyzers, key=lambda item: item["sensor_id"]):
                print(
                    f"    ID {info['sensor_id']} -> "
                    f"{info['gas_type']} -> "
                    f"{info['unit']} -> "
                    f"decimals={info['decimals']}"
                )

    print("=" * 78)
    return discovered


# ============================================================
# RECOVER DISCOVERED ANALYZERS AFTER A PORT CHANGE
# ============================================================

def recover_disconnected_analyzers(removed_ports, added_ports):
    global connected_analyzers

    if not removed_ports:
        return

    affected = []
    for key, info in list(connected_analyzers.items()):
        if info["port"] in removed_ports:
            affected.append((key, info))

    if not affected:
        return

    print()
    print("=" * 78)
    print("RECOVERING DISCOVERED ANALYZERS AFTER PORT DISCONNECT")
    print("=" * 78)
    print(f"Known analyzers affected by port change: {len(affected)}")

    if not added_ports:
        print("No replacement serial port is available yet.")
        print("=" * 78)
        return

    for old_key, info in affected:
        sensor_id = info["sensor_id"]
        old_port = info["port"]
        recovered = False

        print(f"  Recovering ID {sensor_id} (previous port {old_port})...")
        for new_port in added_ports:
            if new_port not in open_ports:
                continue

            raw_value = test_analyzer_on_port(new_port, sensor_id)
            if raw_value is None:
                continue

            new_info = dict(info)
            new_info["port"] = new_port
            connected_analyzers.pop(old_key, None)
            connected_analyzers[(new_port, sensor_id)] = new_info

            print(
                f"    RECOVERED -> ID={sensor_id} | "
                f"{old_port} -> {new_port} | "
                f"Raw Reg32={raw_value}"
            )
            recovered = True
            break

        if not recovered:
            print(f"    WAITING -> ID={sensor_id} | replacement port/analyzer not found")

    print("=" * 78)


def monitor_and_recover_ports():
    if SIMULATE_MODE:
        return [], []

    added_ports, removed_ports = refresh_serial_ports(verbose=True)
    lost_ports = set(removed_ports)

    for info in connected_analyzers.values():
        old_port = info["port"]
        if old_port not in open_ports:
            lost_ports.add(old_port)

    if lost_ports:
        recover_disconnected_analyzers(sorted(lost_ports), added_ports)

    return removed_ports, added_ports


# ============================================================
# READ ONE DISCOVERED ANALYZER
# ============================================================

def read_sensor(analyzer_info):
    port = analyzer_info["port"]
    sensor_id = analyzer_info["sensor_id"]

    if SIMULATE_MODE:
        import random
        # Produce realistic, valid gas telemetry
        if sensor_id == 1:
            raw_value = int(random.uniform(1, 15))
        elif sensor_id == 2:
            raw_value = int(random.uniform(40, 75)) # 4.0 - 7.5 ppm
        else:
            raw_value = int(random.uniform(0, 20))

        decimals = analyzer_info["decimals"]
        value = raw_value / (10 ** decimals)
        return {
            "sensor_id": sensor_id,
            "port": port,
            "device_name": analyzer_info["device_name"],
            "gas_type": analyzer_info["gas_type"],
            "value": round(float(value), 2),
            "unit": analyzer_info["unit"],
            "status": "ONLINE",
            "timestamp": get_timestamp(),
            "raw_value": raw_value,
            "decimals": decimals,
        }

    ser = open_ports.get(port)
    lock = port_locks.get(port)

    if ser is None or lock is None:
        return make_offline_record(analyzer_info, "SERIAL PORT NOT OPEN")

    with lock:
        registers = read_modbus_registers(
            ser,
            sensor_id,
            PROCESS_VALUE_REGISTER,
            1,
            timeout=READ_TIMEOUT,
            retries=READ_RETRIES,
        )

    if registers is None:
        return make_offline_record(analyzer_info, "NO MODBUS RESPONSE")

    raw_value = registers[0]
    decimals = analyzer_info["decimals"]
    value = raw_value / (10 ** decimals)

    return {
        "sensor_id": sensor_id,
        "port": port,
        "device_name": analyzer_info["device_name"],
        "gas_type": analyzer_info["gas_type"],
        "value": float(value),
        "unit": analyzer_info["unit"],
        "status": "ONLINE",
        "timestamp": get_timestamp(),
        "raw_value": raw_value,
        "decimals": decimals,
    }


def make_offline_record(analyzer_info, reason):
    return {
        "sensor_id": analyzer_info["sensor_id"],
        "port": analyzer_info["port"],
        "device_name": analyzer_info["device_name"],
        "gas_type": analyzer_info["gas_type"],
        "value": 0.0,
        "unit": analyzer_info["unit"],
        "status": "OFFLINE",
        "timestamp": get_timestamp(),
        "raw_value": 0,
        "decimals": analyzer_info["decimals"],
        "offline_reason": reason,
    }


# ============================================================
# CSV
# ============================================================

def save_csv(data):
    try:
        with open(CSV_FILE, "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow([
                data["timestamp"],
                data["sensor_id"],
                data["port"],
                data["device_name"],
                data["gas_type"],
                data["value"],
                data["unit"],
                data["status"],
            ])
        return True
    except Exception as exc:
        print(f"CSV ERROR: {exc}")
        return False


# ============================================================
# LOCAL JSONL QUEUE
# ============================================================

def save_pending_data(data):
    try:
        payload = {
            "timestamp": data["timestamp"],
            "sensor_id": data["sensor_id"],
            "device_name": data["device_name"],
            "gas_type": data["gas_type"],
            "value": data["value"],
            "unit": data["unit"],
            "status": data["status"],
        }
        with open(PENDING_FILE, "a", encoding="utf-8") as file:
            file.write(json.dumps(payload, separators=(",", ":")) + "\n")
        return True
    except Exception as exc:
        print(f"LOCAL QUEUE ERROR: {exc}")
        return False


# ============================================================
# BACKEND
# ============================================================

def send_to_backend(data):
    global backend_online, BACKEND_URL

    payload = {
        "sensor_id": data["sensor_id"],
        "device_name": data["device_name"],
        "gas_type": data["gas_type"],
        "value": data["value"],
        "unit": data["unit"],
        "status": data["status"],
        "timestamp": data["timestamp"],
    }

    # Try current BACKEND_URL first, then fallback to candidate endpoints
    urls_to_try = [BACKEND_URL] + [u for u in CANDIDATE_BACKEND_URLS if u != BACKEND_URL]

    for target_url in urls_to_try:
        try:
            response = requests.post(target_url, json=payload, timeout=3)
            if 200 <= response.status_code < 300:
                backend_online = True
                BACKEND_URL = target_url
                return True
        except Exception:
            continue

    backend_online = False
    return False


def sync_pending_data():
    if not os.path.exists(PENDING_FILE):
        return

    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except Exception as exc:
        print(f"QUEUE READ ERROR: {exc}")
        return

    if not lines:
        return

    remaining_lines = list(lines)
    synced_count = 0

    for line in lines[:SYNC_BATCH_SIZE]:
        line = line.strip()
        if not line:
            if remaining_lines:
                remaining_lines.pop(0)
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            break

        if send_to_backend(data):
            if remaining_lines:
                remaining_lines.pop(0)
            synced_count += 1
        else:
            break

    try:
        if remaining_lines:
            temp_file = PENDING_FILE + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as file:
                file.writelines(remaining_lines)
            os.replace(temp_file, PENDING_FILE)
        else:
            try:
                os.remove(PENDING_FILE)
            except FileNotFoundError:
                pass
    except Exception as exc:
        print(f"QUEUE WRITE ERROR: {exc}")

    if synced_count:
        print(f"Offline data synced to backend: {synced_count} record(s)")


# ============================================================
# PROCESS READING
# ============================================================

def process_reading(data):
    print()
    print("-" * 78)
    print(f"Time       : {data['timestamp']}")
    print(f"Port       : {data['port']}")
    print(f"Sensor ID  : {data['sensor_id']}")
    print(f"Gas        : {data['gas_type']}")
    print(f"Raw Reg 32 : {data['raw_value']}")
    print(f"Decimals   : {data['decimals']}")
    print(f"Value      : {data['value']} {data['unit']}")
    print(f"Status     : {data['status']}")

    if save_csv(data):
        print("SD Card    : CSV DATA SAVED")
    else:
        print("SD Card    : CSV SAVE FAILED")

    if data["status"] != "ONLINE":
        print(f"Reason     : {data.get('offline_reason', 'NO MODBUS RESPONSE')}")
        print("-" * 78)
        return

    if send_to_backend(data):
        print(f"Backend    : DATA SENT SUCCESSFULLY -> {BACKEND_URL}")
    else:
        print("Backend    : OFFLINE - DATA STORED LOCALLY")
        if save_pending_data(data):
            print("Local Queue: JSON DATA SAVED")

    print("JSON:")
    print(json.dumps({
        "sensor_id": data["sensor_id"],
        "device_name": data["device_name"],
        "gas_type": data["gas_type"],
        "value": data["value"],
        "unit": data["unit"],
        "status": data["status"],
        "timestamp": data["timestamp"],
    }, indent=2))
    print("-" * 78)


# ============================================================
# SUMMARY
# ============================================================

def print_port_summary():
    print()
    print("=" * 78)
    print("PORT / ANALYZER SUMMARY")
    print("=" * 78)

    if not connected_analyzers:
        print("Total analyzers connected: 0")
        print("No Modbus analyzers detected.")
        print("=" * 78)
        return

    total = len(connected_analyzers)
    print(f"Total analyzers connected: {total}")
    print()

    ports = sorted(set(info["port"] for info in connected_analyzers.values()))
    for port in ports:
        analyzers = [
            info for info in connected_analyzers.values()
            if info["port"] == port
        ]
        if not analyzers:
            continue
        print(f"{port} -> {len(analyzers)} analyzer(s)")
        for info in sorted(analyzers, key=lambda item: item["sensor_id"]):
            print(f"    Modbus ID {info['sensor_id']} -> {info['gas_type']}")

    print("=" * 78)


# ============================================================
# MAIN
# ============================================================

def main():
    global connected_analyzers, SIMULATE_MODE, BACKEND_URL

    parser = argparse.ArgumentParser(description="Multi Gas Analyzer IoT Gateway")
    parser.add_argument("--simulate", "-s", action="store_true", help="Run simulated analyzers for offline testing")
    parser.add_argument("--backend", "-b", type=str, help="Override backend URL")
    args = parser.parse_args()

    if args.simulate:
        SIMULATE_MODE = True
    if args.backend:
        BACKEND_URL = args.backend

    create_directories()
    initialize_csv()

    print()
    print("=" * 78)
    print(APP_NAME)
    print("=" * 78)
    print("Multiple analyzers on SAME RS-485 port: SUPPORTED")
    print("Analyzer discovery: STARTUP ONLY (ONE TIME)")
    print(f"After startup scan: READ DATA ONLY every {READ_INTERVAL} seconds")
    print("Zero value from valid Modbus response: ONLINE")
    print(f"Discovery ID range: {SCAN_ID_START}-{SCAN_ID_END}")
    print(f"Read interval: {READ_INTERVAL} seconds")
    print(f"Active Backend: {BACKEND_URL}")
    print(f"Local Storage Directory: {BASE_DIR}")
    print("=" * 78)

    connected_analyzers = discover_analyzers()
    print_port_summary()

    if not connected_analyzers:
        print()
        print(
            "WARNING: No analyzers were detected. "
            "Check RS-485 wiring, baud rate, slave IDs and power."
        )

    while True:
        try:
            print()
            print("=" * 78)
            print("READING ALL DISCOVERED GAS ANALYZERS - NO MODBUS SCAN")
            print("=" * 78)

            monitor_and_recover_ports()

            offline_count = 0
            for key in sorted(connected_analyzers.keys(), key=lambda item: (item[0], item[1])):
                analyzer_info = connected_analyzers[key]
                data = read_sensor(analyzer_info)
                process_reading(data)
                if data["status"] != "ONLINE":
                    offline_count += 1

            sync_pending_data()
            print_port_summary()

            if offline_count:
                print(f"WARNING: {offline_count} discovered analyzer(s) did not respond in this cycle.")

            print()
            print(f"Next DATA reading in {READ_INTERVAL} seconds...")
            time.sleep(READ_INTERVAL)

        except KeyboardInterrupt:
            print()
            print("Gateway stopped by user.")
            close_all_ports()
            break
        except Exception as exc:
            print()
            print(f"MAIN LOOP ERROR: {exc}")
            print("Retrying in 5 seconds...")
            time.sleep(5)


if __name__ == "__main__":
    main()

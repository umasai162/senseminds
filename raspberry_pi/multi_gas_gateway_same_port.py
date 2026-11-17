#!/usr/bin/env python3
"""
multi_gas_gateway_same_port.py

Industrial-style Modbus RTU gateway for MULTIPLE gas analyzers on the
SAME RS-485 / USB-RS485 port with 800x480 HDMI Dashboard and LAN Sync.
"""

import os
import sys
import csv
import json
import time
import socket
import threading
import argparse
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

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

READ_INTERVAL = int(os.getenv("READ_INTERVAL", "15"))

# Ethernet / LAN Backend Candidates:
# Primary: configured host (default 192.168.1.8 for direct Ethernet)
# Fallbacks: active Wi-Fi LAN IPs (192.168.55.105, 192.168.1.6, 192.168.1.37)
BACKEND_HOST = os.getenv("BACKEND_HOST", "192.168.1.8")
configured_url = os.getenv("BACKEND_URL", f"http://{BACKEND_HOST}:8000/readings/")

CANDIDATE_BACKEND_URLS = [
    configured_url,
    f"http://{BACKEND_HOST}:8000/readings/",
    "http://192.168.55.105:8000/readings/",
    "http://192.168.1.6:8000/readings/",
    "http://192.168.1.37:8000/readings/",
    "http://127.0.0.1:8000/readings/",
    "http://localhost:8000/readings/",
]
# Remove duplicates preserving order
CANDIDATE_BACKEND_URLS = [u for i, u in enumerate(CANDIDATE_BACKEND_URLS) if u and u not in CANDIDATE_BACKEND_URLS[:i]]
BACKEND_URL = CANDIDATE_BACKEND_URLS[0]

# Storage directory
if os.name == "nt":
    DEFAULT_BASE_DIR = str(Path(__file__).resolve().parent / "gas_gateway_data")
else:
    DEFAULT_BASE_DIR = "/home/pi/gas_gateway_data"

BASE_DIR = os.getenv("GATEWAY_DATA_DIR", DEFAULT_BASE_DIR)

try:
    Path(BASE_DIR).mkdir(parents=True, exist_ok=True)
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

PROCESS_VALUE_REGISTER = 31
DECIMAL_REGISTER = 14
GAS_NAME_REGISTER = 16
GAS_NAME_REGISTER_COUNT = 8
UNIT_REGISTER = 24
UNIT_REGISTER_COUNT = 4

SCAN_ID_START = 1
SCAN_ID_END = 32


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
HEADLESS_MODE = False


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
# MODBUS CRC16 & FRAME
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
    frame += bytes([crc & 0xFF, (crc >> 8) & 0xFF])
    return frame


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
    request = build_modbus_request(slave_id, register_address, count)

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
                response_slave, response_function, byte_count = header[0], header[1], header[2]
                if response_slave != slave_id:
                    continue
                if response_function & 0x80 or response_function != MODBUS_FUNCTION_READ_HOLDING:
                    ser.read(2)
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


def registers_to_ascii(registers):
    raw = bytearray()
    for value in registers:
        raw.append((value >> 8) & 0xFF)
        raw.append(value & 0xFF)
    return raw.decode("ascii", errors="ignore").replace("\x00", "").strip()


# ============================================================
# PORT DETECTION
# ============================================================

def detect_serial_ports():
    if not SERIAL_AVAILABLE:
        return []
    candidates = []
    preferred_ports = ["/dev/serial0", "/dev/serial1", "/dev/ttyAMA0", "/dev/ttyAMA1", "/dev/ttyS0", "/dev/ttyS1"]
    for port in preferred_ports:
        if os.path.exists(port):
            candidates.append(port)

    try:
        for info in serial.tools.list_ports.comports():
            desc = f"{info.description} {info.manufacturer} {info.product}".lower()
            if getattr(info, 'vid', None) == 0x2C7C or "quectel" in desc or "ec25" in desc:
                continue
            candidates.append(info.device)
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

    for old_port in removed_ports:
        ser = open_ports.pop(old_port, None)
        port_locks.pop(old_port, None)
        if ser:
            try:
                ser.close()
            except Exception:
                pass

    for port in added_ports:
        ser = open_serial_port(port)
        if ser:
            open_ports[port] = ser
            port_locks[port] = threading.Lock()

    return added_ports, removed_ports


def open_detected_ports():
    detected = detect_serial_ports()
    for port in detected:
        if port not in open_ports:
            ser = open_serial_port(port)
            if ser:
                open_ports[port] = ser
                port_locks[port] = threading.Lock()
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
# METADATA & DISCOVERY
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
    if not ser or not lock:
        return config

    with lock:
        dec = read_modbus_registers(ser, sensor_id, DECIMAL_REGISTER, 1, timeout=DISCOVERY_TIMEOUT, retries=1)
        if dec and 0 <= dec[0] <= 6:
            config["decimals"] = dec[0]
        gas = read_modbus_registers(ser, sensor_id, GAS_NAME_REGISTER, GAS_NAME_REGISTER_COUNT, timeout=DISCOVERY_TIMEOUT, retries=1)
        if gas:
            name = registers_to_ascii(gas)
            if name:
                config["gas_type"] = name[:40]
        u = read_modbus_registers(ser, sensor_id, UNIT_REGISTER, UNIT_REGISTER_COUNT, timeout=DISCOVERY_TIMEOUT, retries=1)
        if u:
            uname = registers_to_ascii(u)
            if uname:
                config["unit"] = uname[:16]
    return config


def test_analyzer_on_port(port, sensor_id):
    ser = open_ports.get(port)
    lock = port_locks.get(port)
    if not ser or not lock:
        return None
    with lock:
        regs = read_modbus_registers(ser, sensor_id, PROCESS_VALUE_REGISTER, 1, timeout=DISCOVERY_TIMEOUT, retries=DISCOVERY_RETRIES)
    return regs[0] if regs else None


def discover_analyzers():
    global connected_analyzers

    if SIMULATE_MODE:
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
        connected_analyzers = discovered
        return discovered

    open_detected_ports()
    discovered = {}
    for port in sorted(open_ports.keys()):
        for sensor_id in range(SCAN_ID_START, SCAN_ID_END + 1):
            raw_value = test_analyzer_on_port(port, sensor_id)
            if raw_value is not None:
                config = read_analyzer_metadata(port, sensor_id)
                discovered[(port, sensor_id)] = {
                    "port": port,
                    "sensor_id": sensor_id,
                    "gas_type": config["gas_type"],
                    "decimals": config["decimals"],
                    "unit": config["unit"],
                    "device_name": config["device_name"],
                }
    connected_analyzers = discovered
    return discovered


def recover_disconnected_analyzers(removed_ports, added_ports):
    global connected_analyzers
    if not removed_ports or not added_ports:
        return

    for old_key, info in list(connected_analyzers.items()):
        if info["port"] in removed_ports:
            for new_port in added_ports:
                if test_analyzer_on_port(new_port, info["sensor_id"]) is not None:
                    new_info = dict(info)
                    new_info["port"] = new_port
                    connected_analyzers.pop(old_key, None)
                    connected_analyzers[(new_port, info["sensor_id"])] = new_info
                    break


def monitor_and_recover_ports():
    if SIMULATE_MODE:
        return [], []
    added_ports, removed_ports = refresh_serial_ports(verbose=False)
    lost_ports = set(removed_ports)
    for info in connected_analyzers.values():
        if info["port"] not in open_ports:
            lost_ports.add(info["port"])
    if lost_ports:
        recover_disconnected_analyzers(sorted(lost_ports), added_ports)
    return removed_ports, added_ports


# ============================================================
# READ SENSOR
# ============================================================

def read_sensor(analyzer_info):
    port = analyzer_info["port"]
    sensor_id = analyzer_info["sensor_id"]

    if SIMULATE_MODE:
        import random
        raw_val = random.randint(1, 15) if sensor_id == 1 else random.randint(40, 75)
        dec = analyzer_info["decimals"]
        val = raw_val / (10 ** dec)
        return {
            "sensor_id": sensor_id,
            "port": port,
            "device_name": analyzer_info["device_name"],
            "gas_type": analyzer_info["gas_type"],
            "value": round(float(val), 2),
            "unit": analyzer_info["unit"],
            "status": "ONLINE",
            "timestamp": get_timestamp(),
            "raw_value": raw_val,
            "decimals": dec,
        }

    ser = open_ports.get(port)
    lock = port_locks.get(port)
    if not ser or not lock:
        return make_offline_record(analyzer_info, "SERIAL PORT NOT OPEN")

    with lock:
        registers = read_modbus_registers(ser, sensor_id, PROCESS_VALUE_REGISTER, 1, timeout=READ_TIMEOUT, retries=READ_RETRIES)

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
# PERSISTENCE & LAN BACKEND
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

    # Try current URL first, then candidate fallbacks across LAN
    urls_to_try = [BACKEND_URL] + [u for u in CANDIDATE_BACKEND_URLS if u != BACKEND_URL]

    for target_url in urls_to_try:
        try:
            response = requests.post(target_url, json=payload, timeout=2.5)
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
    except Exception:
        return

    if not lines:
        return

    remaining_lines = list(lines)
    synced_count = 0
    for line in lines[:SYNC_BATCH_SIZE]:
        line = line.strip()
        if not line:
            if remaining_lines: remaining_lines.pop(0)
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            break

        if send_to_backend(data):
            if remaining_lines: remaining_lines.pop(0)
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
    except Exception:
        pass


def process_reading(data):
    set_dashboard_reading(data)
    print()
    print("-" * 78)
    print(f"Time       : {data['timestamp']}")
    print(f"Port       : {data['port']}")
    print(f"Sensor ID  : {data['sensor_id']}")
    print(f"Gas        : {data['gas_type']}")
    print(f"Value      : {data['value']} {data['unit']}")
    print(f"Status     : {data['status']}")

    save_csv(data)

    if data["status"] != "ONLINE":
        print(f"Reason     : {data.get('offline_reason', 'NO MODBUS RESPONSE')}")
        print("-" * 78)
        return

    if send_to_backend(data):
        print(f"Backend    : DATA SENT SUCCESSFULLY -> {BACKEND_URL}")
    else:
        print("Backend    : OFFLINE - DATA STORED LOCALLY")
        save_pending_data(data)
    print("-" * 78)


def print_port_summary():
    if not connected_analyzers:
        print("Total analyzers connected: 0")
        return
    print(f"Total analyzers connected: {len(connected_analyzers)}")
    for info in connected_analyzers.values():
        print(f"    Modbus ID {info['sensor_id']} -> {info['gas_type']}")


# ============================================================
# HDMI DASHBOARD & NETWORK CHECKS
# ============================================================

dashboard_readings = {}
dashboard_lock = threading.Lock()
dashboard_last_check = "--"
dashboard_wifi = False
dashboard_lan = False
dashboard_backend = False


def set_dashboard_reading(data):
    global dashboard_last_check
    key = (data.get("port", ""), int(data.get("sensor_id", 0)))
    with dashboard_lock:
        dashboard_readings[key] = dict(data)
        dashboard_last_check = data.get("timestamp", "--")


def get_dashboard_snapshot():
    with dashboard_lock:
        readings = [dict(v) for v in dashboard_readings.values()]
        last_check = dashboard_last_check
        wifi = dashboard_wifi
        lan = dashboard_lan
        backend = dashboard_backend
    readings.sort(key=lambda x: (str(x.get("port", "")), int(x.get("sensor_id", 0))))
    return readings, last_check, wifi, lan, backend


def check_wifi_status():
    try:
        res = subprocess.run(["iwgetid", "-r"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2)
        return bool(res.stdout.decode().strip())
    except Exception:
        return False


def check_lan_status():
    if not os.path.exists("/sys/class/net"):
        # On Windows dev machine
        return True
    try:
        interfaces = [n for n in os.listdir("/sys/class/net") if n not in ("lo",) and not n.startswith(("wlan", "wl"))]
        for name in interfaces:
            try:
                op = Path(f"/sys/class/net/{name}/operstate").read_text().strip()
                if op == "up":
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def check_backend_tcp():
    # Try current BACKEND_URL host:port
    try:
        parsed = urlparse(BACKEND_URL)
        host = parsed.hostname
        port = parsed.port or 8000
        if host:
            with socket.create_connection((host, port), timeout=1.2):
                return True
    except Exception:
        pass
    return False


def dashboard_status_worker():
    global dashboard_wifi, dashboard_lan, dashboard_backend
    while True:
        try:
            dashboard_wifi = check_wifi_status()
            dashboard_lan = check_lan_status()
            dashboard_backend = check_backend_tcp()
        except Exception:
            pass
        time.sleep(3)


class GatewayDashboard:
    def __init__(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.title("INDUSTRIAL IoT GATEWAY")
        self.root.geometry("800x480")
        self.root.configure(bg="#101418")
        try:
            self.root.attributes("-fullscreen", True)
        except Exception:
            pass
        self.root.bind("<Escape>", lambda event: self.root.destroy())

        self.title_font = ("DejaVu Sans", 24, "bold")
        self.network_font = ("DejaVu Sans", 10, "bold")
        self.analyzer_cards = {}

        self.build_ui()
        self.update_ui()

    def build_ui(self):
        import tkinter as tk
        header = tk.Frame(self.root, bg="#172027", height=58)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="INDUSTRIAL IoT GATEWAY", font=self.title_font, bg="#172027", fg="white").pack(side="left", expand=True)

        self.analyzer_area = tk.Frame(self.root, bg="#101418", height=340)
        self.analyzer_area.pack(fill="both", expand=True, padx=10, pady=10)

        self.network_frame = tk.Frame(self.root, bg="#172027", height=50)
        self.network_frame.pack(fill="x", side="bottom")

        self.wifi_label = tk.Label(self.network_frame, text="Wi-Fi OFFLINE", font=self.network_font, bg="#172027", fg="#ff5c5c")
        self.wifi_label.pack(side="left", padx=12)

        self.lan_label = tk.Label(self.network_frame, text="LAN OFFLINE", font=self.network_font, bg="#172027", fg="#ff5c5c")
        self.lan_label.pack(side="left", padx=12)

        self.backend_label = tk.Label(self.network_frame, text="BACKEND OFFLINE", font=self.network_font, bg="#172027", fg="#ff5c5c")
        self.backend_label.pack(side="left", padx=12)

        self.last_label = tk.Label(self.network_frame, text="Last Check: --", font=("DejaVu Sans", 9), bg="#172027", fg="#71818c")
        self.last_label.pack(side="right", padx=12)

    def update_ui(self):
        try:
            readings, last_check, wifi, lan, backend = get_dashboard_snapshot()
            self.wifi_label.config(text=f"Wi-Fi {'ONLINE' if wifi else 'OFFLINE'}", fg="#39d98a" if wifi else "#ff5c5c")
            self.lan_label.config(text=f"LAN {'ONLINE' if lan else 'OFFLINE'}", fg="#39d98a" if lan else "#ff5c5c")
            self.backend_label.config(text=f"BACKEND {'ONLINE' if backend else 'OFFLINE'}", fg="#39d98a" if backend else "#ff5c5c")
            self.last_label.config(text=f"Last Check: {last_check}")
            self.root.after(1000, self.update_ui)
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


# ============================================================
# GATEWAY WORKER & MAIN
# ============================================================

def gateway_worker():
    global connected_analyzers
    create_directories()
    initialize_csv()
    connected_analyzers = discover_analyzers()

    for info in connected_analyzers.values():
        initial = {
            "sensor_id": info["sensor_id"],
            "port": info["port"],
            "device_name": info["device_name"],
            "gas_type": info["gas_type"],
            "value": 0.0,
            "unit": info["unit"],
            "status": "OFFLINE",
            "timestamp": get_timestamp(),
            "raw_value": 0,
            "decimals": info["decimals"],
            "offline_reason": "WAITING FOR FIRST READING"
        }
        set_dashboard_reading(initial)

    while True:
        try:
            monitor_and_recover_ports()
            for key in sorted(connected_analyzers.keys(), key=lambda item: (item[0], item[1])):
                info = connected_analyzers[key]
                data = read_sensor(info)
                process_reading(data)
            sync_pending_data()
            time.sleep(READ_INTERVAL)
        except Exception as exc:
            print(f"MAIN LOOP ERROR: {exc}")
            time.sleep(5)


def main():
    global SIMULATE_MODE, BACKEND_URL, HEADLESS_MODE

    parser = argparse.ArgumentParser(description="Multi Gas Analyzer IoT Gateway")
    parser.add_argument("--simulate", "-s", action="store_true", help="Run simulated analyzers for testing")
    parser.add_argument("--backend", "-b", type=str, help="Override backend URL")
    parser.add_argument("--headless", action="store_true", help="Run without HDMI Tkinter GUI")
    args = parser.parse_args()

    if args.simulate:
        SIMULATE_MODE = True
    if args.backend:
        BACKEND_URL = args.backend
    if args.headless or not os.environ.get("DISPLAY"):
        HEADLESS_MODE = True

    print()
    print("=" * 78)
    print(APP_NAME)
    print(f"Read Interval: {READ_INTERVAL} seconds")
    print(f"Target Backend: {BACKEND_URL}")
    print("=" * 78)

    network_thread = threading.Thread(target=dashboard_status_worker, daemon=True)
    network_thread.start()

    if HEADLESS_MODE:
        gateway_worker()
    else:
        gateway_thread = threading.Thread(target=gateway_worker, daemon=True)
        gateway_thread.start()
        dashboard = GatewayDashboard()
        dashboard.run()


if __name__ == "__main__":
    main()

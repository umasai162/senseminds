import os
import sys
import json
import csv
import unittest
from pathlib import Path
from datetime import datetime, timezone
import requests

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

gateway_dir = backend_dir.parent / "raspberry_pi"
sys.path.insert(0, str(gateway_dir))

from app.database import SessionLocal, Base, engine
from app.models import GasReading
import sensor_reader

BASE_URL = "http://127.0.0.1:8000"

class TestIoTGatewayIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    def test_1_health_endpoint(self):
        """Verify GET /health returns 200 OK and database connected."""
        res = requests.get(f"{BASE_URL}/health", timeout=5)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "MULTI GAS ANALYZER IoT GATEWAY")
        self.assertEqual(data["database"], "connected")
        print("✓ GET /health verified")

    def test_2_post_reading(self):
        """Verify POST /readings/ stores a single gas reading."""
        sample_time = datetime.now(timezone.utc).isoformat()
        payload = {
            "sensor_id": 2,
            "device_name": "Uniphos 500DT",
            "gas_type": "H2S",
            "value": 5.42,
            "unit": "ppm",
            "status": "ONLINE",
            "timestamp": sample_time
        }
        res = requests.post(f"{BASE_URL}/readings/", json=payload, timeout=5)
        self.assertIn(res.status_code, (200, 201))
        data = res.json()
        self.assertEqual(data["sensor_id"], 2)
        self.assertEqual(data["value"], 5.42)
        self.assertEqual(data["status"], "ONLINE")
        print("✓ POST /readings/ verified")

    def test_3_get_readings(self):
        """Verify GET /readings/ returns telemetry records."""
        res = requests.get(f"{BASE_URL}/readings/?limit=10", timeout=5)
        self.assertEqual(res.status_code, 200)
        readings = res.json()
        self.assertIsInstance(readings, list)
        self.assertGreaterEqual(len(readings), 1)
        print(f"✓ GET /readings/ verified ({len(readings)} records found)")

    def test_4_get_latest_reading(self):
        """Verify GET /readings/latest returns the most recent gas reading."""
        res = requests.get(f"{BASE_URL}/readings/latest?sensor_id=2", timeout=5)
        self.assertEqual(res.status_code, 200)
        latest = res.json()
        self.assertEqual(latest["sensor_id"], 2)
        self.assertIsNotNone(latest["value"])
        print(f"✓ GET /readings/latest verified (Sensor {latest['sensor_id']}: {latest['value']} {latest['unit']})")

    def test_5_get_readings_stats(self):
        """Verify GET /readings/stats returns aggregates and health."""
        res = requests.get(f"{BASE_URL}/readings/stats", timeout=5)
        self.assertEqual(res.status_code, 200)
        stats = res.json()
        self.assertGreaterEqual(stats["total_readings"], 1)
        self.assertGreaterEqual(stats["total_sensors"], 1)
        print("✓ GET /readings/stats verified")

    def test_6_csv_generation(self):
        """Verify Raspberry Pi gateway generates and appends to readings.csv."""
        test_reading = {
            "sensor_id": 2,
            "device_name": "Uniphos 500DT",
            "gas_type": "H2S",
            "value": 6.15,
            "unit": "ppm",
            "status": "ONLINE",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        sensor_reader.append_to_csv(test_reading)
        self.assertTrue(os.path.exists(sensor_reader.CSV_FILE_PATH))
        
        # Verify CSV contents
        with open(sensor_reader.CSV_FILE_PATH, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertGreater(len(rows), 0)
            last_row = rows[-1]
            self.assertEqual(last_row["sensor_id"], "2")
            self.assertEqual(last_row["gas_type"], "H2S")
        print(f"✓ CSV Generation verified at {sensor_reader.CSV_FILE_PATH}")

    def test_7_offline_buffering_and_reconnection_sync(self):
        """
        Verify network disconnection handling:
        1. When backend is unreachable, reading is buffered to pending_readings.jsonl.
        2. When connection is restored, pending buffer is flushed in batches to backend.
        3. Buffer is emptied and database receives the records.
        """
        pending_file = sensor_reader.PENDING_FILE_PATH
        if os.path.exists(pending_file):
            try:
                os.remove(pending_file)
            except Exception:
                pass

        offline_reading = {
            "sensor_id": 2,
            "device_name": "Uniphos 500DT",
            "gas_type": "H2S",
            "value": 7.89,
            "unit": "ppm",
            "status": "ONLINE",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # Step A: Append to pending queue
        sensor_reader.append_to_pending(offline_reading)
        self.assertEqual(sensor_reader.get_pending_count(), 1)

        # Step B: Deduplication check - duplicate reading should NOT be re-added
        sensor_reader.append_to_pending(offline_reading)
        self.assertEqual(sensor_reader.get_pending_count(), 1)

        # Step C: Reconnect & Flush
        sensor_reader.flush_pending_readings(f"{BASE_URL}/readings/", batch_size=100)
        self.assertEqual(sensor_reader.get_pending_count(), 0)

        # Step D: Confirm reading arrived in database
        latest = requests.get(f"{BASE_URL}/readings/latest?sensor_id=2").json()
        self.assertEqual(latest["value"], 7.89)
        print("✓ Offline buffering & sync reconnection verified")

if __name__ == "__main__":
    unittest.main()

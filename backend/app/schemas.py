from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List, Dict, Any

class GasReadingCreate(BaseModel):
    sensor_id: int
    device_name: str
    gas_type: str
    value: Optional[float] = None
    unit: str
    status: str
    timestamp: Optional[datetime] = None

class GasReadingResponse(GasReadingCreate):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True

class GasReadingBatchCreate(BaseModel):
    readings: List[GasReadingCreate]

class SensorStatItem(BaseModel):
    sensor_id: int
    device_name: str
    gas_type: str
    unit: str
    count: int
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    avg_value: Optional[float] = None
    latest_value: Optional[float] = None
    latest_status: str
    latest_timestamp: Optional[datetime] = None

class GasReadingStats(BaseModel):
    total_readings: int
    total_sensors: int
    online_sensors_count: int
    offline_sensors_count: int
    sensors: List[SensorStatItem]
    first_reading_timestamp: Optional[datetime] = None
    last_reading_timestamp: Optional[datetime] = None

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    database: str
    timestamp: datetime


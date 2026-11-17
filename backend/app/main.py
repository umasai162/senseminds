from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from datetime import datetime, timezone
from typing import List, Union, Dict, Any, Optional
from pydantic import BaseModel
from . import models, schemas
from .database import engine, get_db
from .ml_engine import ml_engine
from .rag_copilot import rag_copilot
from .ml_evaluation import get_full_ml_metrics_report

# Create database tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="MULTI GAS ANALYZER IoT GATEWAY API",
    description="High-concurrency CEMS telemetry ingestion, industrial ML forecasting, and RAG copilot services.",
    version="5.0.0"
)

# Open CORS for React dashboard across LAN
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic schemas for Copilot & ML requests
class CopilotQueryRequest(BaseModel):
    query: str

class ScrubberDiagnoseRequest(BaseModel):
    ph: float
    h2s_ppm: float
    co_ppm: float
    ph_trend: Optional[float] = 0.0
    pump_active: Optional[int] = 1

class DosingCalculationRequest(BaseModel):
    current_ph: float
    h2s_ppm: float
    gas_flow_m3h: Optional[float] = 1200.0

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    print(f"\n--- 422 VALIDATION ERROR ---")
    print(f"Request body: {body}")
    print(f"Validation errors: {exc.errors()}")
    print(f"----------------------------\n")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": body.decode("utf-8") if body else ""},
    )

@app.get("/", tags=["System"])
def read_root():
    return {
        "application": "MULTI GAS ANALYZER IoT GATEWAY",
        "platform": "SenseMinds Industrial Environmental Intelligence",
        "system": "Automated CEMS and Scrubber Telemetry Platform",
        "facility": "Laurus Labs Pharmaceutical Unit 1 Operations",
        "health": "/health",
        "documentation": "/docs"
    }

@app.get("/health", response_model=schemas.HealthResponse, tags=["System"])
def health_check(db: Session = Depends(get_db)):
    """Backend availability and database health check."""
    db_status = "connected"
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unhealthy: {e}"
    
    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "service": "MULTI GAS ANALYZER IoT GATEWAY",
        "version": "5.0.0",
        "database": db_status,
        "timestamp": datetime.now(timezone.utc)
    }

# -------------------------------------------------------------
# 1. Telemetry Ingestion & Retrieval Endpoints
# -------------------------------------------------------------
@app.post("/readings/", status_code=status.HTTP_201_CREATED, tags=["Readings"])
def create_reading(payload: Union[schemas.GasReadingCreate, Dict[str, List[schemas.GasReadingCreate]]], db: Session = Depends(get_db)):
    """
    Receives single gas sensor reading or batch from IoT Gateway / Raspberry Pi.
    """
    if isinstance(payload, dict) and "readings" in payload:
        created_readings = []
        for r in payload["readings"]:
            # Avoid duplicate if same timestamp, sensor_id, and value exist
            t = r.timestamp or datetime.now(timezone.utc)
            db_reading = models.GasReading(
                sensor_id=r.sensor_id,
                device_name=r.device_name,
                gas_type=r.gas_type,
                value=r.value,
                unit=r.unit,
                status=r.status,
                timestamp=t
            )
            db.add(db_reading)
            created_readings.append(db_reading)
        db.commit()
        for r in created_readings:
            db.refresh(r)
        return {"status": "success", "count": len(created_readings)}
    else:
        reading = payload
        t = reading.timestamp or datetime.now(timezone.utc)
        db_reading = models.GasReading(
            sensor_id=reading.sensor_id,
            device_name=reading.device_name,
            gas_type=reading.gas_type,
            value=reading.value,
            unit=reading.unit,
            status=reading.status,
            timestamp=t
        )
        db.add(db_reading)
        db.commit()
        db.refresh(db_reading)
        return db_reading

@app.get("/readings/latest", tags=["Readings"])
def get_latest_reading(sensor_id: Optional[int] = None, all_sensors: bool = False, db: Session = Depends(get_db)):
    """
    Returns the latest gas reading.
    - If sensor_id is supplied: latest reading for that sensor.
    - If all_sensors is True: latest reading for every registered sensor.
    - Default: overall most recent reading.
    """
    if all_sensors:
        # Retrieve all distinct sensor IDs
        sensor_ids = [r[0] for r in db.query(distinct(models.GasReading.sensor_id)).all()]
        results = []
        for s_id in sensor_ids:
            latest = (
                db.query(models.GasReading)
                .filter(models.GasReading.sensor_id == s_id)
                .order_by(models.GasReading.timestamp.desc(), models.GasReading.id.desc())
                .first()
            )
            if latest:
                results.append(latest)
        return results

    query = db.query(models.GasReading)
    if sensor_id is not None:
        query = query.filter(models.GasReading.sensor_id == sensor_id)
    
    latest = query.order_by(models.GasReading.timestamp.desc(), models.GasReading.id.desc()).first()
    if not latest:
        raise HTTPException(status_code=404, detail="No readings found")
    return latest

@app.get("/readings/stats", response_model=schemas.GasReadingStats, tags=["Readings"])
def get_readings_stats(db: Session = Depends(get_db)):
    """
    Returns comprehensive gas sensor statistics for dashboard display.
    """
    total_count = db.query(models.GasReading).count()
    if total_count == 0:
        return {
            "total_readings": 0,
            "total_sensors": 0,
            "online_sensors_count": 0,
            "offline_sensors_count": 0,
            "sensors": [],
            "first_reading_timestamp": None,
            "last_reading_timestamp": None
        }

    first_reading = db.query(models.GasReading).order_by(models.GasReading.timestamp.asc()).first()
    last_reading = db.query(models.GasReading).order_by(models.GasReading.timestamp.desc()).first()

    sensor_ids = [r[0] for r in db.query(distinct(models.GasReading.sensor_id)).all()]
    sensor_stats = []
    online_count = 0
    offline_count = 0

    for s_id in sensor_ids:
        readings_q = db.query(models.GasReading).filter(models.GasReading.sensor_id == s_id)
        count = readings_q.count()
        latest = readings_q.order_by(models.GasReading.timestamp.desc(), models.GasReading.id.desc()).first()

        # Compute min, max, avg for non-null values
        val_q = db.query(
            func.min(models.GasReading.value),
            func.max(models.GasReading.value),
            func.avg(models.GasReading.value)
        ).filter(models.GasReading.sensor_id == s_id, models.GasReading.value.isnot(None)).first()

        min_val = round(val_q[0], 2) if val_q and val_q[0] is not None else None
        max_val = round(val_q[1], 2) if val_q and val_q[1] is not None else None
        avg_val = round(val_q[2], 2) if val_q and val_q[2] is not None else None

        is_online = latest.status.upper() == "ONLINE" if latest and latest.status else False
        if is_online:
            online_count += 1
        else:
            offline_count += 1

        sensor_stats.append({
            "sensor_id": s_id,
            "device_name": latest.device_name if latest else f"Sensor {s_id}",
            "gas_type": latest.gas_type if latest else "Unknown",
            "unit": latest.unit if latest else "ppm",
            "count": count,
            "min_value": min_val,
            "max_value": max_val,
            "avg_value": avg_val,
            "latest_value": latest.value if latest else None,
            "latest_status": latest.status if latest else "OFFLINE",
            "latest_timestamp": latest.timestamp if latest else None
        })

    return {
        "total_readings": total_count,
        "total_sensors": len(sensor_ids),
        "online_sensors_count": online_count,
        "offline_sensors_count": offline_count,
        "sensors": sensor_stats,
        "first_reading_timestamp": first_reading.timestamp if first_reading else None,
        "last_reading_timestamp": last_reading.timestamp if last_reading else None
    }

@app.get("/readings/", response_model=List[schemas.GasReadingResponse], tags=["Readings"])
def get_readings(
    sensor_id: Optional[int] = None,
    gas_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    query = db.query(models.GasReading)
    if sensor_id is not None:
        query = query.filter(models.GasReading.sensor_id == sensor_id)
    if gas_type is not None:
        query = query.filter(models.GasReading.gas_type == gas_type)
    readings = query.order_by(models.GasReading.timestamp.desc(), models.GasReading.id.desc()).offset(skip).limit(limit).all()
    return readings


# -------------------------------------------------------------
# 2. Machine Learning Endpoints
# -------------------------------------------------------------
@app.get("/analytics/forecast/{sensor_id}")
def get_sensor_forecast(sensor_id: int, db: Session = Depends(get_db)):
    """Generates multi-step time-series forecasting (+30m, +1h, +4h) for a sensor."""
    return ml_engine.forecast_sensor_telemetry(db, sensor_id=sensor_id)

@app.get("/analytics/anomalies")
def get_detected_anomalies(limit: int = 100, db: Session = Depends(get_db)):
    """Runs Isolation Forest unsupervised anomaly detection over recent telemetry."""
    return ml_engine.detect_anomalies(db, limit=limit)

@app.post("/analytics/diagnose")
def diagnose_scrubber_condition(payload: ScrubberDiagnoseRequest):
    """Diagnoses root causes for scrubber issues using Random Forest classification."""
    return ml_engine.diagnose_scrubber(
        ph=payload.ph,
        h2s_ppm=payload.h2s_ppm,
        co_ppm=payload.co_ppm,
        ph_trend=payload.ph_trend,
        pump_active=payload.pump_active
    )

@app.post("/analytics/dosing")
def calculate_naoh_dosing(payload: DosingCalculationRequest):
    """Calculates stoichiometric NaOH neutralizing dosing rate in L/hr."""
    return ml_engine.calculate_dosing_recommendation(
        current_ph=payload.current_ph,
        h2s_ppm=payload.h2s_ppm,
        gas_flow_m3h=payload.gas_flow_m3h
    )

@app.get("/analytics/metrics")
def get_model_evaluation_metrics():
    """
    Returns comprehensive evaluation metrics:
    - Classification: Accuracy, Precision, Recall, F1-Score, Confusion Matrix, 5-Fold Cross Validation.
    - Time-Series Forecasting: MAE, RMSE, MSE, R2 Score, MAPE.
    """
    return get_full_ml_metrics_report()

# -------------------------------------------------------------
# 3. RAG Copilot Endpoint
# -------------------------------------------------------------
@app.post("/copilot/query")
def copilot_query(payload: CopilotQueryRequest, db: Session = Depends(get_db)):
    """
    Retrieval-Augmented Generation (RAG) assistant:
    Retrieves live database telemetry, ML state, and CEMS regulatory corpus
    to synthesize actionable, grounded operator responses.
    """
    if not payload.query or not payload.query.strip():
        raise HTTPException(status_code=400, detail="Query string cannot be empty.")
    
    return rag_copilot.generate_response(payload.query, db)

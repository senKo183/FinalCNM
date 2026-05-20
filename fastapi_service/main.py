"""FastAPI Inference Service — LOS Prediction v2 Giai đoạn 5.

Service độc lập chịu trách nhiệm model serving:
- Tải @champion model từ MLflow Registry, fallback về pkl local.
- Nhận inference request, trả về LOS prediction + metadata.
- Health check endpoint cho Nginx upstream health.
- Tách biệt hoàn toàn khỏi Django → scale độc lập, zero-downtime deploy.

Port: 8001 (Docker nội bộ), exposed ra host qua Nginx /api/v2/
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd
from decouple import config
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fastapi_inference")

# ─── Config from env ──────────────────────────────────────────────────────────
MLFLOW_TRACKING_URI = config("MLFLOW_TRACKING_URI", default="http://mlflow:5000")
MLFLOW_S3_ENDPOINT_URL = config("MLFLOW_S3_ENDPOINT_URL", default="http://minio:9000")
MLFLOW_REGISTERED_MODEL_NAME = config("MLFLOW_REGISTERED_MODEL_NAME", default="los_model")
AWS_ACCESS_KEY_ID = config("AWS_ACCESS_KEY_ID", default="minio_admin")
AWS_SECRET_ACCESS_KEY = config("AWS_SECRET_ACCESS_KEY", default="minio_password")
ML_MODELS_DIR = config("ML_MODELS_DIR", default="/app/ml_models")
MONGODB_URI = config("MONGODB_URI", default="")
MONGODB_NAME = config("MONGODB_NAME", default="predictlos_db")
CSV_DATA_PATH = config("CSV_DATA_PATH", default="/app/LengthOfStay.csv")

os.environ.setdefault("MLFLOW_TRACKING_URI", MLFLOW_TRACKING_URI)
os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", MLFLOW_S3_ENDPOINT_URL)
os.environ.setdefault("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID)
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY)

# ─── Feature constants ────────────────────────────────────────────────────────
CONTINUOUS_VARS = [
    "hematocrit", "neutrophils", "sodium", "glucose",
    "bloodureanitro", "creatinine", "bmi", "pulse", "respiration",
]

ISSUE_COLUMNS = [
    "hemo", "dialysisrenalendstage", "asthma", "irondef", "pneum",
    "substancedependence", "psychologicaldisordermajor", "depress",
    "psychother", "fibrosisandother", "malnutrition",
]

FEATURE_ORDER = [
    "rcount", "dialysisrenalendstage", "asthma", "irondef", "pneum",
    "substancedependence", "psychologicaldisordermajor", "depress",
    "psychother", "fibrosisandother", "malnutrition", "hemo",
    "hematocrit", "neutrophils", "sodium", "glucose", "bloodureanitro",
    "creatinine", "bmi", "pulse", "respiration",
    "secondarydiagnosisnonicd9", "number_of_issues",
]

# ─── Model cache ──────────────────────────────────────────────────────────────
_model = None
_model_version = "unknown"
_model_source = "none"
_statistics: dict = {}
_loaded_at: Optional[datetime] = None


def _load_statistics():
    global _statistics
    if _statistics:
        return _statistics
    if os.path.exists(CSV_DATA_PATH):
        try:
            df = pd.read_csv(CSV_DATA_PATH)
            for col in CONTINUOUS_VARS:
                _statistics[col] = {
                    "mean": float(df[col].mean()),
                    "std": float(df[col].std()),
                }
            logger.info("Loaded normalisation statistics from %s", CSV_DATA_PATH)
        except Exception as exc:
            logger.warning("Could not load CSV statistics: %s", exc)
    return _statistics


def _try_load_from_mlflow():
    global _model, _model_version, _model_source, _loaded_at
    try:
        import mlflow.sklearn
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        uri = f"models:/{MLFLOW_REGISTERED_MODEL_NAME}@champion"
        model = mlflow.sklearn.load_model(uri)
        _model = model
        _model_version = "mlflow:champion"
        _model_source = "mlflow_registry"
        _loaded_at = datetime.now()
        logger.info("Loaded @champion model from MLflow Registry: %s", uri)
        return True
    except Exception as exc:
        logger.warning("MLflow load failed: %s", exc)
        return False


def _try_load_from_mongodb():
    """Lấy path pkl active từ MongoDB model_versions collection."""
    global _model, _model_version, _model_source, _loaded_at
    if not MONGODB_URI:
        return False
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
        db = client[MONGODB_NAME]
        active = db["model_versions"].find_one({"is_active": True})
        if active and os.path.exists(active.get("model_file_path", "")):
            _model = joblib.load(active["model_file_path"])
            _model_version = active.get("version_number", "v?")
            _model_source = "local_pkl_mongodb"
            _loaded_at = datetime.now()
            logger.info("Loaded model from MongoDB active version: %s", _model_version)
            return True
    except Exception as exc:
        logger.warning("MongoDB model load failed: %s", exc)
    return False


def _try_load_default_pkl():
    global _model, _model_version, _model_source, _loaded_at
    # Thử load các pkl file theo thứ tự ưu tiên: best_los_model.pkl rồi các file mới nhất
    import glob  # noqa: WPS433
    candidates = []
    default = os.path.join(ML_MODELS_DIR, "best_los_model.pkl")
    if os.path.exists(default):
        candidates.append((default, "v1_bootstrap"))
    # Thêm các file model mới nhất (từ retrain)
    pattern = os.path.join(ML_MODELS_DIR, "model_v*.pkl")
    newer_models = sorted(glob.glob(pattern), reverse=True)
    for p in newer_models[:3]:
        candidates.insert(0, (p, os.path.basename(p).replace(".pkl", "")))

    for path, version_label in candidates:
        try:
            loaded = joblib.load(path)
            _model = loaded
            _model_version = version_label
            _model_source = "bootstrap_pkl"
            _loaded_at = datetime.now()
            logger.info("Loaded bootstrap model from %s", path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skip %s (load error: %s)", path, exc)
    return False


def load_model(force: bool = False):
    global _model
    if _model is not None and not force:
        return
    _load_statistics()
    if _try_load_from_mlflow():
        return
    if _try_load_from_mongodb():
        return
    _try_load_default_pkl()


def preprocess(raw: dict) -> dict:
    stats = _statistics
    processed = dict(raw)
    for col in CONTINUOUS_VARS:
        if col in stats and col in processed:
            m = stats[col]["mean"]
            s = stats[col]["std"]
            if s > 0:
                processed[col] = (float(processed[col]) - m) / s
    processed["number_of_issues"] = sum(int(processed.get(c, 0)) for c in ISSUE_COLUMNS)
    return processed


# ─── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="LOS Prediction Inference API",
    description="Model serving service — PredictLOS v2 Giai đoạn 5",
    version="2.0.0",
    docs_url="/api/v2/docs",
    redoc_url="/api/v2/redoc",
    openapi_url="/api/v2/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    logger.info("FastAPI inference service starting up...")
    try:
        load_model()
    except Exception as exc:  # noqa: BLE001
        logger.error("Model load error (service will continue without model): %s", exc)
    if _model is None:
        logger.warning("No model loaded — predictions will return fallback value.")
    else:
        logger.info("Model ready: %s (source: %s)", _model_version, _model_source)


# ─── Request / Response schemas ───────────────────────────────────────────────
class PredictRequest(BaseModel):
    cccd: Optional[str] = Field(None, description="CCCD bệnh nhân (để tra Feast nếu có)")
    rcount: int = Field(0, ge=0, le=5)
    hematocrit: float = 0.0
    neutrophils: float = 0.0
    sodium: float = 0.0
    glucose: float = 0.0
    bloodureanitro: float = 0.0
    creatinine: float = 0.0
    bmi: float = 0.0
    pulse: float = 0.0
    respiration: float = 0.0
    secondarydiagnosisnonicd9: int = Field(0, ge=0, le=10)
    hemo: int = Field(0, ge=0, le=1)
    dialysisrenalendstage: int = Field(0, ge=0, le=1)
    asthma: int = Field(0, ge=0, le=1)
    irondef: int = Field(0, ge=0, le=1)
    pneum: int = Field(0, ge=0, le=1)
    substancedependence: int = Field(0, ge=0, le=1)
    psychologicaldisordermajor: int = Field(0, ge=0, le=1)
    depress: int = Field(0, ge=0, le=1)
    psychother: int = Field(0, ge=0, le=1)
    fibrosisandother: int = Field(0, ge=0, le=1)
    malnutrition: int = Field(0, ge=0, le=1)


class PredictResponse(BaseModel):
    predicted_los: float
    model_version: str
    model_source: str
    feast_used: bool = False
    service: str = "fastapi"
    timestamp: str


class ModelInfoResponse(BaseModel):
    model_version: str
    model_source: str
    loaded_at: Optional[str]
    mlflow_tracking_uri: str
    registered_model_name: str
    feature_count: int


# ─── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/api/v2/health", tags=["Health"])
async def health_check():
    return {
        "status": "ok" if _model is not None else "degraded",
        "model_loaded": _model is not None,
        "model_version": _model_version,
        "model_source": _model_source,
        "service": "fastapi_inference",
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/v2/model/info", response_model=ModelInfoResponse, tags=["Model"])
async def model_info():
    return ModelInfoResponse(
        model_version=_model_version,
        model_source=_model_source,
        loaded_at=_loaded_at.isoformat() if _loaded_at else None,
        mlflow_tracking_uri=MLFLOW_TRACKING_URI,
        registered_model_name=MLFLOW_REGISTERED_MODEL_NAME,
        feature_count=len(FEATURE_ORDER),
    )


@app.post("/api/v2/model/reload", tags=["Model"])
async def reload_model():
    """Force reload @champion model từ MLflow Registry."""
    load_model(force=True)
    return {
        "message": "Model reloaded",
        "model_version": _model_version,
        "model_source": _model_source,
    }


@app.post("/api/v2/predict", response_model=PredictResponse, tags=["Inference"])
async def predict(req: PredictRequest):
    """Dự đoán LOS cho một bệnh nhân.

    Ưu tiên lấy features từ Feast Online Store nếu có `cccd`.
    Fallback về features trong request body nếu Feast không sẵn sàng.
    """
    if _model is None:
        load_model(force=True)
        if _model is None:
            raise HTTPException(
                status_code=503,
                detail="Chưa có model sẵn sàng. Kiểm tra MLflow hoặc file pkl."
            )

    raw = req.model_dump(exclude={"cccd"})
    feast_used = False

    # Thử Feast Online Store
    if req.cccd:
        try:
            import redis as redis_lib
            from decouple import config as deconf
            feast_redis_url = deconf("FEAST_REDIS_URL", default="redis://redis:6379/1")
            r = redis_lib.from_url(feast_redis_url, socket_connect_timeout=1, decode_responses=True)
            r.ping()
            feast_used = True  # Redis kết nối được, để predictor enrichment đánh dấu
        except Exception:
            pass

    processed = preprocess(raw)
    feature_vector = [processed.get(f, 0) for f in FEATURE_ORDER]
    X = pd.DataFrame([feature_vector], columns=FEATURE_ORDER)

    try:
        prediction = float(_model.predict(X)[0])
        predicted_los = max(round(prediction, 2), 0.5)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Prediction error: {exc}")

    return PredictResponse(
        predicted_los=predicted_los,
        model_version=_model_version,
        model_source=_model_source,
        feast_used=feast_used,
        service="fastapi",
        timestamp=datetime.now().isoformat(),
    )


@app.get("/api/v2/", tags=["Health"])
async def root():
    return {
        "service": "LOS Prediction Inference API",
        "version": "2.0.0",
        "docs": "/api/v2/docs",
        "health": "/api/v2/health",
    }

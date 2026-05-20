"""Feast Feature Definitions — LOS Prediction v2 Giai đoạn 4.

Entity: patient_cccd (CCCD là khóa định danh chính)

FeatureViews:
  - patient_demographics: rcount, bmi, pulse, respiration
  - patient_lab_results:  hematocrit, neutrophils, sodium, glucose,
                          bloodureanitro, creatinine
  - patient_comorbidities: 11 bệnh lý + secondarydiagnosisnonicd9

Offline Store: File (parquet) — data/dvc_snapshots/
Online Store: Redis DB 1 — phục vụ real-time inference

Push Sources cho phép Django push features ngay khi bệnh nhân nhập viện
(thay vì chờ feast materialize theo lịch).
"""
from datetime import timedelta

from feast import Entity, FeatureView, Field, PushSource, FileSource
from feast.types import Float32, Int64, String

# ─── Entity ──────────────────────────────────────────────────────────────────
patient = Entity(
    name="patient_cccd",
    description="Số CCCD bệnh nhân — khóa định danh xuyên suốt mọi đợt điều trị",
)

# ─── Batch (Offline) Sources — trỏ vào thư mục riêng từng FeatureView ───────
# Dùng thư mục (directory) thay vì glob pattern để tương thích Windows
demographics_source = FileSource(
    path="../data/feast_offline/demographics",
    timestamp_field="event_timestamp",
)

lab_results_source = FileSource(
    path="../data/feast_offline/lab_results",
    timestamp_field="event_timestamp",
)

comorbidities_source = FileSource(
    path="../data/feast_offline/comorbidities",
    timestamp_field="event_timestamp",
)

# ─── Push Sources — nhận real-time updates từ Django ─────────────────────────
push_demographics = PushSource(
    name="push_demographics",
    batch_source=demographics_source,
)

push_lab_results = PushSource(
    name="push_lab_results",
    batch_source=lab_results_source,
)

push_comorbidities = PushSource(
    name="push_comorbidities",
    batch_source=comorbidities_source,
)

# ─── Feature Views ────────────────────────────────────────────────────────────
patient_demographics_fv = FeatureView(
    name="patient_demographics",
    entities=[patient],
    ttl=timedelta(days=365),
    schema=[
        Field(name="rcount", dtype=Int64, description="Số lần nhập viện trước (capped 5)"),
        Field(name="bmi", dtype=Float32, description="Chỉ số khối cơ thể"),
        Field(name="pulse", dtype=Float32, description="Mạch (nhịp/phút)"),
        Field(name="respiration", dtype=Float32, description="Tần số thở"),
        Field(name="secondarydiagnosisnonicd9", dtype=Int64, description="Chẩn đoán phụ (0-10)"),
    ],
    source=push_demographics,
    online=True,
)

patient_lab_results_fv = FeatureView(
    name="patient_lab_results",
    entities=[patient],
    ttl=timedelta(days=365),
    schema=[
        Field(name="hematocrit", dtype=Float32),
        Field(name="neutrophils", dtype=Float32),
        Field(name="sodium", dtype=Float32),
        Field(name="glucose", dtype=Float32),
        Field(name="bloodureanitro", dtype=Float32),
        Field(name="creatinine", dtype=Float32),
    ],
    source=push_lab_results,
    online=True,
)

patient_comorbidities_fv = FeatureView(
    name="patient_comorbidities",
    entities=[patient],
    ttl=timedelta(days=365),
    schema=[
        Field(name="hemo", dtype=Int64),
        Field(name="dialysisrenalendstage", dtype=Int64),
        Field(name="asthma", dtype=Int64),
        Field(name="irondef", dtype=Int64),
        Field(name="pneum", dtype=Int64),
        Field(name="substancedependence", dtype=Int64),
        Field(name="psychologicaldisordermajor", dtype=Int64),
        Field(name="depress", dtype=Int64),
        Field(name="psychother", dtype=Int64),
        Field(name="fibrosisandother", dtype=Int64),
        Field(name="malnutrition", dtype=Int64),
    ],
    source=push_comorbidities,
    online=True,
)

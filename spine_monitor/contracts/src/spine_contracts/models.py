"""Contratos v0 (borrador). Sin métricas ni umbrales concretos: los define Aguado."""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Level(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class SensorReading(BaseModel):
    """Un valor escalar de un canal de un sensor (la IMU publica 6 por muestra)."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "timestamp": "2026-10-25T10:00:00Z",
                    "sensor_id": "imu_l3",
                    "channel": "accel_x",
                    "value": 0.02,
                    "unit": "g",
                    "quality": 1.0,
                }
            ]
        }
    )

    timestamp: AwareDatetime
    sensor_id: str = Field(min_length=1, description='p. ej. "imu_l3", "emg_left", "stretch_lumbar"')
    channel: str = Field(min_length=1, description='p. ej. "accel_x", "gyro_z", "envelope", "resistance"')
    value: float
    unit: str = Field(description='p. ej. "g", "deg/s", "mV", "ohm"')
    quality: float = Field(default=1.0, ge=0, le=1)

    @property
    def metric(self) -> str:
        """Clave de la serie temporal a la que pertenece la lectura."""
        return f"{self.sensor_id}.{self.channel}"


class MetricBaseline(BaseModel):
    mean: float
    std: float = Field(ge=0)
    n: int = Field(ge=0)


class Baseline(BaseModel):
    """Línea base personal, por métrica (claves con el formato de `SensorReading.metric`)."""

    metrics: dict[str, MetricBaseline] = Field(default_factory=dict)


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "astronaut_id": "A1",
                    "mission_day": 87,
                    "baseline": {"metrics": {"stretch_lumbar.resistance": {"mean": 2100.0, "std": 35.0, "n": 500}}},
                    "readings": [
                        {
                            "timestamp": "2026-10-25T10:00:00Z",
                            "sensor_id": "stretch_lumbar",
                            "channel": "resistance",
                            "value": 2150.0,
                            "unit": "ohm",
                            "quality": 0.9,
                        }
                    ],
                }
            ]
        }
    )

    astronaut_id: str = Field(min_length=1)
    mission_day: int | None = None
    baseline: Baseline
    readings: list[SensorReading] = Field(min_length=1)


class Driver(BaseModel):
    feature: str
    value: float
    contribution: float


class Trend(BaseModel):
    projected_level: Level
    days_to_threshold: float | None = None


class Assessment(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "astronaut_id": "A1",
                    "mission_day": 87,
                    "as_of": "2026-10-25T10:00:00Z",
                    "level": "amber",
                    "risk_score": 0.62,
                    "drivers": [{"feature": "stretch_lumbar.resistance", "value": 2.4, "contribution": 0.31}],
                    "trend": {"projected_level": "red", "days_to_threshold": 12},
                    "recommendations": ["REC_LUMBAR_STAB_01"],
                    "escalate_to_ground": False,
                }
            ]
        }
    )

    astronaut_id: str
    mission_day: int | None = None
    as_of: AwareDatetime
    level: Level
    risk_score: float = Field(ge=0, le=1)
    drivers: list[Driver] = Field(default_factory=list)
    trend: Trend | None = None
    recommendations: list[str] = Field(default_factory=list, description="Códigos REC_*")
    escalate_to_ground: bool = False


class DeviceStatusKind(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class DeviceStatus(BaseModel):
    device_id: str = Field(min_length=1)
    timestamp: AwareDatetime
    status: DeviceStatusKind
    battery_pct: float | None = Field(default=None, ge=0, le=100)
    firmware_version: str | None = None

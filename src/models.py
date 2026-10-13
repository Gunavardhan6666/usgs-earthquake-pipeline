from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


class QuakeRecord(BaseModel):
    event_id: str = Field(min_length=1)
    event_type: Literal["earthquake"]
    magnitude: float = Field(ge=-2.0, le=10.0)
    time_ms: int = Field(gt=0)
    updated_ms: int = Field(gt=0)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    depth_km: float = Field(ge=-10.0, le=800.0)
    tsunami: int = Field(ge=0, le=1)
    sig: int = Field(ge=0, le=5000)
    felt: Optional[int] = Field(default=None, ge=0)
    nst: Optional[int] = Field(default=None, ge=0)
    cdi: Optional[float] = Field(default=None, ge=0.0, le=12.0)
    mmi: Optional[float] = Field(default=None, ge=0.0, le=12.0)
    alert: Optional[Literal["green", "yellow", "orange", "red"]] = None
    gap: Optional[float] = Field(default=None, ge=0.0, le=360.0)
    rms: Optional[float] = Field(default=None, ge=0.0)
    mag_type: Optional[str] = None
    place: Optional[str] = None
    status: Optional[str] = None
    net: Optional[str] = None
    url: Optional[str] = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> "QuakeRecord":
        if self.updated_ms < self.time_ms:
            raise ValueError("updated_ms must be greater than or equal to time_ms")
        return self

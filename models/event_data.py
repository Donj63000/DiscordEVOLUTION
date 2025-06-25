from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
import json

from pydantic import BaseModel, field_validator, model_validator


class EventData(BaseModel):
    guild_id: int
    channel_id: int
    title: str
    description: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    max_participants: Optional[int] = None
    timezone: Optional[str] = None
    recurrence: Optional[str] = None
    temp_role_id: Optional[int] = None
    banner_url: Optional[str] = None
    author_id: Optional[int] = None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def ensure_tzinfo(cls, v: Optional[datetime]):
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v

    @model_validator(mode="after")
    def check_dates(cls, data: "EventData"):
        if data.ends_at is None:
            data.ends_at = data.starts_at + timedelta(hours=1)
        elif data.ends_at <= data.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return data

    def model_dump_json(self, **kwargs) -> str:  # type: ignore[override]
        data = self.model_dump(mode="json", exclude_none=True)
        return json.dumps(data, **kwargs)

    @classmethod
    def from_dict(cls, data: dict) -> "EventData":
        return cls.model_validate(data)

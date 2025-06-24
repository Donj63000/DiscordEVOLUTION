from __future__ import annotations

from datetime import datetime
from typing import Optional
import json

from pydantic import BaseModel


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

    def model_dump_json(self, **kwargs) -> str:  # type: ignore[override]
        data = self.model_dump(mode="json", exclude_none=True)
        return json.dumps(data, **kwargs)

    @classmethod
    def from_dict(cls, data: dict) -> "EventData":
        return cls.model_validate(data)

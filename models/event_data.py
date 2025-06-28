from __future__ import annotations

from datetime import datetime
from typing import Optional
import json

try:  # pragma: no cover - optional dependency
    from pydantic import BaseModel
    _HAS_PYDANTIC = True
except Exception:  # noqa: PIE786 - fallback without dependency
    from dataclasses import dataclass, asdict
    BaseModel = object  # type: ignore
    _HAS_PYDANTIC = False


if _HAS_PYDANTIC:
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
            if (mp := data.get("max_participants")) is not None:
                try:
                    data["max_participants"] = min(int(mp), 8)
                except (TypeError, ValueError):
                    data["max_participants"] = None
            if hasattr(cls, "model_validate"):
                return cls.model_validate(data)
            return cls.parse_obj(data)

        @classmethod
        def model_validate_json(cls, json_str: str) -> "EventData":
            return cls.from_dict(json.loads(json_str))
else:
    @dataclass
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

        def __post_init__(self) -> None:  # pragma: no cover - simple coercion
            if self.max_participants is not None:
                try:
                    self.max_participants = min(int(self.max_participants), 8)
                except (TypeError, ValueError):
                    self.max_participants = None

        def model_dump(self, mode: str = "python", *, exclude_none: bool = False):
            data = asdict(self)
            if exclude_none:
                data = {k: v for k, v in data.items() if v is not None}
            return data

        def model_dump_json(self, **kwargs) -> str:  # type: ignore[override]
            return json.dumps(self.model_dump(exclude_none=True), **kwargs)

        @classmethod
        def from_dict(cls, data: dict) -> "EventData":
            return cls(**data)

        @classmethod
        def model_validate(cls, data: dict) -> "EventData":
            return cls.from_dict(data)

        @classmethod
        def model_validate_json(cls, json_str: str) -> "EventData":
            return cls.from_dict(json.loads(json_str))

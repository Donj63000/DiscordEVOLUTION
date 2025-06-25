import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from models import EventData


@pytest.mark.asyncio
async def test_eventdata_valid_json():
    json_text = json.dumps({
        "guild_id": 123,
        "channel_id": 456,
        "title": "Test Event",
        "description": "A test returned by Gemini",
        "starts_at": "2025-01-01T12:00:00",
        "ends_at": "2025-01-01T13:00:00",
        "max_participants": 10
    })
    data = EventData.model_validate_json(json_text)
    assert data.guild_id == 123
    assert data.channel_id == 456
    assert data.title == "Test Event"
    assert data.max_participants == 8


@pytest.mark.asyncio
async def test_eventdata_invalid_json():
    bad_json = '{"guild_id": 123,'  # malformed JSON
    with pytest.raises(Exception):
        EventData.model_validate_json(bad_json)

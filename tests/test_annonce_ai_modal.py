from unittest.mock import MagicMock

import pytest

from cogs.annonce_ai import AnnounceAICog


@pytest.mark.asyncio
async def test_annonce_modal_labels_within_limit():
    parent = MagicMock()
    modal = AnnounceAICog._AnnounceModal(parent, None)
    labels = [item.label for item in modal.children]
    assert labels
    assert all(1 <= len(label) <= 45 for label in labels)


@pytest.mark.asyncio
async def test_schedule_modal_labels_within_limit():
    parent = MagicMock()
    modal = AnnounceAICog._ScheduleModal(parent, 123)
    labels = [item.label for item in modal.children]
    assert labels
    assert all(1 <= len(label) <= 45 for label in labels)

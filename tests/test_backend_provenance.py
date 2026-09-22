import json

import pytest

from chatgpt_query_broker.query_api import (
    _stream_handle,
)
from chatgpt_query_broker.query_backend import (
    QueryHandle,
)


async def empty_events():
    if False:
        yield {}


@pytest.mark.asyncio
async def test_thread_event_reports_selected_backend():
    handle = QueryHandle(
        conversation_id="qb1:web:conversation",
        execution_id="qb1:web:execution",
        events=empty_events(),
        backend="web",
    )

    stream = _stream_handle(handle)

    try:
        line = await anext(stream)
        event = json.loads(line)

        assert event == {
            "type": "thread",
            "thread_id": (
                "qb1:web:conversation"
            ),
            "backend": "web",
        }

    finally:
        await stream.aclose()

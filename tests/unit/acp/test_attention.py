"""Request facts survive idle turns and expire with their original connection."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from moss_harness.acp.attention import AttentionSnapshot, OrderedAttentionProjection
from moss_harness.acp.models import ACPConfig
from moss_harness.acp.service import ACPService, ObservationQuery
from pydantic import ValidationError
from tests.unit.acp.test_runtime_status import bind


def attention(revision=1):
    return {
        "revision": revision,
        "requests": [
            {
                "id": 7,
                "reason": "permission",
                "phase": "presented",
                "summary": "Bash",
                "agent": {
                    "agent_id": "child-7",
                    "name": "worker",
                    "description": "test child",
                    "subagent_type": None,
                    "model": None,
                    "color": None,
                    "background": True,
                },
            }
        ],
    }


def test_request_order_and_removal_are_engine_owned():
    projection = OrderedAttentionProjection()
    first = projection.observe(attention())
    assert first.requests[0].agent.background is True
    assert projection.observe(attention()) == first
    empty = projection.observe({"revision": 2, "requests": []})
    assert empty.requests == ()
    with pytest.raises(ValueError, match="backwards"):
        projection.observe(attention())
    with pytest.raises(ValueError, match="without a revision"):
        projection.observe(attention(2))
    assert projection.snapshot == empty
    projection.invalidate()
    assert projection.snapshot is None


async def test_slow_ui_delivery_cannot_restore_an_answered_request():
    projection = OrderedAttentionProjection()
    entered, release = asyncio.Event(), asyncio.Event()
    delivered = []

    async def sink(snapshot):
        if snapshot.revision == 1:
            entered.set()
            await release.wait()
        delivered.append(snapshot)

    projection.observe(attention())
    first = asyncio.create_task(projection.deliver_current(1, sink))
    await entered.wait()
    projection.observe({"revision": 2, "requests": []})
    second = asyncio.create_task(projection.deliver_current(2, sink))
    release.set()
    await asyncio.gather(first, second)
    assert [snapshot.revision for snapshot in delivered] == [1, 2]
    assert delivered[-1].requests == ()
    await projection.deliver_current(1, sink)
    assert len(delivered) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"revision": True},
        {"revision": -1},
        {
            "requests": [
                {"id": 1, "phase": "presented", "reason": "inferred", "summary": "", "agent": None}
            ]
        },
        {"requests": attention()["requests"] * 2},
    ],
)
def test_malformed_attention_cannot_become_ready(change):
    with pytest.raises(ValidationError):
        AttentionSnapshot.model_validate({**attention(), **change})


async def test_attention_query_is_independent_of_turn_and_not_a_permission_grant():
    service = ACPService(config=ACPConfig())
    session = bind(service)
    unsupported = await service.get_attention_observation(thread_id="thread", runner="custom")
    assert unsupported.availability == "unsupported"
    session.conn.ext_method.assert_not_awaited()
    session.attention = ObservationQuery(supported=True)
    session.conn.ext_method.return_value = attention()
    async with session.turn_lock:
        result = await service.get_attention_observation(thread_id="thread", runner="custom")
    assert result.availability == "fresh"
    assert result.snapshot.requests[0].agent.agent_id == "child-7"
    assert session.client.pending_permission is None
    session.conn.ext_method.assert_awaited_once_with(
        "sudocode/session/attention", {"sessionId": "session-custom"}
    )


async def test_response_from_replaced_connection_cannot_label_successor():
    service = ACPService(config=ACPConfig())
    session = bind(service)
    session.attention.supported = True
    gate = asyncio.Event()

    async def delayed(*_):
        await gate.wait()
        return attention()

    session.conn.ext_method.side_effect = delayed
    query = asyncio.create_task(
        service.get_attention_observation(thread_id="thread", runner="custom")
    )
    while session.attention.task is None:
        await asyncio.sleep(0)
    replacement = bind(service)
    gate.set()
    result = await query
    assert result.availability == "disconnected"
    assert result.snapshot is None
    assert result.instance_id != replacement.instance_id


async def test_notifications_are_bound_to_session_and_revision():
    service = ACPService(config=ACPConfig())
    session = bind(service)
    client = session.client
    client.bind_engine_session(session.acp_session_id, {"sudocode": {"attention": {"version": 1}}})
    await client.ext_notification(
        "sudocode/session/attention_update",
        {"sessionId": session.acp_session_id, "update": attention()},
    )
    assert client.attention.snapshot.requests[0].id == 7
    with pytest.raises(ValueError, match="bound engine"):
        await client.ext_notification(
            "sudocode/session/attention_update", {"sessionId": "other", "update": attention(2)}
        )
    assert client.attention.snapshot is None
    reply = await client.request_permission(
        session_id=session.acp_session_id,
        options=[{"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"}],
        tool_call={"title": "Read"},
    )
    assert reply.outcome.outcome == "cancelled"
    assert client.pending_permission is None


async def test_idle_child_permission_can_be_answered_without_starting_a_parent_turn():
    service = ACPService(config=ACPConfig())
    session = bind(service)
    session.conn.prompt = AsyncMock()
    client = session.client
    client.bind_engine_session(session.acp_session_id, {"sudocode": {"attention": {"version": 1}}})
    await client.ext_notification(
        "sudocode/session/attention_update",
        {"sessionId": session.acp_session_id, "update": attention()},
    )
    reply = asyncio.create_task(
        client.request_permission(
            session_id=session.acp_session_id,
            options=[{"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"}],
            tool_call={"title": "Read", "_meta": {"sudocode": {"attention": {"requestId": 7}}}},
        )
    )
    await client.wait_for_permission_request()
    # Starting another renderer must not erase a session-owned child reply.
    client.start_prompt(AsyncMock())
    assert client.pending_permission is not None
    result = await service.resume_permission(
        acp_session_id=session.acp_session_id,
        option_id="allow-once",
        on_message=AsyncMock(),
    )
    assert result == {"status": "permission_resolved", "event": None}
    assert (await reply).outcome.option_id == "allow-once"
    assert client.pending_permission is None
    session.conn.prompt.assert_not_called()


async def test_engine_request_removal_cancels_the_matching_host_reply():
    service = ACPService(config=ACPConfig())
    session = bind(service)
    client = session.client
    client.bind_engine_session(session.acp_session_id, {"sudocode": {"attention": {"version": 1}}})
    await client.ext_notification(
        "sudocode/session/attention_update",
        {"sessionId": session.acp_session_id, "update": attention()},
    )
    reply = asyncio.create_task(
        client.request_permission(
            session_id=session.acp_session_id,
            options=[{"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"}],
            tool_call={"title": "Read", "_meta": {"sudocode": {"attention": {"requestId": 7}}}},
        )
    )
    await client.wait_for_permission_request()
    await client.ext_notification(
        "sudocode/session/attention_update",
        {"sessionId": session.acp_session_id, "update": {"revision": 2, "requests": []}},
    )
    assert (await reply).outcome.outcome == "cancelled"
    assert client.pending_permission is None
    with pytest.raises(ValueError, match="No pending"):
        client.resolve_permission("allow-once")

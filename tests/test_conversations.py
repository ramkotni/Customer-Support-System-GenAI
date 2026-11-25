"""Conversation / chat API tests: transcript CRUD, asking, ownership, streaming.

The reply generator is a placeholder (app/services/chat_responder.py), so these
assert the *contract* — turn lifecycle, ids, persistence, context assembly,
SSE framing — not the wording of any answer.
"""
from __future__ import annotations

import json

import pytest

from tests.conftest import PUBLIC, as_customer

CUSTOMER = 1
OTHER_CUSTOMER = 2
BASE = "/api/v1/conversations"


async def _create(client, customer_id: int = CUSTOMER, **body) -> dict:
    r = await client.post(BASE, headers=as_customer(customer_id), json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _ask(client, conversation_id: str, question: str, customer_id: int = CUSTOMER) -> dict:
    r = await client.post(
        f"{BASE}/{conversation_id}/messages",
        headers=as_customer(customer_id),
        json={"content": question},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------- creation ---------------------------------

@pytest.mark.asyncio
async def test_create_conversation_is_empty_and_titled(client):
    convo = await _create(client)
    assert convo["id"].startswith("conv_")
    assert convo["title"] == "New conversation"
    assert convo["turns"] == []
    assert convo["createdAt"] and convo["updatedAt"]


@pytest.mark.asyncio
async def test_create_with_first_message_derives_the_title(client):
    convo = await _create(client, firstMessage="Where is my order ORD-100001?")
    assert convo["title"] == "Where is my order ORD-100001?"


@pytest.mark.asyncio
async def test_create_with_explicit_title_wins(client):
    convo = await _create(client, title="Billing question", firstMessage="Where is my order?")
    assert convo["title"] == "Billing question"


@pytest.mark.asyncio
async def test_long_first_question_is_truncated_on_a_word_boundary(client):
    convo = await _create(client)
    question = ("I need help because my order has not arrived and I am "
                "leaving the country on Friday")
    await _ask(client, convo["id"], question)

    r = await client.get(f"{BASE}/{convo['id']}", headers=as_customer(CUSTOMER))
    title = r.json()["title"]
    assert title.endswith("…")
    assert len(title) <= 49
    assert not title[:-1].endswith(" ")


@pytest.mark.asyncio
async def test_markdown_is_stripped_from_the_title(client):
    convo = await _create(client)
    await _ask(client, convo["id"], '  "**Refund me**  \n  please  ')
    r = await client.get(f"{BASE}/{convo['id']}", headers=as_customer(CUSTOMER))
    assert r.json()["title"] == "Refund me please"


@pytest.mark.asyncio
async def test_customer_scope_is_required(client):
    r = await client.post(BASE, headers=PUBLIC, json={})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "customer_scope_required"


# -------------------------------- asking ----------------------------------

@pytest.mark.asyncio
async def test_ask_returns_a_complete_turn_with_distinct_ids(client):
    convo = await _create(client)
    turn = await _ask(client, convo["id"], "Where is my order?")

    assert turn["id"].startswith("turn_")
    assert turn["status"] == "complete"
    assert turn["human"]["role"] == "human"
    assert turn["human"]["content"] == "Where is my order?"
    assert turn["ai"]["role"] == "ai"
    assert turn["ai"]["content"]
    assert turn["human"]["id"].startswith("msg_")
    assert turn["ai"]["id"].startswith("msg_")
    assert turn["human"]["id"] != turn["ai"]["id"]
    assert turn["error"] is None


@pytest.mark.asyncio
async def test_answer_carries_tool_and_citation_metadata(client):
    convo = await _create(client)
    turn = await _ask(client, convo["id"], "I want a refund")
    meta = turn["ai"]["meta"]
    assert meta is not None
    assert meta["tools"], "the responder should report which tool APIs it used"
    assert meta["citations"], "the responder should cite the knowledge base"
    assert meta["escalated"] is False


@pytest.mark.asyncio
async def test_escalation_is_flagged_in_metadata(client):
    convo = await _create(client)
    turn = await _ask(client, convo["id"], "I want to speak to a human")
    assert turn["ai"]["meta"]["escalated"] is True


@pytest.mark.asyncio
async def test_turns_accumulate_in_order(client):
    convo = await _create(client)
    await _ask(client, convo["id"], "Where is my order?")
    await _ask(client, convo["id"], "Can I get a refund instead?")

    detail = (await client.get(f"{BASE}/{convo['id']}", headers=as_customer(CUSTOMER))).json()
    assert len(detail["turns"]) == 2
    assert detail["turns"][0]["human"]["content"] == "Where is my order?"
    assert detail["turns"][1]["human"]["content"] == "Can I get a refund instead?"
    assert detail["turns"][0]["id"] != detail["turns"][1]["id"]


@pytest.mark.asyncio
async def test_empty_question_is_rejected(client):
    convo = await _create(client)
    r = await client.post(f"{BASE}/{convo['id']}/messages",
                          headers=as_customer(CUSTOMER), json={"content": "   "})
    # Either the min_length rule or the trimmed-empty guard; both are 422.
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_ask_on_a_missing_conversation_is_404(client):
    r = await client.post(f"{BASE}/conv_doesnotexist/messages",
                          headers=as_customer(CUSTOMER), json={"content": "hello"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


# ------------------------- prior turns as context --------------------------

@pytest.mark.asyncio
async def test_prior_turns_are_passed_to_the_responder_as_context(client):
    """The whole point of retaining turns: the responder receives them."""
    from app.api.deps import get_chat_responder
    from app.main import app
    from app.services.chat_responder import ResponseChunk

    seen: list[list] = []

    class RecordingResponder:
        async def respond(self, *, question, history, customer_id):
            seen.append(list(history))
            yield ResponseChunk(text=f"ack:{question}")

    app.dependency_overrides[get_chat_responder] = lambda: RecordingResponder()
    try:
        convo = await _create(client)
        await _ask(client, convo["id"], "first question")
        await _ask(client, convo["id"], "second question")
    finally:
        app.dependency_overrides.pop(get_chat_responder, None)

    assert len(seen) == 2
    assert seen[0] == [], "the first question has no prior context"
    assert [m.role for m in seen[1]] == ["human", "ai"]
    assert seen[1][0].content == "first question"
    assert seen[1][1].content == "ack:first question"
    assert all(m.message_id.startswith("msg_") for m in seen[1])


@pytest.mark.asyncio
async def test_failed_turns_are_excluded_from_context(client):
    from app.api.deps import get_chat_responder
    from app.main import app
    from app.services.chat_responder import ResponseChunk

    seen: list[list] = []
    calls = {"n": 0}

    class FlakyResponder:
        async def respond(self, *, question, history, customer_id):
            seen.append(list(history))
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("generation blew up")
            yield ResponseChunk(text="ok")

    app.dependency_overrides[get_chat_responder] = lambda: FlakyResponder()
    try:
        convo = await _create(client)
        # First ask fails -> handled by the global handler as a 500.
        r = await client.post(f"{BASE}/{convo['id']}/messages",
                              headers=as_customer(CUSTOMER), json={"content": "boom"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "assistant_failed"
        assert "blew up" not in r.text, "internal errors must not leak"

        # The failed turn is recorded, not left dangling as 'pending'.
        detail = (await client.get(f"{BASE}/{convo['id']}",
                                   headers=as_customer(CUSTOMER))).json()
        assert detail["turns"][0]["status"] == "error"

        await _ask(client, convo["id"], "second question")
    finally:
        app.dependency_overrides.pop(get_chat_responder, None)

    assert seen[-1] == [], "the incomplete first turn must not become context"


# ------------------------------- listing ----------------------------------

@pytest.mark.asyncio
async def test_list_conversations_is_newest_first_with_turn_counts(client):
    first = await _create(client, firstMessage="Oldest")
    second = await _create(client, firstMessage="Newest")
    await _ask(client, second["id"], "Where is my order?")

    page = (await client.get(BASE, headers=as_customer(CUSTOMER))).json()
    ids = [c["id"] for c in page["items"]]
    assert ids[0] == second["id"], "most recently active first"
    assert first["id"] in ids
    assert page["meta"]["total"] == 2
    by_id = {c["id"]: c for c in page["items"]}
    assert by_id[second["id"]]["turnCount"] == 1
    assert by_id[first["id"]]["turnCount"] == 0
    assert "turns" not in by_id[first["id"]], "the sidebar payload stays light"


@pytest.mark.asyncio
async def test_list_is_paginated(client):
    for i in range(3):
        await _create(client, firstMessage=f"Question {i}")
    page = (await client.get(BASE, headers=as_customer(CUSTOMER),
                             params={"limit": 2, "offset": 0})).json()
    assert len(page["items"]) == 2
    assert page["meta"] == {"total": 3, "limit": 2, "offset": 0, "returned": 2}


@pytest.mark.asyncio
async def test_unknown_sort_field_is_rejected(client):
    r = await client.get(BASE, headers=as_customer(CUSTOMER), params={"sort": "title); DROP"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_messages_endpoint_returns_a_flat_ordered_list(client):
    convo = await _create(client)
    await _ask(client, convo["id"], "Where is my order?")
    await _ask(client, convo["id"], "And a refund?")

    page = (await client.get(f"{BASE}/{convo['id']}/messages",
                             headers=as_customer(CUSTOMER))).json()
    assert [m["role"] for m in page["items"]] == ["human", "ai", "human", "ai"]
    assert page["meta"]["total"] == 4


# ------------------------------- mutation ---------------------------------

@pytest.mark.asyncio
async def test_rename_conversation(client):
    convo = await _create(client, firstMessage="Where is my order?")
    r = await client.patch(f"{BASE}/{convo['id']}", headers=as_customer(CUSTOMER),
                           json={"title": "Late delivery"})
    assert r.status_code == 200
    assert r.json()["title"] == "Late delivery"


@pytest.mark.asyncio
async def test_delete_conversation_removes_its_transcript(client):
    convo = await _create(client)
    await _ask(client, convo["id"], "Where is my order?")

    r = await client.delete(f"{BASE}/{convo['id']}", headers=as_customer(CUSTOMER))
    assert r.status_code == 204
    assert (await client.get(f"{BASE}/{convo['id']}",
                             headers=as_customer(CUSTOMER))).status_code == 404
    # The cascade must have taken the turns and messages with it.
    assert (await client.get(f"{BASE}/{convo['id']}/messages",
                             headers=as_customer(CUSTOMER))).status_code == 404


@pytest.mark.asyncio
async def test_delete_turn_supports_retry(client):
    convo = await _create(client)
    turn = await _ask(client, convo["id"], "Where is my order?")

    r = await client.delete(f"{BASE}/{convo['id']}/turns/{turn['id']}",
                            headers=as_customer(CUSTOMER))
    assert r.status_code == 204
    detail = (await client.get(f"{BASE}/{convo['id']}", headers=as_customer(CUSTOMER))).json()
    assert detail["turns"] == []


@pytest.mark.asyncio
async def test_delete_turn_from_another_conversation_is_404(client):
    a = await _create(client)
    b = await _create(client)
    turn = await _ask(client, a["id"], "Where is my order?")
    r = await client.delete(f"{BASE}/{b['id']}/turns/{turn['id']}",
                            headers=as_customer(CUSTOMER))
    assert r.status_code == 404


# ------------------------------- ownership --------------------------------

@pytest.mark.asyncio
async def test_another_customer_cannot_read_the_conversation(client):
    convo = await _create(client, customer_id=CUSTOMER)
    r = await client.get(f"{BASE}/{convo['id']}", headers=as_customer(OTHER_CUSTOMER))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ownership_violation"


@pytest.mark.asyncio
async def test_another_customer_cannot_post_or_delete(client):
    convo = await _create(client, customer_id=CUSTOMER)
    other = as_customer(OTHER_CUSTOMER)
    assert (await client.post(f"{BASE}/{convo['id']}/messages", headers=other,
                              json={"content": "hi"})).status_code == 403
    assert (await client.delete(f"{BASE}/{convo['id']}", headers=other)).status_code == 403
    assert (await client.patch(f"{BASE}/{convo['id']}", headers=other,
                               json={"title": "x"})).status_code == 403


@pytest.mark.asyncio
async def test_conversations_are_scoped_to_their_customer_in_listings(client):
    await _create(client, customer_id=CUSTOMER, firstMessage="Mine")
    await _create(client, customer_id=OTHER_CUSTOMER, firstMessage="Theirs")

    mine = (await client.get(BASE, headers=as_customer(CUSTOMER))).json()
    assert [c["title"] for c in mine["items"]] == ["Mine"]
    assert mine["meta"]["total"] == 1


@pytest.mark.asyncio
async def test_api_key_is_required(client):
    r = await client.get(BASE, headers={"X-Customer-Id": "1"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "missing_api_key"


# ------------------------------- streaming --------------------------------

def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, data) pairs, ignoring comments."""
    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        name, payload = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: "):])
        if name is not None:
            events.append((name, payload or {}))
    return events


@pytest.mark.asyncio
async def test_stream_emits_start_deltas_meta_and_done(client):
    convo = await _create(client)
    async with client.stream(
        "POST", f"{BASE}/{convo['id']}/messages/stream",
        headers=as_customer(CUSTOMER), json={"content": "I want a refund"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache, no-transform"
        body = "".join([chunk async for chunk in response.aiter_text()])

    events = _parse_sse(body)
    names = [name for name, _ in events]
    assert names[0] == "start"
    assert names[-1] == "done"
    assert names.count("delta") > 1, "the answer should arrive in several chunks"
    assert "meta" in names

    start = events[0][1]
    assert start["conversationId"] == convo["id"]
    assert start["turnId"].startswith("turn_")
    assert start["humanMessageId"].startswith("msg_")
    assert start["aiMessageId"].startswith("msg_")
    assert start["title"] == "I want a refund"

    answer = "".join(data["text"] for name, data in events if name == "delta")
    assert "refund" in answer.lower()

    meta = next(data["meta"] for name, data in events if name == "meta")
    assert meta["tools"] and meta["citations"]

    done = events[-1][1]
    assert done["status"] == "complete"
    assert done["turnId"] == start["turnId"]
    assert done["aiMessageId"] == start["aiMessageId"]


@pytest.mark.asyncio
async def test_streamed_turn_is_persisted_and_matches_the_stream(client):
    convo = await _create(client)
    async with client.stream(
        "POST", f"{BASE}/{convo['id']}/messages/stream",
        headers=as_customer(CUSTOMER), json={"content": "Where is my order?"},
    ) as response:
        body = "".join([chunk async for chunk in response.aiter_text()])

    events = _parse_sse(body)
    streamed = "".join(d["text"] for n, d in events if n == "delta")
    start = events[0][1]

    detail = (await client.get(f"{BASE}/{convo['id']}", headers=as_customer(CUSTOMER))).json()
    assert len(detail["turns"]) == 1
    turn = detail["turns"][0]
    assert turn["id"] == start["turnId"]
    assert turn["status"] == "complete"
    assert turn["human"]["id"] == start["humanMessageId"]
    assert turn["ai"]["id"] == start["aiMessageId"]
    assert turn["ai"]["content"] == streamed, "stored answer must equal what was streamed"
    assert turn["ai"]["meta"]["tools"]


@pytest.mark.asyncio
async def test_stream_reports_generation_failure_as_an_error_event(client):
    from app.api.deps import get_chat_responder
    from app.main import app
    from app.services.chat_responder import ResponseChunk

    class BrokenResponder:
        async def respond(self, *, question, history, customer_id):
            yield ResponseChunk(text="partial answer")
            raise RuntimeError("secret internal detail")

    app.dependency_overrides[get_chat_responder] = lambda: BrokenResponder()
    try:
        convo = await _create(client)
        async with client.stream(
            "POST", f"{BASE}/{convo['id']}/messages/stream",
            headers=as_customer(CUSTOMER), json={"content": "hello"},
        ) as response:
            assert response.status_code == 200  # headers already sent
            body = "".join([chunk async for chunk in response.aiter_text()])
    finally:
        app.dependency_overrides.pop(get_chat_responder, None)

    events = _parse_sse(body)
    names = [n for n, _ in events]
    assert names[-1] == "error"
    assert "done" not in names
    assert "secret internal detail" not in body, "internal errors must not leak"

    # The partial answer is kept and the turn is marked errored, so Retry works.
    detail = (await client.get(f"{BASE}/{convo['id']}", headers=as_customer(CUSTOMER))).json()
    turn = detail["turns"][0]
    assert turn["status"] == "error"
    assert turn["error"]
    assert turn["ai"]["content"] == "partial answer"


@pytest.mark.asyncio
async def test_stream_enforces_ownership_before_streaming(client):
    convo = await _create(client, customer_id=CUSTOMER)
    r = await client.post(f"{BASE}/{convo['id']}/messages/stream",
                          headers=as_customer(OTHER_CUSTOMER), json={"content": "hi"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ownership_violation"


@pytest.mark.asyncio
async def test_stream_on_a_missing_conversation_is_404(client):
    r = await client.post(f"{BASE}/conv_nope/messages/stream",
                          headers=as_customer(CUSTOMER), json={"content": "hi"})
    assert r.status_code == 404

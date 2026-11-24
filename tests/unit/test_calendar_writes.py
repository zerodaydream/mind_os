"""Calendar writes: validation, risk, approval, bound state, idempotency, ambiguous failure."""

from __future__ import annotations

import json
from datetime import datetime

from mindos.domain import EventDraft, RiskClass, events
from mindos.integrations.calendar import sample_events
from mindos.tools.calendar import CreateEventInput
from mindos.tools.policy import PolicyConfig
from tests.support.calendar_kit import IST, CalEnv, FlakyCalendar, Writes

CREATE = {"title": "Lunch", "start": "2026-11-26T13:00", "end": "2026-11-26T14:00"}
UPDATE = {"event_id": "evt_design", "title": "Design review v2"}
DELETE = {"event_id": "evt_dentist"}


def content(result: object) -> dict[str, object]:
    text = getattr(result, "content")
    return json.loads(text[text.index("{") : text.rindex("}") + 1])


# ---- validation ------------------------------------------------------------------------------
async def test_bad_arguments_are_refused_before_anything_is_asked() -> None:
    env = CalEnv()
    cases = [
        {**CREATE, "attendees": ["not-an-email"]},
        {**CREATE, "start": "tomorrow"},
        {**CREATE, "surprise": 1},
    ]
    for args in cases:
        result = await env.call("calendar.create_event", args)
        assert result.error_code == "invalid_arguments"
    update = await env.call("calendar.update_event", {"event_id": "evt_design"})
    assert update.error_code == "invalid_arguments"  # nothing to change
    assert env.repo.records == {}


async def test_an_event_that_ends_before_it_starts_is_refused_when_it_runs() -> None:
    env = CalEnv()
    result = await env.approved("calendar.create_event", {**CREATE, "end": "2026-11-26T12:00"})
    assert result.error_code == "invalid_arguments"
    assert [e.title for e in env.calendar._events if e.title == "Lunch"] == []


async def test_an_unknown_event_cannot_even_be_proposed() -> None:
    env = CalEnv()
    result = await env.call("calendar.update_event", {"event_id": "nope", "title": "x"})
    assert result.status == "error" and result.error_code == "not_found"
    assert env.repo.records == {}  # nothing for the user to approve


# ---- risk and approval -----------------------------------------------------------------------
async def test_risk_follows_who_is_affected() -> None:
    env = CalEnv()
    specs = {n: env.engine.spec(n) for n in env.engine.granted("cal") if "event" in n}
    own = specs["calendar.create_event"].classify
    assert own is not None
    assert own(CreateEventInput(**CREATE)) is RiskClass.R2
    assert own(CreateEventInput(**CREATE, attendees=["a@example.com"])) is RiskClass.R3
    for name in ("calendar.update_event", "calendar.delete_event"):
        classify = specs[name].classify
        assert classify is not None and classify(None) is RiskClass.R3


async def test_every_write_asks_by_default_and_nothing_runs_before_approval() -> None:
    env = CalEnv()
    for tool, args in (
        ("calendar.create_event", CREATE),
        ("calendar.update_event", UPDATE),
        ("calendar.delete_event", DELETE),
    ):
        result = await env.call(tool, args, call_id=f"c_{tool}")
        assert result.status == "pending_approval"
    assert await env.calendar.get_event("evt_dentist") is not None
    assert (await env.calendar.get_event("evt_design")).title == "Design review"  # type: ignore[union-attr]
    assert [e.approval.risk for e in env.events if isinstance(e, events.ApprovalRequested)] == [
        RiskClass.R2,
        RiskClass.R3,
        RiskClass.R3,
    ]


async def test_relaxing_r2_frees_a_private_event_but_invitations_still_ask() -> None:
    env = CalEnv(config=PolicyConfig(r2_requires_approval=False))
    private = await env.call("calendar.create_event", CREATE, call_id="a")
    assert private.status == "ok"
    invited = await env.call(
        "calendar.create_event", {**CREATE, "attendees": ["boss@example.com"]}, call_id="b"
    )
    assert invited.status == "pending_approval"


async def test_approved_create_makes_the_event_once_with_evidence() -> None:
    env = CalEnv()
    result = await env.approved("calendar.create_event", CREATE)
    assert result.status == "ok"
    body = content(result)
    event = body["event"]
    assert isinstance(event, dict) and event["title"] == "Lunch"
    assert body["invitations_sent"] is False
    assert [(e.kind, e.id) for e in result.evidence] == [("calendar_event", event["id"])]
    again = await env.call("calendar.create_event", CREATE, approval_id=env.only_approval().id)
    assert again.error_code == "approval_already_used"
    assert len([e for e in env.calendar._events if e.title == "Lunch"]) == 1


async def test_invitations_flag_and_notify_choice_are_carried_to_the_adapter() -> None:
    env = CalEnv()
    args = {"event_id": "evt_design", "location": "Room 9", "notify_attendees": False}
    result = await env.approved("calendar.update_event", args)
    assert result.status == "ok" and content(result)["attendees_notified"] is False
    assert isinstance(env.calendar, Writes) and env.calendar.notified == [False]
    assert content(result)["changed"] == ["location"]


# ---- bound state -----------------------------------------------------------------------------
async def test_the_approval_shows_the_event_the_user_is_changing() -> None:
    env = CalEnv()
    await env.call("calendar.delete_event", DELETE)
    preview = env.only_approval().payload_preview
    assert "Dentist" in preview and "_resolved_targets" in preview


async def test_an_event_that_changed_after_approval_is_not_touched() -> None:
    env = CalEnv()
    pending = await env.call("calendar.update_event", UPDATE)
    aid = pending.approval_id or ""
    await env.broker.resolve(aid, decision="approve")
    # someone moves the meeting after the user looked at it
    await env.calendar.update_event(
        "evt_design",
        __import__("mindos.domain").domain.EventPatch(location="Elsewhere"),
        notify=False,
    )  # type: ignore[attr-defined]
    result = await env.call("calendar.update_event", UPDATE, approval_id=aid)
    assert result.error_code == "approval_invalidated"
    event = await env.calendar.get_event("evt_design")
    assert event is not None and event.title == "Design review" and event.location == "Elsewhere"


async def test_deleting_an_event_that_vanished_reports_not_found_after_approval() -> None:
    env = CalEnv()
    pending = await env.call("calendar.delete_event", DELETE)
    aid = pending.approval_id or ""
    await env.broker.resolve(aid, decision="approve")
    await env.calendar.delete_event("evt_dentist", notify=False)  # gone meanwhile
    result = await env.call("calendar.delete_event", DELETE, approval_id=aid)
    assert result.status == "denied" and result.error_code == "approval_invalidated"


# ---- ambiguous failure -----------------------------------------------------------------------
async def test_a_create_that_failed_ambiguously_says_so_and_is_not_repeated() -> None:
    flaky = FlakyCalendar(sample_events(datetime(2026, 11, 23, 9, tzinfo=IST), IST))
    flaky.fail_create = TimeoutError()
    env = CalEnv(calendar=flaky)
    result = await env.approved("calendar.create_event", CREATE)
    assert result.error_code == "timeout" and "may or may not have happened" in result.content
    assert flaky.create_attempts == 1  # the engine never retries a side effect
    again = await env.call("calendar.create_event", CREATE, approval_id=env.only_approval().id)
    assert again.error_code == "approval_already_used"
    assert "Do not repeat it" in again.content
    # the reconcile path: the same call key finds the event it already made
    assert len([e for e in flaky._events if e.title == "Lunch"]) == 1


async def test_a_repeat_with_the_same_key_never_creates_a_second_event() -> None:
    env = CalEnv()
    draft = EventDraft(
        title="Once",
        start=datetime(2026, 12, 1, 9, tzinfo=IST),
        end=datetime(2026, 12, 1, 10, tzinfo=IST),
    )
    first = await env.calendar.create_event(draft, request_key="k1", notify=False)
    second = await env.calendar.create_event(draft, request_key="k1", notify=False)
    assert first.id == second.id
    assert (await env.calendar.find_created("k1")) == first


# ---- injection -------------------------------------------------------------------------------
async def test_reading_untrusted_event_text_makes_even_a_relaxed_write_ask() -> None:
    env = CalEnv(config=PolicyConfig(r2_requires_approval=False))
    await env.call("calendar.get_event", {"event_id": "evt_design"}, call_id="r1")  # untrusted
    result = await env.call("calendar.create_event", CREATE, call_id="w1")
    assert result.status == "pending_approval"
    assert env.only_approval().tainted is True

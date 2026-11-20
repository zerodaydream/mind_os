from datetime import datetime, timedelta, timezone

from backend.services.retrieval.core.search_memories import score_memory_candidate


def test_overlap_ranks_higher() -> None:
    now = datetime.now(timezone.utc)
    high = score_memory_candidate(
        query="prefer morning meetings",
        content="I prefer morning meetings",
        updated_at=now,
    )
    low = score_memory_candidate(
        query="prefer morning meetings",
        content="I live in Bangalore",
        updated_at=now,
    )
    assert high > low


def test_recency_boost() -> None:
    now = datetime.now(timezone.utc)
    recent = score_memory_candidate(
        query="timezone",
        content="My timezone is IST",
        updated_at=now,
    )
    old = score_memory_candidate(
        query="timezone",
        content="My timezone is IST",
        updated_at=now - timedelta(days=120),
    )
    assert recent > old

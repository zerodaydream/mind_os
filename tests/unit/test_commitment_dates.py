from backend.services.commitments.core.dates import parse_due_at


def test_by_iso_date() -> None:
    dt = parse_due_at("I will send the deck by 2026-12-01")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 12 and dt.day == 1

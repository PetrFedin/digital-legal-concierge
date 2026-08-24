from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_claim_sent_notification_uses_shared_business_datetime_formatter():
    source = read("app/api/lawyer_m1_claim.py")

    assert "from app.presentation_time import format_business_datetime" in source
    handler = source.split("async def mark_claim_sent", 1)[1].split(
        "async def open_court_stage", 1
    )[0]
    assert "format_business_datetime(due_at)" in handler
    assert '%H:%M UTC' not in handler
    assert '"claim_due_at": due_at.isoformat()' in handler

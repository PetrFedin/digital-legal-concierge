from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_client_statuses_do_not_expose_success_fee_jargon():
    timeline = read("app/domain/cases/case_timeline.py")

    assert 'CaseStatus.M1_WAITING_SUCCESS_FEE: "Ожидается финальный платёж"' in timeline
    assert 'CaseStatus.M1_SUCCESS_FEE_RECEIVED: "Финальный платёж получен"' in timeline
    assert '"Ожидается оплата success fee"' not in timeline
    assert '"Success fee оплачен"' not in timeline


def test_rejected_m1_is_not_presented_as_completed_before_client_decision():
    timeline = read("app/domain/cases/case_timeline.py")
    recovery = read("app/bot/screens/m1_rejection_recovery.py")

    assert 'CaseStatus.M1_REJECTED: "Ведение дела не принято — выберите следующий шаг"' in timeline
    assert "CaseStatus.M1_REJECTED: 35" in timeline
    assert "CaseStatus.M1_REJECTED: 100" not in timeline
    assert "Перейти в консультацию" in recovery
    assert "Завершить обращение" in recovery

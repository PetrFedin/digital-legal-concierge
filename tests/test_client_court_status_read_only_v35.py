from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_client_court_status_never_mutates_waiting_period_into_court():
    source = (ROOT / "app/bot/screens/m1_stages.py").read_text(encoding="utf-8")
    start = source.index('c.data == "court_status"')
    end = source.index('c.data == "pay_court_70000"')
    block = source[start:end]

    assert "CaseStatus.M1_WAITING_30_DAYS" in block
    assert "Просмотр этого экрана не открывает судебный этап" in block
    assert "CaseStatus.M1_COURT_STAGE" in block
    assert "change_status(" not in block
    assert "M1_COURT_STAGE," not in block
    assert "court/open" not in block

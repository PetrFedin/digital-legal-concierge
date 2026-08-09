from app.domain.cases.case_timeline import (
    get_case_progress_percent,
    get_client_visible_status,
)
from app.domain.statuses.case_statuses import CaseStatus


def test_document_rework_and_contract_handoff_have_explicit_client_labels():
    assert (
        get_client_visible_status(CaseStatus.M1_DOCS_REQUESTED)
        == "Нужна новая версия документа"
    )
    assert (
        get_client_visible_status(CaseStatus.M1_DOCUMENTS_RECEIVED)
        == "Документы получены"
    )
    assert (
        get_client_visible_status(CaseStatus.M1_ACCEPTED)
        == "Дело принято юристом"
    )
    assert (
        get_client_visible_status(CaseStatus.M1_CONTRACT_READY)
        == "Договор готов"
    )


def test_document_rework_progress_returns_to_document_stage_without_resetting_case():
    assert get_case_progress_percent(CaseStatus.M1_LAWYER_REVIEW) == 30
    assert get_case_progress_percent(CaseStatus.M1_DOCS_REQUESTED) == 25
    assert get_case_progress_percent(CaseStatus.M1_CONTRACT_READY) == 40


def test_unknown_future_status_remains_safe_and_non_mutating():
    assert get_client_visible_status("FUTURE_CASE_STATUS") == "Статус обновляется"
    assert get_case_progress_percent("FUTURE_CASE_STATUS") == 0

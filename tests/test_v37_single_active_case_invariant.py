from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_case_service_uses_operation_idempotency_not_client_singleton() -> None:
    service = read("app/domain/cases/case_service.py")

    assert "async def create_case_for_operation" in service
    assert "CaseCreationRequest" in service
    assert "operation_key" in service
    assert "different operation_key creates a different Case" in service
    assert "async def get_active_cases_for_user" in service
    assert "async def select_case_for_user" in service
    assert "ClientCaseContext" in service
    assert "Case.status.notin_(_TERMINAL_CASE_VALUES)" in service


def test_m2_intake_supports_new_operation_or_explicit_existing_case() -> None:
    intake = read("app/domain/consultations/consultation_intake.py")

    assert "case_id: int | None = None" in intake
    assert "operation_key: str | None = None" in intake
    assert "self.cases.create_case_for_operation(" in intake
    assert 'purpose="m2_consultation_start"' in intake
    assert "route=RouteCode.M2" in intake
    assert "status=CaseStatus.M2_DESCRIPTION_PENDING" in intake
    assert "self.cases.get_case_for_user(" in intake
    assert "ActiveCaseRouteConflict" in intake
    assert "client-wide singleton" in intake


def test_migration_0014_retires_invalid_client_wide_active_case_index() -> None:
    migration = read("migrations/versions/20260819_0014_one_active_case_per_client.py")

    assert 'revision = "20260819_0014"' in migration
    assert 'down_revision = "20260806_0013"' in migration
    assert 'INDEX_NAME = "uq_cases_one_active_per_client"' in migration
    assert "must never create a client-wide partial unique index" in migration
    assert "_drop_invalid_index_if_present()" in migration
    assert "op.drop_index(INDEX_NAME, table_name=\"cases\")" in migration
    assert "op.create_index" not in migration


def test_migration_0017_encodes_multi_case_context_and_operation_ledger() -> None:
    migration = read("migrations/versions/20260819_0017_multi_case_calculation_history.py")

    assert 'revision = "20260819_0017"' in migration
    assert 'down_revision = "20260819_0016"' in migration
    assert 'INVALID_ACTIVE_CASE_INDEX = "uq_cases_one_active_per_client"' in migration
    assert "_drop_invalid_client_index(bind)" in migration
    assert 'op.create_table(\n        "client_case_contexts"' in migration
    assert 'op.create_table(\n        "case_creation_requests"' in migration
    assert 'name="uq_case_creation_client_operation"' in migration
    assert "_make_calculations_one_to_many(bind)" in migration
    assert "unique=False" in migration
    assert "do not restore the invalid client-wide Case uniqueness" in migration


def test_database_still_enforces_one_live_consultation_per_case() -> None:
    migration = read(
        "migrations/versions/20260819_0015_one_active_consultation_per_case.py"
    )

    assert 'revision = "20260819_0015"' in migration
    assert 'down_revision = "20260819_0014"' in migration
    assert 'INDEX_NAME = "uq_consultations_one_active_per_case"' in migration
    for terminal in (
        "DONE",
        "CLIENT_NO_SHOW",
        "LAWYER_NO_SHOW",
        "CANCELLED",
        "RESCHEDULED",
        "CLOSED",
    ):
        assert terminal in migration
    assert "SELECT case_id, COUNT(*) AS active_count" in migration
    assert "HAVING COUNT(*) > 1" in migration
    assert 'unique=True' in migration


def test_retired_active_case_integrity_guard_registers_no_shadow_route() -> None:
    retired = read("app/api/active_case_integrity_guard.py")
    current = read("app/api/workdesk_integrity_guard.py")

    assert "retired client-wide active-case guard" in retired
    assert "Multiple active Cases for one client are valid product data" in retired
    assert "router = APIRouter" in retired
    assert "@router." not in retired
    assert "multiple_active_cases_for_client" not in current
    assert "duplicate_active_client_count" not in current


def test_current_runtime_regressions_cover_same_and_distinct_case_operations() -> None:
    postgres = read("tests/test_postgres_multi_case_concurrency.py")
    telegram = read("tests/test_telegram_multi_case_runtime.py")

    assert "test_same_source_operation_is_idempotent_under_postgres_concurrency" in postgres
    assert "test_distinct_source_operations_remain_distinct_cases_under_postgres_concurrency" in postgres
    assert "test_terminal_source_operation_replay_does_not_reselect_completed_case" in postgres
    assert "test_persistent_calculate_creates_distinct_case_and_replay_is_idempotent" in telegram
    assert "test_my_case_selector_preserves_context_until_explicit_selection" in telegram

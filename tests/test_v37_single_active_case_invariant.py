from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_case_service_serializes_get_or_create_on_client_row():
    service = read("app/domain/cases/case_service.py")

    assert "async def get_or_create_active_case_for_user" in service
    assert "select(User)" in service
    assert ".with_for_update()" in service
    lock_at = service.index(".with_for_update()")
    recheck_at = service.index("case = await self.get_active_case_for_user", lock_at)
    create_at = service.index("return await self.create_case", recheck_at)
    assert lock_at < recheck_at < create_at
    assert 'raise LookupError("Клиент не найден")' in service


def test_telegram_context_and_m2_intake_use_serialized_case_creation():
    context = read("app/bot/context.py")
    intake = read("app/domain/consultations/consultation_intake.py")

    assert "return await self.case_service.get_or_create_active_case_for_user(user)" in context
    assert "self.cases.get_or_create_active_case_for_user(" in intake
    assert "route=RouteCode.M2" in intake
    assert "status=CaseStatus.M2_DESCRIPTION_PENDING" in intake
    assert "ActiveCaseRouteConflict" in intake


def test_database_migration_enforces_partial_unique_active_case_index():
    migration = read("migrations/versions/20260819_0014_one_active_case_per_client.py")

    assert 'revision = "20260819_0014"' in migration
    assert 'down_revision = "20260806_0013"' in migration
    assert 'INDEX_NAME = "uq_cases_one_active_per_client"' in migration
    assert "status NOT IN ('M1_CLOSED', 'M2_CLOSED', 'ARCHIVED')" in migration
    assert "SELECT client_id, COUNT(*) AS active_count" in migration
    assert "HAVING COUNT(*) > 1" in migration
    assert "will not auto-close or archive client matters" in migration
    assert 'unique=True' in migration
    assert 'kwargs["postgresql_where"] = predicate' in migration
    assert 'kwargs["sqlite_where"] = predicate' in migration


def test_workdesk_integrity_surfaces_historical_duplicate_active_cases():
    guard = read("app/api/active_case_integrity_guard.py")
    operator = read("app/api/operator_guard.py")
    initial = read("app/api/initial_setup_wizard.py")

    assert '@router.get("/admin/workdesk/integrity")' in guard
    assert "result = await base_integrity(" in guard
    assert "multiple_active_cases_for_client" in guard
    assert "У клиента одновременно несколько активных дел" in guard
    assert "Ничего не закрывайте автоматически" in guard
    assert 'result["duplicate_active_client_count"] = len(conflicts)' in guard
    assert "router.include_router(active_case_integrity_guard_router)" in operator

    # initial_setup mounts operator_guard before its historical integrity guard;
    # inside operator_guard the new exact route is therefore effective first.
    assert initial.index("router.include_router(operator_guard_router)") < initial.index(
        "router.include_router(workdesk_integrity_guard_router)"
    )

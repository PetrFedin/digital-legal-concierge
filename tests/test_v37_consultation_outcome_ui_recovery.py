from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_consultation_outcome_ui_has_one_product_owner_and_preserves_existing_m2_patches():
    product = read("app/api/consultation_outcomes_product.py")
    retired_guard = read("app/api/consultation_outcomes_ui_guard.py")
    initial_setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert 'prefix="/admin/consultation-outcomes"' in product
    assert '@router.get("/ui"' in product
    assert "_inject_client_no_show_ui(OUTCOMES_HTML)" in product
    assert "inject_legacy_outcome_ui(html)" in product
    assert "legacy_outcome_queue" in product
    assert "resolve_legacy_outcome" in product

    # The old guard is deliberately importable but owns no public path and the
    # setup router must no longer mount it as a competing FastAPI owner.
    assert "router = APIRouter" in retired_guard
    assert "@router." not in retired_guard
    assert "router.add_api_route" not in retired_guard
    assert "consultation_outcomes_ui_guard_router" not in initial_setup

    assert "consultation_outcomes_product_router" in main
    assert "guided_consultation_outcomes_router" not in main
    assert "consultation_outcomes_ui_guard_router" not in main


def test_consultation_outcome_product_ui_recovers_wrong_or_incomplete_staff_role():
    source = read("app/api/consultation_outcomes_product.py")

    assert "except DocumentAccessError as error:" in source
    assert "except HTTPException as error:" in source
    assert "error.status_code in {403, 409}" in source
    assert 'RedirectResponse(url="/login", status_code=303)' in source
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in source
    assert "actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}" in source

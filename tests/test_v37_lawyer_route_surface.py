from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_enforcement_router_owns_lawyer_prefix_when_mounted_directly():
    source = read("app/api/lawyer_m1_enforcement.py")
    assert 'APIRouter(prefix="/lawyer"' in source
    assert '@router.post("/cases/{case_id}/enforcement/money-received")' in source


def test_claim_router_does_not_nest_enforcement_a_second_time():
    source = read("app/api/lawyer_m1_claim.py")
    assert "lawyer_m1_enforcement" not in source
    assert "include_router(enforcement_router)" not in source
    assert 'APIRouter(prefix="/lawyer"' in source


def test_main_direct_mount_keeps_single_intended_lawyer_enforcement_surface():
    main = read("app/main.py")
    assert '("lawyer_m1_enforcement", lawyer_m1_enforcement_router)' in main
    assert '("lawyer_m1_claim", lawyer_m1_claim_router)' in main

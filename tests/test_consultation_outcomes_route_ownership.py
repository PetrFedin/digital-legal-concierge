from __future__ import annotations

from collections import Counter

from app.api.consultation_outcomes_ui_guard import router as retired_ui_guard_router
from app.main import create_app


PREFIX = "/admin/consultation-outcomes"
EXPECTED = {
    ("GET", PREFIX),
    ("GET", f"{PREFIX}/slots"),
    ("GET", f"{PREFIX}/ui"),
    ("GET", f"{PREFIX}/legacy"),
    ("POST", f"{PREFIX}/{{consultation_id}}/lawyer-no-show"),
    ("POST", f"{PREFIX}/{{consultation_id}}/rebook"),
    ("POST", f"{PREFIX}/{{consultation_id}}/refund"),
    ("POST", f"{PREFIX}/{{consultation_id}}/client-no-show/rebook"),
    ("POST", f"{PREFIX}/{{consultation_id}}/client-no-show/close"),
    ("POST", f"{PREFIX}/{{consultation_id}}/legacy/resolve"),
}


def test_consultation_outcomes_has_one_runtime_owner_per_method_path() -> None:
    app = create_app()
    counts: Counter[tuple[str, str]] = Counter()
    for route in app.routes:
        path = str(getattr(route, "path", ""))
        if not path.startswith(PREFIX):
            continue
        for method in set(getattr(route, "methods", set()) or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            counts[(str(method), path)] += 1

    assert set(counts) == EXPECTED
    assert all(count == 1 for count in counts.values())


def test_consultation_outcomes_ui_guard_is_a_route_free_compatibility_shim() -> None:
    assert retired_ui_guard_router.routes == []

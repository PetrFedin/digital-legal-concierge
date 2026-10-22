"""The deployment gate must inspect endpoints inside FastAPI's lazy routers."""
from fastapi import APIRouter, FastAPI

from scripts.architecture_check import check_runtime_routes


def application_with_two_staff_routes(second_method):
    app = FastAPI()
    staff = APIRouter(prefix="/staff")
    first = APIRouter(prefix="/cases")
    second = APIRouter(prefix="/cases")

    async def read_case():
        return {"case": 1}

    async def update_case():
        return {"updated": True}

    first.add_api_route("/{case_id}", read_case, methods=["GET"])
    second.add_api_route("/{case_id}", update_case, methods=[second_method])
    staff.include_router(first)
    staff.include_router(second)
    app.include_router(staff)
    return app


def test_architecture_gate_catches_shadowed_nested_endpoints(monkeypatch):
    app = application_with_two_staff_routes("GET")
    monkeypatch.setattr("app.main.create_app", lambda: app)
    errors = check_runtime_routes()
    assert len(errors) == 1
    assert "GET /staff/cases/{case_id}" in errors[0]
    assert "read_case" in errors[0] and "update_case" in errors[0]


def test_architecture_gate_allows_distinct_methods_on_same_nested_path(monkeypatch):
    app = application_with_two_staff_routes("POST")
    monkeypatch.setattr("app.main.create_app", lambda: app)
    assert check_runtime_routes() == []

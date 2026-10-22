"""Defend staff spreadsheet exports without modifying stored legal evidence.

This checks formula prefixes, CSV column boundaries, numeric types and
cache behavior. Spreadsheet save/reopen still requires a separate manual UAT.
"""
from decimal import Decimal
import csv
import io

import httpx
import pytest
from fastapi import FastAPI

from app.api.exports import csv_response, safe_spreadsheet_cell


@pytest.mark.parametrize(
    "value,expected",
    [
        ("=2+2", "'=2+2"),
        ("+2+2", "'+2+2"),
        ("-SUM(2,2)", "'-SUM(2,2)"),
        ("@SUM(2,2)", "'@SUM(2,2)"),
        (" \t=2+2", "' \t=2+2"),
        ("\r=2+2", "'\r=2+2"),
        ("\n=2+2", "'\n=2+2"),
        ("＝2+2", "'＝2+2"),
        ("＋2+2", "'＋2+2"),
        ("－2+2", "'－2+2"),
        ("＠SUM(2,2)", "'＠SUM(2,2)"),
        ("\ufeff=2+2", "'\ufeff=2+2"),
        ("Пётр, клиент", "Пётр, клиент"),
        ("'already text", "'already text"),
        ("", ""),
        (None, None),
        (Decimal("-123.45"), Decimal("-123.45")),
        (123, 123),
    ],
)
def test_risky_spreadsheet_text_is_escaped_but_typed_numbers_unchanged(
    value, expected,
):
    assert safe_spreadsheet_cell(value) == expected


@pytest.mark.asyncio
async def test_csv_formula_and_delimiter_isolation_with_real_http_response():
    app = FastAPI()

    @app.get("/protected-csv-test")
    async def download():
        return csv_response(
            "synthetic.csv",
            [
                {
                    "id": 1001,
                    "customer_name": "=1+1",
                    "phone": "+79990000001",
                    "notes": '" ,=1+1\r\n=3+3',
                    "balance": Decimal("-245.20"),
                    "normal": "Обычный текст",
                },
            ],
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/protected-csv-test")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == "attachment; filename=synthetic.csv"
    assert response.text.startswith('"id","customer_name"')
    cells = list(csv.DictReader(io.StringIO(response.text, newline="")))
    assert len(cells) == 1
    row = cells[0]
    assert len(row) == 6
    assert row["customer_name"] == "'=1+1"
    assert row["phone"] == "'+79990000001"
    assert row["notes"] == '" ,=1+1\r\n=3+3'
    assert row["balance"] == "-245.20"
    assert row["normal"] == "Обычный текст"


@pytest.mark.asyncio
async def test_empty_csv_response_is_non_cacheable():
    app = FastAPI()

    @app.get("/empty")
    async def empty():
        return csv_response("empty.csv", [])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/empty")
    assert response.status_code == 200
    assert response.text == "empty\r\n"
    assert response.headers["cache-control"] == "private, no-store"

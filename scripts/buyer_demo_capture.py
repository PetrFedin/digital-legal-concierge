"""Buyer-facing *test-only* Chromium evidence of the real staff web UI.

This command is invoked only after the existing browser_staff_e2e.py step has
seeded a DISPOSABLE CI PostgreSQL database and validated role boundaries.
It never starts Telegram, processes payments or reads customer records.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from scripts.browser_staff_e2e import PASSWORD


def check_environment() -> None:
    if os.getenv("DLC_DEMO_CAPTURE") != "1":
        raise RuntimeError("Capture disabled: DLC_DEMO_CAPTURE must equal 1")
    if os.getenv("APP_ENV") != "test":
        raise RuntimeError("Capture refused: APP_ENV is not test")
    if os.getenv("RUN_BOT", "").lower() != "false":
        raise RuntimeError("Capture refused: RUN_BOT is not false")
    if os.getenv("RUN_SCHEDULER", "").lower() != "false":
        raise RuntimeError("Capture refused: RUN_SCHEDULER is not false")

    db = os.getenv("DATABASE_URL", "")
    if not db.startswith("postgresql+asyncpg://"):
        raise RuntimeError("Capture refused: only disposable PostgreSQL")
    db_url = urlsplit(db.replace("postgresql+asyncpg://", "postgresql://", 1))
    if db_url.hostname not in {"localhost", "127.0.0.1"} or db_url.path != "/dlc_browser_ci":
        raise RuntimeError("Capture refused: nonlocal or non-CI database")

    base = os.getenv("BROWSER_E2E_BASE_URL", "")
    if base != "http://127.0.0.1:18101":
        raise RuntimeError("Capture refused: unexpected browser target")


def login_and_capture(browser, *, username: str, target: str, out: Path,
                      name: str, expected: str, forbidden: str | None = None):
    base = "http://127.0.0.1:18101"
    recordings = out / "recording_raw"
    recordings.mkdir(exist_ok=True)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        device_scale_factor=1,
        record_video_dir=str(recordings),
        record_video_size={"width": 1440, "height": 900},
    )
    page = context.new_page()
    js_errors: list[str] = []
    page.on("pageerror", lambda exc: js_errors.append(str(exc)))
    try:
        page.goto(base + "/login", wait_until="domcontentloaded")
        expect(page.locator('input[name="username"]')).to_be_visible(timeout=15000)
        page.locator('input[name="username"]').fill(username)
        page.locator('input[name="password"]').fill(PASSWORD)
        page.get_by_role("button", name="Войти в кабинет").click()
        page.wait_for_url("**/admin-ui", timeout=20000)
        page.goto(base + target, wait_until="domcontentloaded")
        if name == "admin":
            expect(page.locator("#view")).to_contain_text("Что требует внимания", timeout=20000)
            page.wait_for_timeout(800)
            page.screenshot(path=str(out / "admin_overview.png"), animations="disabled")
            page.locator('button[data-tab="cases"]').click()
            expect(page.locator("#view")).to_contain_text(expected, timeout=15000)
        elif name == "lawyer":
            expect(page.locator("#casesContent")).to_contain_text(expected, timeout=15000)
            if forbidden is not None:
                expect(page.locator("#casesContent")).not_to_contain_text(forbidden)
        else:
            raise RuntimeError("Unknown capture role")
        page.wait_for_timeout(1500)
        image_path = out / (name + ".png")
        page.screenshot(path=str(image_path), animations="disabled")
        page.mouse.move(1060, 400, steps=15)
        page.wait_for_timeout(1600)
        page.mouse.wheel(0, 340)
        page.wait_for_timeout(1200)
        page.mouse.wheel(0, -340)
        page.wait_for_timeout(850)
        if js_errors:
            raise RuntimeError("JS errors during browser capture: " + "; ".join(js_errors))
        video = page.video
    finally:
        page.close()
        context.close()
    if video is None:
        raise RuntimeError("Chromium did not record video")
    Path(video.path()).replace(out / (name + ".webm"))
    return {
        "image": image_path.name,
        "video": name + ".webm",
        "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
    }


def main() -> None:
    check_environment()
    out = Path("buyer_demo_evidence").resolve()
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            admin = login_and_capture(
                browser,
                username="browser-admin",
                target="/admin-ui",
                out=out,
                name="admin",
                expected="BROWSER-OWN-2026",
            )
            lawyer = login_and_capture(
                browser,
                username="browser-lawyer",
                target="/lawyer/ui",
                out=out,
                name="lawyer",
                expected="BROWSER-OWN-2026",
                forbidden="BROWSER-FOREIGN-2026",
            )
        finally:
            browser.close()

    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                            text=True, check=True).stdout.strip()
    manifest = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha": commit,
        "github_run_id": os.getenv("GITHUB_RUN_ID", "unknown"),
        "environment": "isolated ephemeral GitHub Actions PostgreSQL",
        "data_origin": "synthetic browser_staff_e2e seed (not real clients)",
        "screens": {"admin": admin, "lawyer": lawyer},
        "telegram_client": "NOT CAPTURED: requires a separate test Telegram account",
        "production_ready": False,
        "claim": "Real Chromium session against application web UI in isolated CI; not production UAT",
    }
    (out / "evidence_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for item in ("admin.png", "lawyer.png", "admin_overview.png", "admin.webm", "lawyer.webm"):
        if (out / item).stat().st_size < 1000:
            raise RuntimeError("Missing/empty evidence asset: " + item)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

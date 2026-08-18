from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_effective_auth_session_no_longer_exposes_bearer_to_browser_js():
    guard = read("app/api/staff_ui_shell_guard.py")
    legacy_auth = read("app/api/auth.py")
    main = read("app/main.py")

    assert '@router.get("/auth/session")' in guard
    assert '"api_token": BROWSER_SESSION_SENTINEL' in guard
    assert '"session_transport": "httponly_cookie"' in guard
    assert '"api_token": token' in legacy_auth

    # The hardened early route must shadow the historical token-returning route.
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"auth\", auth_router)'
    )


def test_cookie_header_bridge_keeps_legacy_staff_api_helpers_working():
    security = read("app/security/http_security.py")

    assert 'BROWSER_SESSION_SENTINEL = "httponly-cookie-session"' in security
    assert "def _explicit_bearer_token" in security
    assert "token == BROWSER_SESSION_SENTINEL" in security
    assert "def _inject_cookie_admin_header" in security
    assert 'name.lower() != ADMIN_TOKEN_HEADER' in security
    assert 'headers.append((ADMIN_TOKEN_HEADER, str(session_cookie).encode("latin-1")))' in security


def test_cookie_mutations_still_pass_origin_guard_before_internal_header_injection():
    security = read("app/security/http_security.py")
    dispatch = security.split("async def dispatch(self, request: Request, call_next):", 1)[1]

    cross_site = dispatch.index('if fetch_site == "cross-site" and has_ambient_credentials:')
    origin_allowed = dispatch.index("if supplied_origin and supplied_origin not in allowed_origins(request):")
    missing_origin = dispatch.index('and settings.app_env == "production"')
    unsafe_inject = dispatch.rindex("_inject_cookie_admin_header(request, session_cookie)")

    assert cross_site < unsafe_inject
    assert origin_allowed < unsafe_inject
    assert missing_origin < unsafe_inject


def test_safe_cookie_reads_are_bridged_without_requiring_csrf_origin():
    security = read("app/security/http_security.py")
    dispatch = security.split("async def dispatch(self, request: Request, call_next):", 1)[1]
    safe_block = dispatch.split("if method in SAFE_METHODS:", 1)[1].split(
        "if request.url.path.startswith(WEBHOOK_PREFIXES):", 1
    )[0]

    assert "_inject_cookie_admin_header(request, session_cookie)" in safe_block
    assert "return await call_next(request)" in safe_block


def test_real_bearer_api_clients_keep_existing_transport_contract():
    security = read("app/security/http_security.py")

    assert "return token if decode_access_token(token) else None" in security
    assert "if explicit_bearer:" in security
    assert "return await call_next(request)" in security


def test_sensitive_staff_surfaces_are_never_browser_cacheable():
    security = read("app/security/http_security.py")

    # These paths contain client/legal/payment data or privileged operational
    # controls and must not fall outside the global no-store boundary merely
    # because their historical route did not begin with /admin.
    for prefix in (
        "/document-access",
        "/contracts",
        "/message-center",
        "/consultation-slots",
        "/search-center",
        "/audit",
        "/retention",
        "/recovery",
        "/settings-ui",
        "/diagnostic-center",
        "/initial-setup-wizard",
        "/launch-check",
    ):
        assert f'"{prefix}"' in security

    assert 'response.headers["Cache-Control"] = "no-store, max-age=0"' in security
    assert 'response.headers["Pragma"] = "no-cache"' in security

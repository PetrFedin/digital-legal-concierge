import inspect

from app.api.auth import LOGIN_HTML, _login_response, _prefers_html, login


def test_login_page_contains_accessible_inline_error_and_account_guidance():
    assert "__ERROR__" in LOGIN_HTML
    assert "Используйте логин или email вашей учетной записи кабинета" in LOGIN_HTML
    assert (
        "Пароль из переменных сервера не заменяет пароль уже созданного пользователя"
        in LOGIN_HTML
    )
    assert 'value="__USERNAME__"' in LOGIN_HTML
    assert 'autocomplete="current-password"' in LOGIN_HTML


def test_login_response_escapes_values_and_never_replays_password():
    response = _login_response(
        error="<script>alert(1)</script>",
        username='legal-concierge"><script>',
        status_code=401,
    )
    body = response.body.decode("utf-8")
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "legal-concierge&quot;&gt;&lt;script&gt;" in body
    assert 'role="alert"' in body
    assert response.status_code == 401
    assert "no-store" in response.headers["cache-control"]


def test_browser_login_failures_render_html_but_api_behavior_remains_available():
    source = inspect.getsource(login)
    assert source.count("_prefers_html(request)") >= 2
    assert "status_code=401" in source
    assert "status_code=429" in source
    assert "raise HTTPException(status_code=401" in source
    assert "raise _rate_limit_error" in source
    assert inspect.isfunction(_prefers_html)

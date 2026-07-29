from __future__ import annotations

import pytest

from app.config import settings
from app.security.client_address import (
    TrustedProxyClientAddressMiddleware,
    resolve_client_address,
    trusted_proxy_networks,
)


def scope_for(
    peer: str,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    scheme: str = "http",
):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": scheme,
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": (peer, 54321),
        "server": ("app", 8000),
    }


def configure(monkeypatch, cidrs="10.0.0.0/8,fd00::/8", max_hops=5):
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", cidrs)
    monkeypatch.setattr(settings, "trusted_proxy_max_hops", max_hops)
    monkeypatch.setattr(settings, "trust_forwarded_proto", True)


def test_untrusted_peer_cannot_spoof_forwarded_client_or_proto(monkeypatch):
    configure(monkeypatch)
    scope = scope_for(
        "198.51.100.20",
        headers=[
            (b"x-forwarded-for", b"203.0.113.99"),
            (b"x-forwarded-proto", b"https"),
        ],
    )
    result = resolve_client_address(scope)
    assert result.client_ip == "198.51.100.20"
    assert result.trusted_proxy_applied is False
    assert result.forwarded_headers_ignored is True
    assert result.error is None


def test_trusted_proxy_resolves_nearest_untrusted_address(monkeypatch):
    configure(monkeypatch)
    scope = scope_for(
        "10.0.0.5",
        headers=[
            (b"x-forwarded-for", b"203.0.113.7, 10.0.0.4"),
            (b"x-forwarded-proto", b"https"),
        ],
    )
    result = resolve_client_address(scope)
    assert result.direct_peer == "10.0.0.5"
    assert result.client_ip == "203.0.113.7"
    assert result.trusted_proxy_applied is True
    assert result.forwarded_headers_ignored is False


def test_attacker_supplied_leftmost_value_does_not_cross_untrusted_boundary(monkeypatch):
    configure(monkeypatch)
    scope = scope_for(
        "10.0.0.5",
        headers=[
            (b"x-forwarded-for", b"192.0.2.111, 198.51.100.8"),
        ],
    )
    result = resolve_client_address(scope)
    assert result.client_ip == "198.51.100.8"
    assert result.client_ip != "192.0.2.111"


def test_x_real_ip_and_ipv6_are_supported_only_for_trusted_peer(monkeypatch):
    configure(monkeypatch)
    ipv4 = resolve_client_address(
        scope_for("10.1.2.3", headers=[(b"x-real-ip", b"203.0.113.44")])
    )
    assert ipv4.client_ip == "203.0.113.44"
    assert ipv4.trusted_proxy_applied is True

    ipv6 = resolve_client_address(
        scope_for(
            "fd00::10",
            headers=[(b"x-forwarded-for", b"[2001:db8::1234]:443")],
        )
    )
    assert ipv6.client_ip == "2001:db8::1234"
    assert ipv6.trusted_proxy_applied is True


def test_malformed_or_excessive_trusted_chain_fails_closed(monkeypatch):
    configure(monkeypatch, max_hops=2)
    malformed = resolve_client_address(
        scope_for("10.0.0.5", headers=[(b"x-forwarded-for", b"not-an-ip")])
    )
    assert malformed.client_ip == "10.0.0.5"
    assert malformed.forwarded_headers_ignored is True
    assert malformed.error == "invalid_forwarded_address"

    excessive = resolve_client_address(
        scope_for(
            "10.0.0.5",
            headers=[(b"x-forwarded-for", b"192.0.2.1,192.0.2.2,192.0.2.3")],
        )
    )
    assert excessive.client_ip == "10.0.0.5"
    assert excessive.error == "forwarded_chain_too_long"


def test_invalid_proxy_cidr_is_not_trusted(monkeypatch):
    configure(monkeypatch, cidrs="10.0.0.0/8,not-a-network")
    with pytest.raises(RuntimeError):
        trusted_proxy_networks()
    result = resolve_client_address(
        scope_for("10.0.0.5", headers=[(b"x-forwarded-for", b"203.0.113.7")])
    )
    assert result.client_ip == "10.0.0.5"
    assert result.trusted_proxy_applied is False
    assert result.forwarded_headers_ignored is True
    assert result.error == "invalid_trusted_proxy_config"


@pytest.mark.asyncio
async def test_middleware_updates_scope_before_downstream_components(monkeypatch):
    configure(monkeypatch)
    captured = {}
    sent = []

    async def downstream(scope, receive, send):
        captured["client"] = scope["client"]
        captured["scheme"] = scope["scheme"]
        captured["state"] = dict(scope["state"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = TrustedProxyClientAddressMiddleware(downstream)
    await middleware(
        scope_for(
            "10.0.0.5",
            headers=[
                (b"x-forwarded-for", b"203.0.113.7"),
                (b"x-forwarded-proto", b"https"),
            ],
        ),
        receive,
        send,
    )
    assert captured["client"] == ("203.0.113.7", 54321)
    assert captured["scheme"] == "https"
    assert captured["state"]["direct_peer_ip"] == "10.0.0.5"
    assert captured["state"]["client_ip"] == "203.0.113.7"
    assert captured["state"]["trusted_proxy_applied"] is True
    assert sent[-1]["body"] == b"ok"


@pytest.mark.asyncio
async def test_invalid_forwarded_proto_is_ignored_and_audited(monkeypatch):
    configure(monkeypatch)
    captured = {}
    events = []

    async def fake_record(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "app.security.client_address.record_security_event_best_effort",
        fake_record,
    )

    async def downstream(scope, receive, send):
        captured["scheme"] = scope["scheme"]
        captured["error"] = scope["state"]["forwarded_header_error"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    await TrustedProxyClientAddressMiddleware(downstream)(
        scope_for(
            "10.0.0.5",
            headers=[
                (b"x-forwarded-for", b"203.0.113.7"),
                (b"x-forwarded-proto", b"javascript"),
            ],
        ),
        receive,
        send,
    )
    assert captured["scheme"] == "http"
    assert captured["error"] == "invalid_forwarded_proto"
    assert events[0]["action"] == "security.forwarded_header_rejected"
    assert events[0]["details"]["reason"] == "invalid_forwarded_proto"

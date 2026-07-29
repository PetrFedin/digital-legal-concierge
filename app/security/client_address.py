from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Iterable

from app.config import settings
from app.security.security_events import record_security_event_best_effort


@dataclass(frozen=True)
class ClientAddressResolution:
    direct_peer: str | None
    client_ip: str | None
    trusted_proxy_applied: bool
    forwarded_headers_present: bool
    forwarded_headers_ignored: bool
    error: str | None = None


def _parse_networks(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    result: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in str(raw or "").split(","):
        value = item.strip()
        if not value:
            continue
        try:
            result.append(ipaddress.ip_network(value, strict=False))
        except ValueError as error:
            raise RuntimeError(
                f"Некорректная сеть TRUSTED_PROXY_CIDRS: {value}"
            ) from error
    return tuple(result)


def trusted_proxy_networks() -> tuple[
    ipaddress.IPv4Network | ipaddress.IPv6Network, ...
]:
    return _parse_networks(settings.trusted_proxy_cidrs)


def _strip_ip_token(value: str) -> str:
    token = str(value or "").strip().strip('"')
    if not token:
        raise ValueError("empty address")
    if token.lower() == "unknown":
        raise ValueError("unknown address")
    if token.startswith("["):
        closing = token.find("]")
        if closing < 0:
            raise ValueError("invalid bracketed IPv6")
        return token[1:closing]
    if token.count(":") == 1 and "." in token:
        host, port = token.rsplit(":", 1)
        if port.isdigit():
            return host
    return token


def parse_ip(value: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(_strip_ip_token(str(value or "")))


def _is_trusted(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    networks: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    return any(
        address.version == network.version and address in network
        for network in networks
    )


def _header_values(scope: dict, name: bytes) -> list[str]:
    values: list[str] = []
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            values.append(value.decode("latin-1"))
    return values


def _forwarded_chain(scope: dict) -> list[str]:
    values = _header_values(scope, b"x-forwarded-for")
    if values:
        combined = ",".join(values)
        return [part.strip() for part in combined.split(",") if part.strip()]
    real_ip = _header_values(scope, b"x-real-ip")
    return [real_ip[-1].strip()] if real_ip and real_ip[-1].strip() else []


def resolve_client_address(scope: dict) -> ClientAddressResolution:
    client = scope.get("client")
    direct_value = str(client[0]) if client and client[0] else None
    chain = _forwarded_chain(scope)
    forwarded_present = bool(
        chain
        or _header_values(scope, b"x-forwarded-proto")
        or _header_values(scope, b"forwarded")
    )
    if not direct_value:
        return ClientAddressResolution(
            direct_peer=None,
            client_ip=None,
            trusted_proxy_applied=False,
            forwarded_headers_present=forwarded_present,
            forwarded_headers_ignored=forwarded_present,
            error="missing_direct_peer",
        )
    try:
        direct = parse_ip(direct_value)
    except ValueError:
        return ClientAddressResolution(
            direct_peer=direct_value,
            client_ip=direct_value,
            trusted_proxy_applied=False,
            forwarded_headers_present=forwarded_present,
            forwarded_headers_ignored=forwarded_present,
            error="invalid_direct_peer",
        )

    try:
        networks = trusted_proxy_networks()
    except RuntimeError:
        return ClientAddressResolution(
            direct_peer=str(direct),
            client_ip=str(direct),
            trusted_proxy_applied=False,
            forwarded_headers_present=forwarded_present,
            forwarded_headers_ignored=forwarded_present,
            error="invalid_trusted_proxy_config",
        )
    if not networks or not _is_trusted(direct, networks):
        return ClientAddressResolution(
            direct_peer=str(direct),
            client_ip=str(direct),
            trusted_proxy_applied=False,
            forwarded_headers_present=forwarded_present,
            forwarded_headers_ignored=forwarded_present,
        )
    if not chain:
        return ClientAddressResolution(
            direct_peer=str(direct),
            client_ip=str(direct),
            trusted_proxy_applied=False,
            forwarded_headers_present=forwarded_present,
            forwarded_headers_ignored=False,
        )

    max_hops = max(1, min(int(settings.trusted_proxy_max_hops), 20))
    if len(chain) > max_hops:
        return ClientAddressResolution(
            direct_peer=str(direct),
            client_ip=str(direct),
            trusted_proxy_applied=False,
            forwarded_headers_present=True,
            forwarded_headers_ignored=True,
            error="forwarded_chain_too_long",
        )
    try:
        parsed_chain = [parse_ip(value) for value in chain]
    except ValueError:
        return ClientAddressResolution(
            direct_peer=str(direct),
            client_ip=str(direct),
            trusted_proxy_applied=False,
            forwarded_headers_present=True,
            forwarded_headers_ignored=True,
            error="invalid_forwarded_address",
        )

    full_chain = [*parsed_chain, direct]
    selected = parsed_chain[0]
    for candidate in reversed(full_chain):
        if not _is_trusted(candidate, networks):
            selected = candidate
            break
    return ClientAddressResolution(
        direct_peer=str(direct),
        client_ip=str(selected),
        trusted_proxy_applied=True,
        forwarded_headers_present=True,
        forwarded_headers_ignored=False,
    )


def _trusted_forwarded_proto(
    scope: dict,
    resolution: ClientAddressResolution,
) -> tuple[str | None, str | None]:
    if not resolution.trusted_proxy_applied or not settings.trust_forwarded_proto:
        return None, None
    values = _header_values(scope, b"x-forwarded-proto")
    if not values:
        return None, None
    parts = [part.strip().lower() for part in ",".join(values).split(",")]
    if not parts or any(part not in {"http", "https"} for part in parts):
        return None, "invalid_forwarded_proto"
    return parts[0], None


class TrustedProxyClientAddressMiddleware:
    """Trust forwarding headers only for direct peers in an explicit CIDR allowlist."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        resolution = resolve_client_address(scope)
        trusted_proto, proto_error = _trusted_forwarded_proto(scope, resolution)
        effective_error = resolution.error or proto_error
        state = scope.setdefault("state", {})
        state["direct_peer_ip"] = resolution.direct_peer
        state["client_ip"] = resolution.client_ip
        state["trusted_proxy_applied"] = resolution.trusted_proxy_applied
        state["forwarded_headers_present"] = resolution.forwarded_headers_present
        state["forwarded_headers_ignored"] = resolution.forwarded_headers_ignored
        state["forwarded_header_error"] = effective_error

        if resolution.client_ip and scope.get("client"):
            scope["client"] = (resolution.client_ip, scope["client"][1])
        if trusted_proto:
            scope["scheme"] = trusted_proto

        await self.app(scope, receive, send)

        if effective_error and resolution.forwarded_headers_present:
            await record_security_event_best_effort(
                action="security.forwarded_header_rejected",
                severity="warning",
                source="trusted_proxy_middleware",
                client_address=resolution.direct_peer,
                resource_type="http_request",
                details={
                    "reason": effective_error,
                    "trusted_proxy_applied": resolution.trusted_proxy_applied,
                },
                comment="Forwarded-заголовок не прошёл строгую проверку proxy-chain",
                sample_seconds=60,
            )


def get_client_address(request) -> str | None:
    value = getattr(request.state, "client_ip", None)
    if value:
        return str(value)
    return request.client.host if request.client else None

"""Resolve once, connect to that public IP, retain the original TLS/Host name."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx


class NetworkPolicyError(ValueError):
    """A URL or resolved address is outside the read-only network policy."""


def parse_url(value: str, *, allow_http: bool = True) -> httpx.URL:
    try:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4096
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or "\\" in value
        ):
            raise ValueError
        parts = urlsplit(value)
        if (
            parts.scheme not in ({"http", "https"} if allow_http else {"https"})
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or "%" in parts.hostname
            or parts.port == 0
        ):
            raise ValueError
        url = httpx.URL(value).copy_with(fragment=None)
        if not url.host or url.host.endswith("."):
            raise ValueError
        return url
    except (ValueError, TypeError, httpx.InvalidURL):
        raise NetworkPolicyError("invalid_url") from None


def public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
        # IPv4 tunnelling/mapping can reach addresses beyond the visible v6 range.
        if isinstance(ip, ipaddress.IPv6Address) and (
            ip.ipv4_mapped is not None
            or ip.sixtofour is not None
            or ip.teredo is not None
            or ip in ipaddress.ip_network("64:ff9b::/96")
            or ip in ipaddress.ip_network("64:ff9b:1::/48")
        ):
            return False
        return ip.is_global and not ip.is_multicast and not ip.is_reserved
    except ValueError:
        return False


def explicit_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def pin_url(value: str, *, allow_local: bool = False) -> tuple[httpx.URL, dict, dict]:
    """Reject mixed public/private DNS responses; no DNS re-resolution at connect time."""
    url = parse_url(value)
    local = allow_local and explicit_loopback(url.host)
    if url.scheme == "http" and allow_local and not local:
        raise NetworkPolicyError("insecure_endpoint")
    try:
        addresses = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                url.host,
                url.port or (443 if url.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            ),
            timeout=5,
        )
    except (OSError, TimeoutError):
        raise NetworkPolicyError("dns_failed") from None
    ips = {item[4][0] for item in addresses}
    if not ips or any(not (explicit_loopback(ip) if local else public_ip(ip)) for ip in ips):
        raise NetworkPolicyError("blocked_address")
    selected = sorted(ips, key=lambda value: (":" in value, value))[0]
    target = url.copy_with(host=selected)
    host = f"[{url.host}]" if ":" in url.host else url.host
    default_port = 443 if url.scheme == "https" else 80
    if url.port is not None and url.port != default_port:
        host += f":{url.port}"
    return target, {"Host": host}, {"sni_hostname": url.host}


def client() -> httpx.AsyncClient:
    # Each request gets a fresh cookie jar; no environment proxy or user auth.
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(20, connect=10),
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        headers={"Accept-Encoding": "identity", "User-Agent": "ai-neko/0.2 read-only"},
    )


async def bounded_body(response: httpx.Response, limit: int = 2_000_000) -> bytes:
    if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
        raise NetworkPolicyError("unsupported_encoding")
    length = response.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        raise NetworkPolicyError("response_too_large")
    result = bytearray()
    async for chunk in response.aiter_raw():
        result.extend(chunk)
        if len(result) > limit:
            raise NetworkPolicyError("response_too_large")
    return bytes(result)

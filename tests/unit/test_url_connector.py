"""UrlConnector: подключение к проверенному адресу, редиректы, лимит размера."""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from app.domain.exceptions import InvalidSourceUrlError, SourceFetchError
from app.domain.interfaces.source_connector import SourceKind, SourceRef
from app.domain.source_url import IpAddress
from app.infrastructure.source_connectors.url_connector import UrlConnector, resolve_host

PUBLIC_IP = ipaddress.ip_address("93.184.216.34")
PRIVATE_IP = ipaddress.ip_address("10.0.0.5")


def _ref(url: str) -> SourceRef:
    return SourceRef(
        id=uuid.uuid4(),
        name="spec",
        type=SourceKind.URL,
        storage_key=None,
        url=url,
        uploaded_at=datetime.now(UTC),
    )


def _resolver(table: dict[str, list[IpAddress]]):
    async def resolve(host: str, port: int) -> list[IpAddress]:
        return table.get(host, [])

    return resolve


def _connector(handler, table, **kwargs) -> UrlConnector:
    return UrlConnector(resolver=_resolver(table), transport=httpx.MockTransport(handler), **kwargs)


async def test_connects_to_resolved_ip_and_keeps_host_and_sni() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="hello", headers={"content-type": "text/plain"})

    connector = _connector(handler, {"example.com": [PUBLIC_IP]})

    text = await connector.fetch(_ref("https://example.com/spec?v=1"))

    assert text == "hello"
    page = seen[-1]
    assert page.url.host == str(PUBLIC_IP)
    assert page.url.path == "/spec"
    assert page.url.query == b"v=1"
    assert page.headers["host"] == "example.com"
    assert page.extensions["sni_hostname"] == "example.com"
    assert seen[0].url.path == "/robots.txt"


async def test_rejects_host_resolving_to_private_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("запрос не должен уходить в сеть")

    connector = _connector(handler, {"internal.example.com": [PUBLIC_IP, PRIVATE_IP]})

    with pytest.raises(InvalidSourceUrlError):
        await connector.fetch(_ref("http://internal.example.com/"))


async def test_unresolvable_host_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("запрос не должен уходить в сеть")

    connector = _connector(handler, {})

    with pytest.raises(SourceFetchError):
        await connector.fetch(_ref("http://nowhere.example.com/"))


async def test_redirect_to_private_host_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})

    connector = _connector(handler, {"example.com": [PUBLIC_IP]})

    with pytest.raises(InvalidSourceUrlError):
        await connector.fetch(_ref("http://example.com/"))


async def test_redirect_is_followed_and_rechecked() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.headers["host"])
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.headers["host"] == "example.com":
            return httpx.Response(301, headers={"location": "https://cdn.example.net/doc"})
        return httpx.Response(
            200,
            text="<html><body><p>Текст</p></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    connector = _connector(
        handler,
        {"example.com": [PUBLIC_IP], "cdn.example.net": [ipaddress.ip_address("8.8.8.8")]},
    )

    text = await connector.fetch(_ref("http://example.com/"))

    assert text == "Текст"
    assert hosts == ["example.com", "example.com", "cdn.example.net"]


async def test_too_many_redirects_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://example.com/loop"})

    connector = _connector(handler, {"example.com": [PUBLIC_IP]}, max_redirects=2)

    with pytest.raises(SourceFetchError, match="редиректов"):
        await connector.fetch(_ref("http://example.com/"))


async def test_oversized_body_is_rejected_not_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"x" * 2048, headers={"content-type": "text/plain"})

    connector = _connector(handler, {"example.com": [PUBLIC_IP]}, max_bytes=1024)

    with pytest.raises(SourceFetchError, match="лимита"):
        await connector.fetch(_ref("http://example.com/big"))


async def test_declared_oversized_content_length_is_rejected_early() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, headers={"content-length": "999999", "content-type": "text/plain"}
        )

    connector = _connector(handler, {"example.com": [PUBLIC_IP]}, max_bytes=1024)

    with pytest.raises(SourceFetchError, match="лимита"):
        await connector.fetch(_ref("http://example.com/big"))


async def test_http_error_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(503)

    connector = _connector(handler, {"example.com": [PUBLIC_IP]})

    with pytest.raises(httpx.HTTPStatusError):
        await connector.fetch(_ref("http://example.com/"))


async def test_robots_failure_does_not_block_fetch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("robots down")
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    connector = _connector(handler, {"example.com": [PUBLIC_IP]})

    assert await connector.fetch(_ref("http://example.com/")) == "ok"


async def test_system_resolver_returns_addresses_for_localhost() -> None:
    addresses = await resolve_host("localhost", 80)

    assert addresses
    assert all(address.is_loopback for address in addresses)


async def test_system_resolver_reports_unknown_host() -> None:
    with pytest.raises(SourceFetchError):
        await resolve_host("host.invalid", 80)

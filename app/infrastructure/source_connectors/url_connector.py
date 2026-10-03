"""
UrlConnector — скачивает текст по HTTP(S)-ссылке.

Особенности:
  - httpx.AsyncClient с явными таймаутами (connect=5s, read=20s).
  - SSRF-защита: разрешены только http/https, host должен резолвиться
    исключительно в публичные IP; проверка повторяется на каждом редиректе.
  - Редиректы обрабатываются вручную (не более 5).
  - Ответ читается потоково; при превышении 5 МБ — ошибка (без OOM).
  - robots.txt читается в отдельном потоке через asyncio.to_thread()
    с таймаутом 3 с, чтобы не блокировать event loop. (R-3 fix)
  - Content-type: если text/html — парсит через BeautifulSoup,
    убирает теги и возвращает чистый текст. Иначе — декодирует как UTF-8.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import urllib.parse
import urllib.robotparser

import httpx

try:
    from bs4 import BeautifulSoup as _BeautifulSoup
except ImportError:
    _BeautifulSoup = None  # type: ignore[assignment,misc]

from app.domain.interfaces.source_connector import SourceKind, SourceMetadata, SourceRef

logger = logging.getLogger("syncscribe.connectors.url")

_MAX_BYTES = 5 * 1024 * 1024  # 5 МБ
_MAX_REDIRECTS = 5
_USER_AGENT = "SyncScribeBot/1.0 (+https://syncscribe.app/bot)"
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)
_ROBOTS_TIMEOUT = 3.0
_REDIRECT_CODES = {301, 302, 303, 307, 308}


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def _validate_public_http_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Поддерживаются только http/https URL")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL должен содержать публичный host без credentials")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parsed.hostname, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise ValueError("Не удалось разрешить host источника") from exc
    resolved = {info[4][0] for info in infos}
    if not resolved or any(not _is_public_ip(a) for a in resolved):
        raise ValueError("URL источника указывает на непубличный адрес")
    return urllib.parse.urlunsplit(parsed)


class UrlConnector:
    def supports(self, source_type: SourceKind) -> bool:
        return source_type == SourceKind.URL

    async def fetch(self, source: SourceRef) -> str:
        if not source.url:
            raise ValueError(f"url отсутствует для источника {source.id}")

        current_url = await _validate_public_http_url(source.url)
        await self._check_robots_async(current_url)

        async with httpx.AsyncClient(
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
            trust_env=False,
        ) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                async with client.stream("GET", current_url) as response:
                    if response.status_code in _REDIRECT_CODES:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("Редирект без заголовка Location")
                        current_url = await _validate_public_http_url(
                            urllib.parse.urljoin(current_url, location)
                        )
                        continue
                    response.raise_for_status()
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > _MAX_BYTES:
                        raise ValueError("Ответ источника превышает лимит 5 МБ")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_BYTES:
                            raise ValueError("Ответ источника превышает лимит 5 МБ")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    content_type = response.headers.get("content-type", "")
                    if "html" in content_type:
                        return self._strip_html(raw)
                    return raw.decode("utf-8", errors="replace")
        raise ValueError("Слишком много перенаправлений источника")

    async def get_metadata(self, source: SourceRef) -> SourceMetadata:
        return SourceMetadata(
            name=source.name,
            type=source.type,
            uploaded_at=source.uploaded_at.isoformat(),
        )

    async def _check_robots_async(self, url: str) -> None:
        """Асинхронная проверка robots.txt через asyncio.to_thread() с таймаутом."""
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._read_robots_sync, url),
                timeout=_ROBOTS_TIMEOUT,
            )
        except TimeoutError:
            logger.debug("robots.txt check timed out, продолжаем", extra={"url": url})
        except Exception as exc:
            logger.debug("robots.txt check failed, продолжаем", extra={"url": url, "exc": str(exc)})

    @staticmethod
    def _read_robots_sync(url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        if not rp.can_fetch(_USER_AGENT, url):
            logger.warning(
                "robots.txt запрещает crawl (MVP: всё равно скачиваем)",
                extra={"url": url},
            )

    @staticmethod
    def _strip_html(raw: bytes) -> str:
        if _BeautifulSoup is not None:
            soup = _BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "noscript", "head"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)
        return raw.decode("utf-8", errors="replace")

"""
UrlConnector — скачивает текст по HTTP(S)-ссылке.

Особенности:
  - httpx.AsyncClient с явными таймаутами (connect=5s, read=20s).
  - robots.txt читается в отдельном потоке через asyncio.to_thread()
    с таймаутом 3 с, чтобы не блокировать event loop. (R-3 fix)
  - Редиректы: httpx следует автоматически (follow_redirects=True).
  - Content-type: если text/html — парсит через BeautifulSoup,
    убирает теги и возвращает чистый текст. Иначе — декодирует как UTF-8.
  - Лимит: 5 МБ на ответ.
"""
from __future__ import annotations

import asyncio
import logging
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
_USER_AGENT = "SyncScribeBot/1.0 (+https://syncscribe.app/bot)"
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)
_ROBOTS_TIMEOUT = 3.0  # секунды ожидания robots.txt (блокирующий вызов в потоке)


class UrlConnector:
    def supports(self, source_type: SourceKind) -> bool:
        return source_type == SourceKind.URL

    async def fetch(self, source: SourceRef) -> str:
        if not source.url:
            raise ValueError(f"url отсутствует для источника {source.id}")

        url = source.url
        await self._check_robots_async(url)  # non-blocking (R-3 fix)

        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

            raw = response.content
            if len(raw) > _MAX_BYTES:
                logger.warning(
                    "Ответ превышает лимит 5 МБ, обрезаем",
                    extra={"url": url, "size": len(raw)},
                )
                raw = raw[:_MAX_BYTES]

            content_type = response.headers.get("content-type", "")
            if "html" in content_type:
                return self._strip_html(raw)
            return raw.decode("utf-8", errors="replace")

    async def get_metadata(self, source: SourceRef) -> SourceMetadata:
        return SourceMetadata(
            name=source.name,
            type=source.type,
            uploaded_at=source.uploaded_at.isoformat(),
        )

    # ------------------------------------------------------------------
    # R-3 fix: robots.txt читается в потоке, не блокирует event loop
    # ------------------------------------------------------------------

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
        """Синхронный вызов — выполняется в отдельном потоке."""
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

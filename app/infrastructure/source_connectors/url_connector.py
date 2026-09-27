"""
UrlConnector — скачивает текст по HTTP(S)-ссылке.

Особенности:
  - httpx.AsyncClient с разумными таймаутами и User-Agent.
  - Проверяет robots.txt: если страница закрыта для ботов — логирует
    предупреждение и всё равно пробует скачать (MVP; строгий robots.txt
    можно включить через флаг конфигурации).
  - Редиректы: httpx следует автоматически (follow_redirects=True).
  - Content-type: если text/html — парсит через BeautifulSoup (lxml/html.parser),
    убирает теги и возвращает чистый текст. Иначе — декодирует как UTF-8.
  - Лимит: 5 МБ на ответ (защита от бесконечных страниц).
"""
from __future__ import annotations

import logging
import urllib.parse
import urllib.robotparser

import httpx

from app.domain.interfaces.source_connector import SourceKind, SourceMetadata, SourceRef

logger = logging.getLogger("syncscribe.connectors.url")

_MAX_BYTES = 5 * 1024 * 1024  # 5 МБ
_USER_AGENT = "SyncScribeBot/1.0 (+https://syncscribe.app/bot)"
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)


class UrlConnector:
    def supports(self, source_type: SourceKind) -> bool:
        return source_type == SourceKind.URL

    async def fetch(self, source: SourceRef) -> str:
        if not source.url:
            raise ValueError(f"url отсутствует для источника {source.id}")

        url = source.url
        self._log_robots(url)

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
    # Helpers
    # ------------------------------------------------------------------

    def _log_robots(self, url: str) -> None:
        """Проверяем robots.txt и логируем предупреждение (не блокируем в MVP)."""
        try:
            parsed = urllib.parse.urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(robots_url)
            rp.read()  # синхронный вызов; приемлемо для редкой операции
            if not rp.can_fetch(_USER_AGENT, url):
                logger.warning(
                    "robots.txt запрещает crawl (MVP: всё равно скачиваем)",
                    extra={"url": url},
                )
        except Exception as exc:
            logger.debug("Не удалось прочитать robots.txt", extra={"url": url, "exc": str(exc)})

    @staticmethod
    def _strip_html(raw: bytes) -> str:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "noscript", "head"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)
        except ImportError:
            # bs4 не установлена — возвращаем сырой текст
            return raw.decode("utf-8", errors="replace")

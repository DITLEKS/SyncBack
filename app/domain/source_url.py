"""Адрес внешнего источника и правила, каким адресам доверять.

Источник по ссылке скачивает воркер с сервера, поэтому ссылка — это запрос
от имени сервера во внешнюю сеть. Домен задаёт правила: только http(s),
без учётных данных в URL, никаких локальных и приватных адресов. Разрешение
DNS-имён и само подключение остаются в инфраструктуре, но итоговые адреса
проверяются той же функцией is_public_address.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

from app.domain.exceptions import InvalidSourceUrlError

ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
_LOCAL_HOSTS = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})
_MAX_URL_LENGTH = 2048

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def is_public_address(address: IpAddress) -> bool:
    """Адрес доступен из публичного интернета, а не из нашей сети.

    Отсекает loopback, приватные диапазоны, link-local (в том числе metadata
    169.254.169.254), multicast, зарезервированные и неопределённые адреса.
    IPv4-адреса, завёрнутые в IPv6 (::ffff:10.0.0.1), проверяются как IPv4.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_multicast or address.is_reserved or address.is_unspecified:
        return False
    return address.is_global


def _parse_ip_literal(host: str) -> IpAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class SourceUrl:
    """Проверенный адрес источника: http(s), с хостом, без учётных данных."""

    value: str
    scheme: str
    host: str
    port: int

    @classmethod
    def parse(cls, raw: str) -> SourceUrl:
        """Разбирает и проверяет адрес; при нарушении правил — InvalidSourceUrlError."""
        value = raw.strip()
        if not value or len(value) > _MAX_URL_LENGTH:
            raise InvalidSourceUrlError("Ссылка пустая или слишком длинная")

        try:
            parts = urlsplit(value)
            port = parts.port
        except ValueError as exc:
            raise InvalidSourceUrlError("Ссылка имеет некорректный формат") from exc

        scheme = parts.scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            raise InvalidSourceUrlError("Поддерживаются только ссылки http и https")
        if parts.username is not None or parts.password is not None:
            raise InvalidSourceUrlError("Ссылка не должна содержать учётные данные")

        host = cls._checked_host(parts)
        return cls(value=value, scheme=scheme, host=host, port=port or _DEFAULT_PORTS[scheme])

    @staticmethod
    def _checked_host(parts: SplitResult) -> str:
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            raise InvalidSourceUrlError("В ссылке не указан хост")

        literal = _parse_ip_literal(host)
        if literal is not None:
            if not is_public_address(literal):
                raise InvalidSourceUrlError("Ссылки на локальные и приватные адреса запрещены")
            return host

        if host in _LOCAL_HOSTS or host.endswith(_LOCAL_HOST_SUFFIXES):
            raise InvalidSourceUrlError("Ссылки на локальные и приватные адреса запрещены")
        return host

    @property
    def ip_literal(self) -> IpAddress | None:
        """IP-адрес, если хост задан числом, иначе None (нужно разрешить имя)."""
        return _parse_ip_literal(self.host)

    @property
    def uses_default_port(self) -> bool:
        return self.port == _DEFAULT_PORTS[self.scheme]

    @property
    def host_header(self) -> str:
        """Значение заголовка Host: порт указывается, только если он нестандартный."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.uses_default_port else f"{host}:{self.port}"
